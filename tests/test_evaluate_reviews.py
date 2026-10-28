import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from evaluate_reviews import ARMS, distribution, freeze, load_packet, prepare, read, resource_total, score, sha
from local_storage import atomic_json


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.saved_fixture("revise")
        self.verification = self.fixture.source
        self.analysis = self.fixture.root / "analysis"
        self.analysis.mkdir()
        self.output = self.fixture.root / "evaluation"
        self.original = read(self.verification / "original-review.json")
        self.bundle = read(self.verification / "evidence-catalog.json")
        # 合成图片只用于指纹校验，不做视觉质量判断。
        image = self.fixture.root / "frame.png"
        image.write_bytes(b"synthetic image fingerprint fixture")
        self.bundle["catalog"]["frame-1"] = {"type": "video_frame", "local_path": str(image),
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
        for directory in (self.analysis, self.verification):
            atomic_json(directory / "evidence-catalog.json", self.bundle)
        atomic_json(self.analysis / "review.json", self.original)
        self.meta = {"model": "test", "status": "structure_and_references_valid_semantics_unverified",
            "automatic_retries": 0, "finish_reason": "stop", "elapsed_seconds": 0,
            "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}}
        atomic_json(self.analysis / "run.json", self.meta)
        verifier_meta = read(self.verification / "run.json")
        verifier_meta.update(automatic_retries=0, finish_reason="stop", elapsed_seconds=2,
            evidence_sha256=sha(self.verification / "evidence-catalog.json"),
            usage={"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20})
        atomic_json(self.verification / "run.json", verifier_meta)

    def prepare(self):
        with patch("review_tutorial.call_dashscope", side_effect=AssertionError("no API allowed")):
            return prepare(self.analysis, self.verification, self.output, self.bundle["task"])

    def draft(self):
        path = self.output / "annotations.draft.json"
        value = read(path)
        value["provenance"]["actor"] = "human"
        finding = value["arms"][ARMS[0]]["findings"]["finding-1"]
        finding.update(verdict="supported", reason="合成测试理由，不是真实标注")
        atomic_json(path, value)
        return path, value

    def test_preparation_has_no_score_or_new_calls_and_freezes_images(self):
        self.prepare()
        result = read(self.output / "comparison.json")
        self.assertEqual(result["status"], "awaiting_human_annotations")
        self.assertIsNone(result["arms"][ARMS[0]]["findings"]["supported_proportion"])
        self.assertIsNone(result["completion_rate"])
        manifest, _ = load_packet(self.output)
        self.assertFalse(manifest["held_out"])
        self.assertFalse(manifest["labels_frozen_before_model_execution"])
        self.assertEqual(manifest["new_model_calls"], 0)
        self.assertEqual(manifest["resources"][ARMS[0]]["model_seconds"], 0)
        self.assertEqual(manifest["resources"][ARMS[1]]["tokens"]["total_tokens"], 30)
        Path(self.bundle["catalog"]["frame-1"]["local_path"]).unlink()
        load_packet(self.output)  # 原图消失后仍保留当时快照。

    def test_changed_source_and_old_goal_rejected_before_output(self):
        changed = dict(self.bundle["task"], tutorial_goal="another goal")
        with self.assertRaisesRegex(ValueError, "当前样本"):
            prepare(self.analysis, self.verification, self.output, changed)
        self.assertFalse(self.output.exists())
        changed_review = dict(self.original, summary="another initial review")
        atomic_json(self.analysis / "review.json", changed_review)
        with self.assertRaisesRegex(ValueError, "同一份"):
            self.prepare()

    def test_source_image_tamper_rejected(self):
        Path(self.bundle["catalog"]["frame-1"]["local_path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "图片已变化"):
            self.prepare()

    def test_refinement_or_unknown_call_count_is_not_a_baseline(self):
        for changes in ({"status": "completed_structure_valid"}, {"automatic_retries": 1}, {"finish_reason": "length"}):
            atomic_json(self.analysis / "run.json", dict(self.meta, **changes))
            with self.assertRaises(ValueError):
                self.prepare()

    def test_pending_insufficient_and_wrong_are_distinct_denominators(self):
        result = distribution([{"verdict": status} for status in ("supported", "revise", "insufficient_evidence", None)])
        self.assertEqual(result["judged"], 3)
        self.assertEqual(result["supported_proportion"], 1 / 3)
        self.assertEqual(result["coverage"], .75)
        self.assertEqual(result["pending"], 1)
        self.assertIsNone(distribution([])["coverage"])
        self.assertIsNone(distribution([{"verdict": None}])["supported_proportion"])

    def test_missing_usage_is_unknown_and_zero_is_preserved(self):
        value = resource_total([self.meta, {}])
        self.assertIsNone(value["tokens"]["total_tokens"])
        self.assertIsNone(value["model_seconds"])
        self.assertEqual(resource_total([self.meta])["tokens"]["completion_tokens"], 0)
        for invalid in (True, -1, float("inf"), "10"):
            self.assertIsNone(resource_total([dict(self.meta, elapsed_seconds=invalid)])["model_seconds"])

    def test_assistant_or_unsubmitted_drafts_never_count(self):
        self.prepare()
        path, value = self.draft()
        self.assertIsNone(score(self.output)["arms"][ARMS[0]]["findings"]["supported_proportion"])
        for actor in ("assistant", "unconfirmed"):
            value["provenance"]["actor"] = actor
            atomic_json(path, value)
            with self.assertRaisesRegex(ValueError, "人工草稿"):
                freeze(self.output, path, "reviewer-test", True)
        self.assertFalse((self.output / "annotations.frozen.json").exists())

    def test_confirmation_required_partial_labels_are_visible_and_cannot_overwrite(self):
        self.prepare()
        path, _ = self.draft()
        with self.assertRaisesRegex(ValueError, "人工完成"):
            freeze(self.output, path, "reviewer-test")
        result = freeze(self.output, path, "reviewer-test", True)
        self.assertEqual(result["arms"][ARMS[0]]["findings"]["supported_proportion"], 1)
        self.assertIsNone(result["arms"][ARMS[1]]["findings"]["supported_proportion"])
        self.assertFalse(result["annotation_coverage_complete"])
        previous = sha(self.output / "annotations.frozen.json")
        with self.assertRaises(FileExistsError):
            freeze(self.output, path, "reviewer-test", True)
        self.assertEqual(sha(self.output / "annotations.frozen.json"), previous)

    def test_rejects_missing_duplicate_wrong_refs_or_reasons(self):
        self.prepare()
        path, original = self.draft()
        for change in ("missing_arm", "wrong_ref", "wrong_id", "missing_reason", "wrong_packet", "invalid_verdict"):
            value = copy.deepcopy(original)
            finding = value["arms"][ARMS[0]]["findings"]["finding-1"]
            if change == "missing_arm":
                del value["arms"][ARMS[1]]
            elif change == "wrong_ref":
                finding["citations"]["unknown"] = {"verdict": None, "reason": ""}
            elif change == "wrong_id":
                value["arms"][ARMS[0]]["findings"]["another"] = finding
            elif change == "missing_reason":
                finding["reason"] = " "
            elif change == "wrong_packet":
                value["manifest_sha256"] = "other"
            else:
                finding["verdict"] = "pass"
            atomic_json(path, value)
            with self.assertRaises(ValueError, msg=change):
                freeze(self.output, path, "reviewer-test", True)

    def test_frozen_labels_or_result_changes_block_rescoring(self):
        self.prepare()
        path, _ = self.draft()
        freeze(self.output, path, "reviewer-test", True)
        sealed = self.output / "annotations.frozen.json"
        old = sealed.read_bytes()
        value = read(sealed)
        value["arms"][ARMS[0]]["findings"]["finding-1"]["verdict"] = "revise"
        atomic_json(sealed, value)
        with self.assertRaisesRegex(ValueError, "冻结标注已变化"):
            score(self.output)
        sealed.write_bytes(old)
        atomic_json(self.output / (ARMS[0] + ".json"), dict(self.original, summary="changed"))
        with self.assertRaisesRegex(ValueError, "冻结内容已变化"):
            score(self.output)

    def test_manifest_path_escape_rejected(self):
        self.prepare()
        path = self.output / "manifest.json"
        value = read(path)
        value["files"]["../analysis/review.json"] = sha(self.analysis / "review.json")
        atomic_json(path, value)
        with self.assertRaisesRegex(ValueError, "目录内"):
            load_packet(self.output)

    def test_existing_packet_not_overwritten_and_events_omit_labels(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.prepare()
        path, _ = self.draft()
        freeze(self.output, path, "reviewer-test", True)
        logs = (self.output / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("合成测试理由", logs)
        self.assertNotIn("reviewer-test", logs)
        self.assertTrue(all("duration_seconds" in json.loads(line) for line in logs.splitlines()))


if __name__ == "__main__":
    unittest.main()
