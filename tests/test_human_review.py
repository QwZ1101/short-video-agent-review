import copy
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import json

from check_materials import PROJECT_ROOT
from human_review import ReviewStore, atomic_json, digest, render_feedback
from review_server import ReviewServer
from run_workflow import new_state, run_replay, create_handoff, read_json


class HumanReviewTests(unittest.TestCase):
    def setUp(self):
        (PROJECT_ROOT / "reports").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "reports", prefix="review-test-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.source = self.workspace / "source"
        self.source.mkdir()
        self.root = self.workspace / "reports/workflows/run-test"
        self.root.mkdir(parents=True)
        original = {"sample_id": "test", "summary": "有待验证", "findings": [dict(id="f1", observation="观察", interpretation="可能", suggestion="改善", validation_plan="实测", evidence_ids=["t1"], confidence="medium")], "limitations": ["未实测"]}
        bundle = {"task": {"sample_id": "test", "tutorial_goal": "运行", "platform_metrics": None}, "coverage": {"selected_frames": 0, "available_frames": 0, "subtitle_count": 0, "image_count": 0}, "catalog": {"t1": {"type": "text", "text": "文字证据"}}}
        verification = {"sample_id": "test", "access_check": {"creator_side": "作者", "viewer_side": "观众", "evidence_ids": ["t1"]}, "summary_check": {"status": "supported", "reason": "理由", "evidence_ids": ["t1"]}, "checks": [{"finding_id": "f1", "status": "supported", "reason": "理由", "evidence_ids": ["t1"]}], "revised_review": original, "limitations": ["未实测"]}
        for name, data in (("original-review.json", original), ("evidence-catalog.json", bundle), ("verification.json", verification), ("revised-review.json", original)):
            atomic_json(self.source / name, data)
        atomic_json(self.source / "run.json", {"status": "completed_structure_valid", "model": "test", "review_sha256": digest(self.source / "original-review.json"), "evidence_sha256": digest(self.source / "evidence-catalog.json")})
        for name in ("prompt.md", "model-output.txt"):
            (self.source / name).write_text("fixture")
        state = new_state("replay", 0, "test")
        paths = run_replay(self.root, state, self.source)
        create_handoff(self.root, state, paths)
        atomic_json(self.root.parent / "latest.json", {"run_dir": str(self.root), "status": state["status"]})
        self.store = ReviewStore(self.workspace)
        self.form = self.store.detail("run-test")["feedback"]

    def approve(self):
        form = copy.deepcopy(self.form)
        form["summary"]["decision"] = "accept"
        form["findings"][0]["decision"] = "accept"
        return form

    def test_draft_persists_without_approval(self):
        self.form["findings"][0]["note"] = "我的草稿"
        self.store.save("run-test", self.form)
        detail = self.store.detail("run-test")
        self.assertEqual(detail["feedback"]["findings"][0]["note"], "我的草稿")
        self.assertEqual(detail["status"], "awaiting_human_review")
        self.assertNotIn("reviewed_result", detail["feedback"])

    def test_latest_review_is_sorted_by_time_not_random_run_identifier(self):
        for name,stamp in [('run-zz-old','2026-01-01T00:00:00+00:00'),('run-aa-new','2026-11-04T00:00:00+00:00')]:
            folder=self.root.parent/name;folder.mkdir()
            atomic_json(folder/'workflow.json',dict(sample_id='test',status='awaiting_human_review',mode='live',started_at=stamp))
            atomic_json(folder/'human-review.json',{})
        rows=self.store.list_runs()['runs']
        ids=[r['id'] for r in rows]
        self.assertLess(ids.index('run-aa-new'),ids.index('run-zz-old'))

    def test_changed_goal_is_marked_without_rewriting_historical_task(self):
        before = digest(self.root / "verification/evidence-catalog.json")
        sample = self.workspace / "data/samples/test.json"
        sample.parent.mkdir(parents=True)
        atomic_json(sample, {"tutorial_goal": "索要展示链接或者点赞"})
        detail = self.store.detail("run-test")
        self.assertTrue(detail["goal_changed"])
        self.assertEqual(detail["current_goal"], "索要展示链接或者点赞")
        self.assertEqual(detail["task"]["tutorial_goal"], "运行")
        self.assertEqual(digest(self.root / "verification/evidence-catalog.json"), before)

    def test_assisted_submission_records_actor_and_freezes_supplement(self):
        proof = self.workspace / "reports/runtime-checks/test.json"
        proof.parent.mkdir(parents=True)
        atomic_json(proof, {"result": "user_reported_pass"})
        with self.assertRaisesRegex(ValueError, "授权原文"):
            self.store.save("run-test", self.approve(), True, actor="assistant_on_user_request")
        result = self.store.save("run-test", self.approve(), True, actor="assistant_on_user_request",
                                 authorization_quote="请帮我完成并提交", supplemental_paths=[proof])
        atomic_json(proof, {"result": "changed"})
        self.assertEqual(result["supplemental_evidence"][0]["snapshot"]["result"], "user_reported_pass")
        self.assertEqual(result["provenance"]["actor"], "assistant_on_user_request")
        self.assertIn("助手按用户明确授权", render_feedback(result))

    def test_supplement_must_stay_in_runtime_records(self):
        with self.assertRaisesRegex(ValueError, "允许的目录"):
            self.store.save("run-test", self.form, supplemental_paths=[self.source / "run.json"])

    def test_pending_cannot_submit(self):
        with self.assertRaisesRegex(ValueError, "全部"):
            self.store.save("run-test", self.form, True)
        self.assertFalse((self.root / "human-feedback.json").exists())

    def test_unsure_keeps_gate(self):
        form = self.approve()
        form["findings"][0].update(decision="unsure", note="需要实际运行")
        result = self.store.save("run-test", form, True)
        self.assertEqual(result["status"], "needs_evidence")
        self.assertNotIn("reviewed_result", result)
        self.assertEqual(self.store.detail("run-test")["status"], "awaiting_human_review")

    def test_reason_and_real_edit_required(self):
        form = self.approve()
        form["findings"][0]["decision"] = "revise"
        with self.assertRaisesRegex(ValueError, "理由"):
            self.store.save("run-test", form, True)
        form["findings"][0]["note"] = "修正措辞"
        with self.assertRaisesRegex(ValueError, "实际修改"):
            self.store.save("run-test", form, True)

    def test_edited_accept_rejected(self):
        form = self.approve()
        form["findings"][0]["suggestion"] = "已修改文字"
        with self.assertRaisesRegex(ValueError, "文字已修改"):
            self.store.save("run-test", form, True)

    def test_submit_retains_source_and_outputs_revision(self):
        before = digest(self.root / "verification/revised-review.json")
        form = self.approve()
        form["findings"][0].update(decision="revise", interpretation="可能导致教程失败", note="避免确定断言")
        result = self.store.save("run-test", form, True)
        self.assertEqual(result["reviewed_result"]["findings"][0]["interpretation"], "可能导致教程失败")
        self.assertEqual(digest(self.root / "verification/revised-review.json"), before)
        self.assertEqual(self.store.detail("run-test")["status"], "human_reviewed")
        self.assertEqual(read_json(self.root.parent / "latest.json")["status"], "human_reviewed")
        with self.assertRaisesRegex(ValueError, "已提交"):
            self.store.save("run-test", result, True)

    def test_stale_page_and_changed_candidate_blocked(self):
        self.store.save("run-test", self.form)
        with self.assertRaisesRegex(ValueError, "过期"):
            self.store.save("run-test", self.form)
        with (self.root / "verification/revised-review.json").open("a") as output:
            output.write(" ")
        with self.assertRaisesRegex(ValueError, "候选稿已改变"):
            self.store.save("run-test", self.form)

    def test_changed_verifier_blocked(self):
        self.store.save("run-test", self.form)
        path = self.root / "verification/verification.json"
        value = read_json(path)
        value["checks"][0]["reason"] = "另一条理由"
        atomic_json(path, value)
        with self.assertRaisesRegex(ValueError, "来源已改变"):
            self.store.detail("run-test")

    def test_changed_image_or_external_path_blocked(self):
        image = self.workspace / "proof.png"
        image.write_bytes(b"test image")
        directory = self.root / "verification"
        catalog = read_json(directory / "evidence-catalog.json")
        catalog["catalog"]["img"] = {"type": "screenshot", "local_path": str(image), "sha256": digest(image)}
        atomic_json(directory / "evidence-catalog.json", catalog)
        meta = read_json(directory / "run.json")
        meta["evidence_sha256"] = digest(directory / "evidence-catalog.json")
        atomic_json(directory / "run.json", meta)
        self.assertEqual(self.store.image("run-test", "img"), image)
        form = self.store.detail("run-test")["feedback"]
        image.write_bytes(b"changed image")
        with self.assertRaisesRegex(ValueError, "证据图片已改变"):
            self.store.save("run-test", form)
        with self.assertRaisesRegex(ValueError, "指纹"):
            self.store.image("run-test", "img")
        catalog["catalog"]["img"]["local_path"] = str(PROJECT_ROOT / "README.md")
        atomic_json(directory / "evidence-catalog.json", catalog)
        with self.assertRaisesRegex(ValueError, "允许的目录"):
            self.store.image("run-test", "img")

    def test_projection_recovers_after_interrupted_commit(self):
        with patch.object(self.store, "project", side_effect=OSError("模拟提交后断电")):
            with self.assertRaises(OSError):
                self.store.save("run-test", self.approve(), True)
        self.assertEqual(read_json(self.root / "workflow.json")["status"], "awaiting_human_review")
        self.assertEqual(self.store.detail("run-test")["status"], "human_reviewed")

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.store.detail("../run-test")
        with self.assertRaises(ValueError):
            self.store.image("run-test", "../../.env")

    def test_http_host_csrf_and_static_allowlist(self):
        server = ReviewServer(workspace=self.workspace)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%s" % server.server_port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path, headers in (("/api/runs", {"Host": "evil.test"}), ("/.env", {})):
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(urllib.request.Request(url + path, headers=headers))
            self.assertIn(error.exception.code, (403, 404))
        body = json.dumps({"run": "run-test", "feedback": self.approve()}).encode()
        with self.assertRaises(urllib.error.HTTPError) as error:
            opener.open(urllib.request.Request(url + "/api/submit", data=body, headers={"Content-Type": "application/json"}))
        self.assertEqual(error.exception.code, 403)
        headers = {"Content-Type": "application/json", "X-Review-Token": server.token}
        response = opener.open(urllib.request.Request(url + "/api/submit", data=body, headers=headers))
        self.assertEqual(json.load(response)["feedback"]["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
