import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from refine_workflow import load_source, reserve
from refinement import decide, run_refinement_loop
from revise_review import repair_tasks, validate_revision, revision_request
from run_workflow import create_handoff, read_json
from review_tutorial import write_json


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.saved_fixture("revise")
        self.root = self.fixture.run
        self.source = self.fixture.source
        self.original = read_json(self.source / "original-review.json")
        self.bundle = read_json(self.source / "evidence-catalog.json")
        write_json(self.source / "review.json", self.original)
        self.state = {"mode": "live", "status": "running", "started_at": "2026-01-01T00:00:00+00:00",
            "sample_id": "example", "model_stage_budget": 6, "model_stage_attempts": 2,
            "platform_metrics_provided": False, "stages": []}
        self.paths = {"analysis_dir": str(self.source), "verification_dir": str(self.source)}
        self.calls = []

    def verification_result(self, original, status):
        result = read_json(self.source / "verification.json")
        result["summary_check"]["status"] = status
        result["checks"][0]["status"] = status
        result["revised_review"] = copy.deepcopy(original)
        return result

    def set_initial_status(self, status):
        write_json(self.source / "verification.json", self.verification_result(self.original, status))

    def fake_runner(self, verification_status="supported", no_change=False, failure=None):
        def run(root, stage, script, args):
            self.calls.append(script)
            if failure == script:
                raise ValueError("network failed; no retry")
            output = Path(args[args.index("--output-dir") + 1])
            destination = output / "run-fixture"
            destination.mkdir(parents=True)
            if script == "revise_review.py":
                source = Path(args[args.index("--verification-dir") + 1])
                original = read_json(source / "original-review.json")
                revised = copy.deepcopy(original)
                if not no_change:
                    revised["summary"] += "（修正）"
                    revised["findings"][0]["interpretation"] += "，尚未证实"
                write_json(destination / "review.json", revised)
                write_json(destination / "evidence-catalog.json", self.bundle)
                write_json(destination / "run.json", {"model": "fake"})
            else:
                source = Path(args[args.index("--review-dir") + 1])
                original = read_json(source / "review.json")
                result = self.verification_result(original, verification_status)
                for name, value in (("original-review.json", original), ("evidence-catalog.json", self.bundle),
                                    ("verification.json", result), ("revised-review.json", original)):
                    write_json(destination / name, value)
                meta = {"status": "completed_structure_valid", "model": "fake"}
                for name, key in (("original-review.json", "review_sha256"), ("evidence-catalog.json", "evidence_sha256")):
                    meta[key] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
                write_json(destination / "run.json", meta)
            write_json(output / "latest.json", {"run_dir": str(destination)})
        return run

    def run_loop(self, status="supported", max_rounds=1, **kwargs):
        with patch("refinement.run_script", side_effect=self.fake_runner(status, **kwargs)):
            return run_refinement_loop(self.root, self.state, self.paths, "fake", max_rounds)

    def test_insufficient_evidence_dominates_mixed_feedback_without_api(self):
        result = self.verification_result(self.original, "revise")
        result["checks"][0]["status"] = "insufficient_evidence"
        write_json(self.source / "verification.json", result)
        self.run_loop()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state["refinement"]["status"], "awaiting_evidence")
        self.assertTrue((self.root / "repair-tasks.json").exists())

    def test_supported_never_calls_repair(self):
        self.set_initial_status("supported")
        self.run_loop()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state["refinement"]["status"], "supported")

    def test_one_remaining_call_cannot_start_half_round(self):
        self.state["model_stage_budget"] = 3
        self.run_loop()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state["model_stage_attempts"], 2)
        self.assertEqual(self.state["refinement"]["status"], "budget_limit")

    def test_repair_then_independent_verification_preserves_original(self):
        before = (self.source / "original-review.json").read_bytes()
        self.run_loop()
        self.assertEqual(self.calls, ["revise_review.py", "verify_tutorial.py"])
        self.assertEqual(self.state["model_stage_attempts"], 4)
        self.assertEqual(self.state["refinement"]["status"], "supported")
        self.assertEqual((self.source / "original-review.json").read_bytes(), before)
        self.assertNotEqual(self.paths["verification_dir"], str(self.source))
        create_handoff(self.root, self.state, self.paths)
        self.assertEqual(self.state["status"], "awaiting_human_review")
        self.assertEqual(read_json(self.root / "human-review.json")["status"], "pending")

    def test_two_round_cap_stops_with_unresolved_feedback(self):
        self.run_loop(status="revise", max_rounds=2)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.state["model_stage_attempts"], 6)
        self.assertEqual(self.state["refinement"]["status"], "round_limit")

    def test_no_change_stops_before_verifier(self):
        self.run_loop(no_change=True)
        self.assertEqual(self.calls, ["revise_review.py"])
        self.assertEqual(self.state["refinement"]["status"], "no_change")
        self.assertEqual(self.paths["verification_dir"], str(self.source))

    def test_network_failure_has_no_automatic_retry_or_handoff(self):
        with self.assertRaisesRegex(ValueError, "network"):
            self.run_loop(failure="revise_review.py")
        self.assertEqual(self.calls, ["revise_review.py"])
        self.assertEqual(self.state["model_stage_attempts"], 3)
        self.assertEqual(self.state["refinement"]["status"], "failed")
        self.assertEqual(self.state["stages"][-1]["status"], "skipped")
        self.assertFalse((self.root / "human-review.json").exists())

    def test_reverification_failure_keeps_unverified_revision_separate(self):
        with self.assertRaisesRegex(ValueError, "network"):
            self.run_loop(failure="verify_tutorial.py")
        self.assertEqual(self.calls, ["revise_review.py", "verify_tutorial.py"])
        self.assertEqual(self.paths["verification_dir"], str(self.source))
        self.assertTrue((self.root / "refinement/round-1/analysis/run-fixture/review.json").exists())
        self.assertEqual(self.state["status"], "failed")

    def test_supported_content_and_ids_cannot_be_changed(self):
        tasks = repair_tasks(self.verification_result(self.original, "supported"))
        revised = copy.deepcopy(self.original)
        revised["summary"] = "unsupported rewrite"
        with self.assertRaisesRegex(ValueError, "总结"):
            validate_revision(revised, self.original, self.bundle, tasks)
        revised = copy.deepcopy(self.original)
        revised["findings"][0]["id"] = "replacement"
        with self.assertRaisesRegex(ValueError, "ID"):
            validate_revision(revised, self.original, self.bundle, tasks)

    def test_request_contains_feedback_and_original_evidence(self):
        tasks = repair_tasks(self.verification_result(self.original, "revise"))
        payload = revision_request(self.original, self.bundle, [], "fake", tasks)
        sent = json.loads(payload["messages"][1]["content"][0]["text"])
        self.assertEqual(sent["repair_tasks"], tasks)
        self.assertEqual(sent["original_review"], self.original)
        self.assertEqual(sent["catalog"], self.bundle["catalog"])

    def test_continuation_claim_is_exclusive(self):
        reserve(self.root, self.root / "next")
        with self.assertRaisesRegex(ValueError, "重复"):
            reserve(self.root, self.root / "another")

    def test_revision_cli_retains_valid_and_invalid_raw_responses(self):
        from revise_review import main
        for index, content in enumerate((json.dumps(dict(self.original, summary="修正后的总结")), "[]")):
            with self.subTest(index=index):
                output = self.root / ("cli-%d" % index)
                response = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
                with patch("sys.argv", ["revise_review.py", "--verification-dir", str(self.source), "--output-dir", str(output)]), patch("revise_review.call_dashscope", return_value=response) as call, patch("builtins.print"):
                    code = main()
                call.assert_called_once()
                self.assertEqual(code, index)
                folder = next(output.glob("run-*"))
                self.assertEqual((folder / "model-output.txt").read_text(encoding="utf-8"), content)
                self.assertEqual((folder / "review.json").exists(), index == 0)
                self.assertEqual((output / "latest.json").exists(), index == 0)

    def test_old_goal_cannot_be_refined(self):
        # source loader checks a real workspace-shaped tree, with all IO isolated.
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            parent = project / "reports/workflows/run-test"
            parent.mkdir(parents=True)
            self.bundle["task"]["tutorial_goal"] = "旧目标"
            state = dict(self.state, status="awaiting_human_review", artifacts={"analysis_dir": str(parent / "analysis"), "verification_dir": str(parent / "verification")})
            write_json(parent / "workflow.json", state)
            samples = project / "data/samples"
            samples.mkdir(parents=True)
            write_json(samples / "example.json", {"tutorial_goal": "新目标", "goal_revision": 2})
            with patch("refine_workflow.PROJECT_ROOT", project), patch("refine_workflow.load_review_run", return_value=(self.original, self.bundle, [], {})), patch("refine_workflow.check_saved_verification", return_value=(self.original, self.bundle, {}, {})):
                with self.assertRaisesRegex(ValueError, "目标已经变化"):
                    load_source(parent)

    def test_cannot_chain_refinement_workflow(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            parent = project / "reports/workflows/run-test"
            parent.mkdir(parents=True)
            write_json(parent / "workflow.json", {"mode": "refine", "status": "awaiting_human_review"})
            with patch("refine_workflow.PROJECT_ROOT", project):
                with self.assertRaisesRegex(ValueError, "串联"):
                    load_source(parent)
