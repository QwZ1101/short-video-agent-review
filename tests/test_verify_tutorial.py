import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from verify_tutorial import build_verification_request, load_review_run, validate_verification


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {"task": {"sample_id": "example"}, "catalog": {"evidence-1": {"type": "text", "text": "证据内容"}}}
        self.original = {"sample_id": "example", "summary": "初稿", "findings": [
            {"id": "finding-1", "observation": "观察", "interpretation": "推测", "suggestion": "建议",
             "validation_plan": "实际检查", "evidence_ids": ["evidence-1"], "confidence": "medium"}], "limitations": ["未运行"]}
        self.result = {"sample_id": "example", "summary_check": {"status": "supported", "reason": "在证据范围内", "evidence_ids": ["evidence-1"]},
                       "access_check": {"creator_side": "录屏者操作界面", "viewer_side": "观众路径尚未证实", "evidence_ids": ["evidence-1"]},
                       "checks": [{"finding_id": "finding-1", "status": "supported", "reason": "符合证据", "evidence_ids": ["evidence-1"]}],
                       "revised_review": copy.deepcopy(self.original), "limitations": ["模型判断仍可能出错"]}

    def test_all_supported_is_scoped(self):
        self.assertEqual(validate_verification(self.result, self.original, self.bundle), "supported_within_provided_evidence")

    def test_any_uncertainty_requires_human_review(self):
        for status in ("revise", "insufficient_evidence"):
            self.result["summary_check"]["status"] = status
            self.assertEqual(validate_verification(self.result, self.original, self.bundle), "needs_human_review")

    def test_rejects_missing_or_duplicate_check(self):
        for checks in ([], self.result["checks"] * 2):
            result = copy.deepcopy(self.result)
            result["checks"] = checks
            with self.assertRaisesRegex(ValueError, "恰好"):
                validate_verification(result, self.original, self.bundle)

    def test_rejects_unknown_reference(self):
        self.result["checks"][0]["evidence_ids"] = ["imagined"]
        with self.assertRaisesRegex(ValueError, "证据"):
            validate_verification(self.result, self.original, self.bundle)

    def test_rejects_replacing_finding_id(self):
        self.result["revised_review"]["findings"][0]["id"] = "finding-99"
        with self.assertRaisesRegex(ValueError, "保留"):
            validate_verification(self.result, self.original, self.bundle)

    def test_rejects_wrong_sample(self):
        self.result["sample_id"] = "another"
        with self.assertRaisesRegex(ValueError, "样本"):
            validate_verification(self.result, self.original, self.bundle)

    def test_rejects_empty_array_from_real_model_failure(self):
        with self.assertRaisesRegex(ValueError, "JSON 对象"):
            validate_verification([], self.original, self.bundle)

    def test_requires_actor_access_check(self):
        del self.result["access_check"]
        with self.assertRaisesRegex(ValueError, "操作路径"):
            validate_verification(self.result, self.original, self.bundle)

    def test_request_separates_draft_from_evidence_and_removes_paths(self):
        self.bundle["catalog"]["evidence-1"]["local_path"] = "PRIVATE_LOCAL_PATH"
        payload = build_verification_request(self.original, self.bundle, [], "test-model")
        user = json.loads(payload["messages"][1]["content"][0]["text"])
        self.assertEqual(user["original_review"], self.original)
        self.assertNotIn("PRIVATE_LOCAL_PATH", json.dumps(payload))
        self.assertIn("验证员", payload["messages"][0]["content"])

    def test_does_not_read_manual_answer_and_rejects_changed_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "test.jpg"
            image.write_bytes(b"original fixture bytes")
            self.bundle["catalog"]["image-1"] = {"type": "screenshot", "local_path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
            (root / "review.json").write_text(json.dumps(self.original), encoding="utf-8")
            (root / "evidence-catalog.json").write_text(json.dumps(self.bundle), encoding="utf-8")
            (root / "manual-review.md").write_text("DO_NOT_INCLUDE_MANUAL_ANSWER", encoding="utf-8")
            review, bundle, images, _ = load_review_run(root)
            payload = build_verification_request(review, bundle, images, "test-model")
            self.assertNotIn("DO_NOT_INCLUDE_MANUAL_ANSWER", json.dumps(payload))
            image.write_bytes(b"changed fixture bytes")
            with self.assertRaisesRegex(ValueError, "已变化"):
                load_review_run(root)


if __name__ == "__main__":
    unittest.main()
