import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from check_materials import PROJECT_ROOT
from review_tutorial import write_json
from run_workflow import check_saved_verification, create_handoff, execute_stage, new_state, read_json, run_replay, run_verify_only, write_failure_report


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary_parent = PROJECT_ROOT / "reports"
        temporary_parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-test-", dir=str(temporary_parent))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.run = self.root / "workflow"
        self.run.mkdir()

    def saved_fixture(self, status="supported"):
        original = {"sample_id": "example", "summary": "初稿", "findings": [
            {"id": "finding-1", "observation": "观察", "interpretation": "推测", "suggestion": "建议",
             "validation_plan": "实际检查", "evidence_ids": ["text-1"], "confidence": "medium"}], "limitations": ["未运行"]}
        bundle = {"task": {"sample_id": "example", "tutorial_goal": "目标", "platform_metrics": None},
                  "coverage": {"selected_frames": 0, "available_frames": 0, "subtitle_count": 0, "image_count": 0},
                  "catalog": {"text-1": {"type": "text", "text": "材料"}}}
        result = {"sample_id": "example", "access_check": {"creator_side": "创作者界面", "viewer_side": "观众操作", "evidence_ids": ["text-1"]},
                  "summary_check": {"status": status, "reason": "理由", "evidence_ids": ["text-1"]},
                  "checks": [{"finding_id": "finding-1", "status": status, "reason": "理由", "evidence_ids": ["text-1"]}],
                  "revised_review": copy.deepcopy(original), "limitations": ["未实测"]}
        for name, data in (("original-review.json", original), ("evidence-catalog.json", bundle),
                           ("verification.json", result), ("revised-review.json", result["revised_review"])):
            write_json(self.source / name, data)
        meta = {"status": "completed_structure_valid", "model": "test-model"}
        for name, key in (("original-review.json", "review_sha256"), ("evidence-catalog.json", "evidence_sha256")):
            meta[key] = hashlib.sha256((self.source / name).read_bytes()).hexdigest()
        write_json(self.source / "run.json", meta)
        (self.source / "prompt.md").write_text("fixture prompt")
        (self.source / "model-output.txt").write_text("fixture answer")

    def test_failure_skips_dependents(self):
        state = new_state("live", 2, "example")
        def fail():
            raise ValueError("missing material")
        with self.assertRaises(ValueError):
            execute_stage(self.run, state, "materials", fail)
        self.assertEqual(state["status"], "failed")
        self.assertEqual([x["status"] for x in state["stages"]], ["failed", "skipped", "skipped", "skipped"])
        self.assertEqual(read_json(self.run / "workflow.json")["model_stage_attempts"], 0)

    def test_dependency_order_enforced(self):
        state = new_state("live", 2, "example")
        with self.assertRaisesRegex(ValueError, "依赖"):
            execute_stage(self.run, state, "verification", lambda: {})

    def test_failure_report_does_not_offer_approved_candidate(self):
        state = new_state("live", 2, "example")
        state.update(status="failed", stop_reason="模型返回空数组")
        write_failure_report(self.run, state)
        self.assertEqual(state["human_review"]["status"], "unavailable")
        self.assertTrue((self.run / "workflow.md").is_file())
        self.assertFalse((self.run / "human-review.json").exists())

    def test_budget_stops_second_model_stage_before_action(self):
        state = new_state("live", 1, "example")
        for name in ("materials", "video"):
            execute_stage(self.run, state, name, lambda: {})
        execute_stage(self.run, state, "analysis", lambda: {}, model_stage=True)
        called = []
        with self.assertRaisesRegex(ValueError, "上限"):
            execute_stage(self.run, state, "verification", lambda: called.append(True), model_stage=True)
        self.assertEqual(called, [])
        self.assertEqual(state["model_stage_attempts"], 1)

    def test_replay_does_not_run_scripts_or_model_stages(self):
        self.saved_fixture()
        state = new_state("replay", 0, None)
        with patch("run_workflow.run_script", side_effect=AssertionError("must not run")):
            paths = run_replay(self.run, state, self.source)
        self.assertEqual(state["model_stage_attempts"], 0)
        self.assertTrue(Path(paths["verification_dir"]).is_dir())

    def test_supported_still_requires_human_review(self):
        self.saved_fixture()
        state = new_state("replay", 0, None)
        paths = run_replay(self.run, state, self.source)
        create_handoff(self.run, state, paths)
        self.assertEqual(state["status"], "awaiting_human_review")
        self.assertEqual(state["verification_decision"], "supported_within_provided_evidence")
        self.assertEqual(read_json(self.run / "human-review.json")["status"], "pending")

    def test_revision_needed_does_not_auto_retry(self):
        self.saved_fixture("revise")
        state = new_state("replay", 0, None)
        paths = run_replay(self.run, state, self.source)
        create_handoff(self.run, state, paths)
        self.assertEqual(state["verification_decision"], "needs_human_review")
        self.assertEqual(state["model_stage_attempts"], 0)

    def test_replay_rejects_changed_source(self):
        self.saved_fixture()
        (self.source / "original-review.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "已改变"):
            check_saved_verification(self.source)

    def test_replay_rejects_different_candidate(self):
        self.saved_fixture()
        write_json(self.source / "revised-review.json", {})
        with self.assertRaisesRegex(ValueError, "不一致"):
            check_saved_verification(self.source)

    def test_verify_mode_reuses_analysis_and_calls_only_verifier(self):
        self.saved_fixture()
        original = read_json(self.source / "original-review.json")
        write_json(self.source / "review.json", original)
        state = new_state("verify", 1, None)
        def fake_script(root, stage, script, arguments):
            self.assertEqual(script, "verify_tutorial.py")
            self.assertEqual(stage, "verification")
            output = Path(arguments[arguments.index("--output-dir") + 1])
            output.mkdir()
            write_json(output / "latest.json", {"run_dir": str(self.source)})
        with patch("run_workflow.run_script", side_effect=fake_script) as runner:
            paths = run_verify_only(self.run, state, self.source, "test-model")
        runner.assert_called_once()
        self.assertEqual(state["model_stage_attempts"], 1)
        self.assertEqual(read_json(Path(paths["analysis_dir"]) / "review.json"), original)
        self.assertEqual([item["status"] for item in state["stages"]], ["completed", "completed"])


if __name__ == "__main__":
    unittest.main()
