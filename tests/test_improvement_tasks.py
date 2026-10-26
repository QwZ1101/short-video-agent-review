import copy
import json
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

import test_human_review as fixtures
from human_review import atomic_json, digest
from improvement_tasks import TaskStore, progress
from review_server import ReviewServer
from run_workflow import read_json


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HumanReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.reviews = self.fixture.store
        self.root = self.fixture.root
        self.tasks = TaskStore(self.reviews)

    def accepted(self):
        self.reviews.save("run-test", self.fixture.approve(), True)
        return self.tasks.create("run-test")

    def payload(self, plan, status, **extra):
        result = dict(copy.deepcopy(plan["tasks"][0]), revision=plan["revision"],
                      source_feedback_sha256=plan["source_feedback_sha256"], task_id=plan["tasks"][0]["id"], status=status)
        result.update(extra)
        return result

    def ready(self):
        plan = self.accepted()
        plan = self.tasks.save("run-test", self.payload(plan, "in_progress"))
        return self.tasks.save("run-test", self.payload(plan, "ready_for_validation", implementation_note="增加演示", artifact_reference="video-v2"))

    def result(self, outcome="passed", version="video-v2"):
        return dict(outcome=outcome, tested_version=version, method="目标观众独立操作", record="模拟：成功找到入口，无额外提示")

    def test_unaccepted_review_cannot_generate(self):
        with self.assertRaisesRegex(ValueError, "先提交"):
            self.tasks.create("run-test")
        self.assertFalse((self.root / "improvement-tasks.json").exists())

    def test_generation_is_idempotent_and_pending(self):
        plan = self.accepted()
        self.assertEqual(plan, self.tasks.create("run-test"))
        self.assertEqual(plan["tasks"][0]["instruction"], "改善")
        self.assertEqual(plan["tasks"][0]["status"], "pending")
        self.assertEqual(progress(plan)["validated"], 0)
        self.assertEqual(read_json(self.root / "workflow.json")["status"], "human_reviewed")

    def test_pending_cannot_skip_to_passed(self):
        plan = self.accepted()
        with self.assertRaisesRegex(ValueError, "跳过"):
            self.tasks.save("run-test", self.payload(plan, "validated", implementation_note="已改", artifact_reference="video-v2", validation=self.result()))

    def test_ready_requires_implementation_and_artifact(self):
        plan = self.tasks.save("run-test", self.payload(self.accepted(), "in_progress"))
        with self.assertRaisesRegex(ValueError, "修改说明和作品版本"):
            self.tasks.save("run-test", self.payload(plan, "ready_for_validation"))

    def test_pass_requires_actual_record_and_matching_version(self):
        plan = self.ready()
        with self.assertRaisesRegex(ValueError, "实际记录"):
            self.tasks.save("run-test", self.payload(plan, "validated"))
        with self.assertRaisesRegex(ValueError, "版本必须"):
            self.tasks.save("run-test", self.payload(plan, "validated", validation=self.result(version="video-v1")))
        plan = self.tasks.save("run-test", self.payload(plan, "validated", validation=self.result()))
        self.assertEqual(progress(plan)["status"], "validated_user_reported")
        self.assertEqual(plan["tasks"][0]["validation"]["source"], "user_reported")

    def test_failed_or_inconclusive_returns_to_revision(self):
        plan = self.ready()
        plan = self.tasks.save("run-test", self.payload(plan, "needs_revision", validation=self.result("failed")))
        self.assertEqual(progress(plan)["validated"], 0)
        self.assertEqual(plan["tasks"][0]["status"], "needs_revision")

    def test_new_version_invalidates_pass_and_preserves_history(self):
        plan = self.ready()
        plan = self.tasks.save("run-test", self.payload(plan, "validated", validation=self.result()))
        historical = self.root / "improvement-history" / plan["history_file"]
        with self.assertRaisesRegex(ValueError, "先返回修改中"):
            self.tasks.save("run-test", self.payload(plan, "validated", artifact_reference="video-v3", validation=self.result(version="video-v3")))
        plan = self.tasks.save("run-test", self.payload(plan, "in_progress", artifact_reference="video-v3"))
        self.assertEqual(plan["tasks"][0]["validation"]["outcome"], "not_tested")
        self.assertEqual(read_json(historical)["tasks"][0]["validation"]["outcome"], "passed")

    def test_stale_save_and_changed_source_rejected(self):
        plan = self.accepted()
        self.tasks.save("run-test", self.payload(plan, "in_progress"))
        with self.assertRaisesRegex(ValueError, "过期"):
            self.tasks.save("run-test", self.payload(plan, "blocked", implementation_note="缺录屏"))
        feedback = read_json(self.root / "human-feedback.json")
        feedback["summary"]["text"] = "被改动"
        atomic_json(self.root / "human-feedback.json", feedback)
        with self.assertRaisesRegex(ValueError, "历史版本不一致"):
            self.tasks.load("run-test")

    def test_changed_plan_and_blocked_without_reason_rejected(self):
        plan = self.accepted()
        with self.assertRaisesRegex(ValueError, "缺少什么"):
            self.tasks.save("run-test", self.payload(plan, "blocked"))
        plan["tasks"][0]["instruction"] = "篡改"
        atomic_json(self.root / "improvement-tasks.json", plan)
        with self.assertRaisesRegex(ValueError, "历史版本不一致"):
            self.tasks.load("run-test")

    def test_projection_recovers_and_review_is_unchanged(self):
        plan = self.accepted()
        fingerprint = digest(self.root / "human-feedback.json")
        with patch.object(self.tasks, "project", side_effect=OSError("模拟中断")):
            with self.assertRaises(OSError):
                self.tasks.save("run-test", self.payload(plan, "in_progress"))
        loaded = self.tasks.load("run-test")
        self.assertEqual(read_json(self.root / "workflow.json")["improvement_tracking"]["revision"], loaded["revision"])
        self.assertEqual(digest(self.root / "human-feedback.json"), fingerprint)

    def test_http_task_flow_and_csrf(self):
        self.reviews.save("run-test", self.fixture.approve(), True)
        server = ReviewServer(workspace=self.fixture.workspace)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%s" % server.server_port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def post(path, body, token=True):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["X-Review-Token"] = server.token
            with opener.open(urllib.request.Request(url + path, data=json.dumps(body).encode(), headers=headers)) as response:
                return json.load(response)
        with self.assertRaises(urllib.error.HTTPError) as error:
            post("/api/tasks/create", {"run": "run-test"}, False)
        self.assertEqual(error.exception.code, 403)
        plan = post("/api/tasks/create", {"run": "run-test"})["plan"]
        result = post("/api/tasks/save", {"run": "run-test", "task": self.payload(plan, "in_progress")})
        self.assertEqual(result["plan"]["tasks"][0]["status"], "in_progress")
        with opener.open(url + "/api/tasks/report?run=run-test") as response:
            self.assertIn("修改中", response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
