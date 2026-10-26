import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from check_materials import PROJECT_ROOT
from review_tutorial import write_json
from resume_workflow import load_resume_source, recovery_state, repair_once, repair_request, reserve_resume
from run_workflow import new_state


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resume-test-", dir=str(PROJECT_ROOT / "reports"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        analysis = self.root / "analysis" / "run-test"
        failure = self.root / "verification" / "run-test"
        analysis.mkdir(parents=True)
        failure.mkdir(parents=True)
        original = {"sample_id": "example", "summary": "初稿", "findings": [{"id": "finding-1", "observation": "观察", "interpretation": "推测",
            "suggestion": "建议", "validation_plan": "实测", "evidence_ids": ["text-1"], "confidence": "low"}], "limitations": ["尚未验证"]}
        bundle = {"task": {"sample_id": "example"}, "catalog": {"text-1": {"type": "text", "text": "原始材料"}}}
        write_json(analysis / "review.json", original)
        write_json(analysis / "evidence-catalog.json", bundle)
        meta = {"status": "failed", "source_review_dir": str(analysis), "model": "test-model", "finish_reason": "stop"}
        for name, key in (("review.json", "review_sha256"), ("evidence-catalog.json", "evidence_sha256")):
            meta[key] = hashlib.sha256((analysis / name).read_bytes()).hexdigest()
        write_json(failure / "run.json", meta)
        (failure / "model-output.txt").write_text("[]", encoding="utf-8")
        state = new_state("live", 2, "example")
        state.update(status="failed", model_stage_attempts=2)
        for item in state["stages"][:3]:
            item["status"] = "completed"
        state["stages"][1]["artifacts"] = {"video_evidence": str(self.root / "video.json")}
        state["stages"][2]["artifacts"] = {"analysis_dir": str(analysis)}
        state["stages"][3]["status"] = "failed"
        write_json(self.root / "workflow.json", state)

    def test_loads_actual_empty_array_failure_without_calling_model(self):
        with patch("resume_workflow.call_dashscope", side_effect=AssertionError("no call")):
            source = load_resume_source(self.root)
        self.assertEqual(source["failed_text"], "[]")
        self.assertIn("JSON 对象", source["validation_error"])

    def test_failure_feedback_is_in_request(self):
        source = load_resume_source(self.root)
        payload = repair_request(source)
        self.assertEqual(payload["messages"][-2], {"role": "assistant", "content": "[]"})
        self.assertIn(source["validation_error"], payload["messages"][-1]["content"])
        self.assertIn("finding-1", payload["messages"][-1]["content"])
        self.assertEqual(payload["response_format"]["type"], "json_object")

    def test_recovery_has_own_one_call_budget_and_preserves_parent(self):
        source = load_resume_source(self.root)
        before = copy.deepcopy(source["state"])
        state = recovery_state(self.root, source)
        self.assertEqual(state["model_stage_budget"], 1)
        self.assertEqual(state["model_stage_attempts"], 0)
        self.assertEqual(state["parent_model_stage_attempts"], 2)
        self.assertEqual(state["stages"][2]["execution"], "reused_from_parent_no_rerun")
        self.assertEqual(source["state"], before)

    def test_duplicate_reservation_rejected(self):
        reserve_resume(self.root, self.root / "recovery")
        with self.assertRaisesRegex(ValueError, "已经启动"):
            reserve_resume(self.root, self.root / "another")

    def test_changed_analysis_rejected(self):
        path = self.root / "analysis/run-test/review.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["summary"] = "changed"
        write_json(path, data)
        with self.assertRaisesRegex(ValueError, "已变化"):
            load_resume_source(self.root)

    def test_cannot_resume_recovery_again(self):
        path = self.root / "workflow.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["mode"] = "resume"
        write_json(path, state)
        with self.assertRaisesRegex(ValueError, "只能恢复|只允许恢复"):
            load_resume_source(self.root)

    def test_network_error_is_not_treated_as_format_failure(self):
        path = self.root / "verification/run-test/run.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        del meta["finish_reason"]
        write_json(path, meta)
        with self.assertRaisesRegex(ValueError, "网络"):
            load_resume_source(self.root)

    def test_second_invalid_response_stops_after_one_call(self):
        source = load_resume_source(self.root)
        response = {"choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}]}
        with patch("resume_workflow.call_dashscope", return_value=response) as api, patch("builtins.print"):
            with self.assertRaisesRegex(ValueError, "JSON 对象"):
                repair_once(self.root / "recovery", source)
        api.assert_called_once()
        self.assertEqual(len(list((self.root / "recovery/verification").glob("run-*/model-output.txt"))), 1)


if __name__ == "__main__":
    unittest.main()
