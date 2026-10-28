from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from create_sample import create_sample
from local_storage import atomic_json
from review_server import ReviewServer
from workflow_jobs import JobStore, execute, read


class WebJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.video = self.root / "视频.mp4"
        self.video.write_bytes(b"synthetic not decodable video")
        self.sample = create_sample(str(self.video), "点赞或者索要链接", "初学者", "会浏览网页", sample_id="test-video", root=self.root)
        self.store = JobStore(self.root)
        key = patch.dict(os.environ, {"DASHSCOPE_API_KEY": "fixture-credential-not-real", "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1"})
        tool = patch("workflow_jobs.shutil.which", return_value="fixture-tool")
        key.start(); tool.start()
        self.addCleanup(key.stop); self.addCleanup(tool.stop)

    def plan(self, **changes):
        return self.store.plan(dict(sample_id="test-video", max_model_calls=2, max_repair_rounds=0, **changes))

    def launch_fake(self, identifier):
        with patch.object(self.store, "launch", return_value=Mock(pid=os.getpid())) as launch:
            result = self.store.start(identifier)
        return result, launch

    def test_plan_freezes_budget_without_model_or_process(self):
        with patch("workflow_jobs.subprocess.Popen", side_effect=AssertionError("no process")), patch("review_tutorial.call_dashscope", side_effect=AssertionError("no API")):
            result = self.plan()
        self.assertTrue(result["ready"])
        self.assertEqual(result["model_stage_budget"], 2)
        self.assertEqual(result["plan"]["goal"], "点赞或者索要链接")
        self.assertFalse(self.store.slot.exists())
        root = self.store.root(result["id"])
        self.assertEqual(read(root / "sample.json")["video"]["path"], str(self.video))
        self.assertNotIn("fixture-credential-not-real", ''.join(path.read_text(encoding="utf-8") for path in root.glob('*.json*')))

    def test_base_url_check_and_failure_detail_are_safe(self):
        with patch.dict(os.environ, {"DASHSCOPE_BASE_URL": "https://untrusted.test/?token=fixture-secret"}):
            job = self.plan()
            self.assertFalse(job["ready"])
            self.assertNotIn("fixture-secret", json.dumps(job))
        job = self.plan()
        run = self.root / "reports/workflows" / job["run_id"]
        failure = run / "analysis/run-fixture"
        failure.mkdir(parents=True)
        atomic_json(failure / "failure.json", {"error": "connection closed; api_key=fixture-secret"})
        atomic_json(run / "workflow.json", {"status": "failed", "model_stage_attempts": 1, "stages": [{"id": "analysis", "name": "分析", "status": "failed"}]})
        detail = self.store.detail(job["id"])
        self.assertIn("connection closed", detail["workflow"]["error_detail"])
        self.assertNotIn("fixture-secret", json.dumps(detail))

    def test_budgets_and_paths_are_server_validated(self):
        for budget, rounds in ((1, 0), (6, 0), (4, 2), (True, 0), (2, -1), (2, False)):
            with self.assertRaises(ValueError):
                self.store.plan({"sample_id": "test-video", "max_model_calls": budget, "max_repair_rounds": rounds})
        for identifier in ("../test-video", "C:/sample", "A"):
            with self.assertRaises(ValueError):
                self.store.sample_path(identifier)
        with self.assertRaises(ValueError):
            self.store.detail("../outside")
        for budget, rounds in ((2, 0), (4, 1), (6, 2)):
            self.assertEqual(self.store.plan({"sample_id": "test-video", "max_model_calls": budget, "max_repair_rounds": rounds})["max_repair_rounds"], rounds)

    def test_missing_key_tools_and_changed_materials_block_start(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}), patch("workflow_jobs.shutil.which", return_value=None):
            job = self.plan()
            self.assertFalse(job["ready"])
            self.assertEqual(len(job["blockers"]), 3)
            with self.assertRaises(ValueError), patch.object(self.store, "launch", side_effect=AssertionError("blocked")):
                self.store.start(job["id"])
        job = self.plan()
        self.video.write_bytes(b"changed input")
        with self.assertRaisesRegex(ValueError, "变化"):
            self.store.start(job["id"])
        self.assertFalse(self.store.slot.exists())

    def test_changed_budget_or_goal_snapshot_rejected(self):
        job = self.plan()
        root = self.store.root(job["id"])
        request = read(root / "request.json")
        request["max_model_calls"] = 6
        atomic_json(root / "request.json", request)
        with self.assertRaisesRegex(ValueError, "计划内容"):
            self.store.start(job["id"])
        job = self.plan()
        sample = read(self.sample)
        sample["tutorial_goal"] = "new goal"
        atomic_json(self.sample, sample)
        with self.assertRaisesRegex(ValueError, "变化"):
            self.store.start(job["id"])

    def test_repeat_start_is_idempotent_and_second_job_is_blocked(self):
        first, second = self.plan(), self.plan()
        _, launch = self.launch_fake(first["id"])
        self.assertEqual(launch.call_count, 1)
        with patch.object(self.store, "launch", side_effect=AssertionError("no duplicate")):
            self.assertEqual(self.store.start(first["id"])["status"], "queued")
            with self.assertRaisesRegex(ValueError, "占用"):
                self.store.start(second["id"])
        # 重新创建服务对象也能读取占用，不依赖内存锁。
        self.assertEqual(JobStore(self.root).jobs()["active_job"], first["id"])

    def test_concurrent_starts_spawn_at_most_once(self):
        job = self.plan()
        def start():
            try:
                return self.store.start(job["id"])
            except (ValueError, OSError):
                return None  # 极短暂的文件占用/写入窗口可以拒绝，不允许多启动。
        with patch.object(self.store, "launch", return_value=Mock(pid=os.getpid())) as launch:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: start(), range(4)))
        self.assertEqual(launch.call_count, 1)

    def test_launch_failure_is_saved_and_never_retried_by_same_request(self):
        job = self.plan()
        with patch.object(self.store, "launch", side_effect=OSError("fixture spawn failure")):
            with self.assertRaises(OSError):
                self.store.start(job["id"])
        self.assertFalse(self.store.slot.exists())
        with patch.object(self.store, "launch", side_effect=AssertionError("no retry")):
            self.assertEqual(self.store.start(job["id"])["status"], "failed")

    def test_worker_failure_preserves_workflow_and_redacts_job_error(self):
        job = self.plan()
        self.launch_fake(job["id"])
        with patch("workflow_jobs.run_live", side_effect=ValueError("api_key=fixture-secret network failed")):
            self.assertEqual(execute(self.store, job["id"]), 1)
        detail = self.store.detail(job["id"])
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["workflow"]["model_stage_attempts"], 0)
        self.assertNotIn("fixture-secret", json.dumps(detail))
        self.assertTrue((self.root / "reports/workflows" / job["run_id"] / "workflow.md").is_file())
        self.assertFalse(self.store.slot.exists())

    def test_worker_handoff_budget_and_same_worker_cannot_execute_twice(self):
        job = self.store.plan({"sample_id": "test-video", "max_model_calls": 4, "max_repair_rounds": 1})
        self.launch_fake(job["id"])
        def handoff(root, state, paths):
            state.update(status="awaiting_human_review", model_stage_attempts=2)
            atomic_json(root / "workflow.json", state)
        with patch("workflow_jobs.run_live", return_value={}) as live, patch("workflow_jobs.create_handoff", side_effect=handoff):
            self.assertEqual(execute(self.store, job["id"]), 0)
        args = live.call_args.args
        self.assertEqual(args[1]["model_stage_budget"], 4)
        self.assertEqual(args[-1], 1)
        self.assertEqual(self.store.detail(job["id"])["status"], "awaiting_human_review")
        with self.assertRaises(ValueError):
            execute(self.store, job["id"])
        self.assertFalse(self.store.slot.exists())

    def test_dead_worker_keeps_slot_and_never_automatically_restarts(self):
        job = self.plan()
        self.launch_fake(job["id"])
        with patch("workflow_jobs.process_alive", return_value=False):
            self.assertEqual(self.store.detail(job["id"])["status"], "interrupted")
        self.assertTrue(self.store.slot.exists())

    def test_existing_workflow_is_not_overwritten(self):
        job = self.plan()
        self.launch_fake(job["id"])
        run = self.root / "reports/workflows" / job["run_id"]
        run.mkdir(parents=True)
        marker = run / "workflow.json"
        marker.write_text('{"historical":true}')
        self.assertEqual(execute(self.store, job["id"]), 1)
        self.assertEqual(marker.read_text(), '{"historical":true}')

    def test_registration_does_not_inherit_other_samples_and_rejects_commands(self):
        result = self.store.register({"video": str(self.video), "goal": "评论", "audience": "观众", "prior_knowledge": "无"})
        sample = read(self.store.sample_path(result["sample_id"]))
        self.assertEqual(sample["supporting_materials"], [])
        self.assertNotEqual(result["sample_id"], "test-video")
        with self.assertRaises(ValueError):
            self.store.register({"command": "anything"})

    def test_http_routes_require_token_origin_and_reject_start_overrides(self):
        server = ReviewServer(workspace=self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%s" % server.server_port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        headers = {"Content-Type": "application/json", "X-Review-Token": server.token}
        def post(path, payload, custom=None):
            return opener.open(urllib.request.Request(base + path, data=json.dumps(payload).encode(), headers=custom or headers))
        with self.assertRaises(urllib.error.HTTPError) as denied:
            post('/api/jobs/plan', {}, {"Content-Type": "application/json"})
        self.assertEqual(denied.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            post('/api/jobs/plan', {}, dict(headers, Origin="https://outside.test"))
        self.assertEqual(denied.exception.code, 403)
        with opener.open(base + '/start') as response:
            self.assertEqual(response.status, 200)
        with post('/api/jobs/plan', {"sample_id": "test-video", "max_model_calls": 2, "max_repair_rounds": 0}) as response:
            job = json.load(response)
        with self.assertRaises(urllib.error.HTTPError):
            post('/api/jobs/start', {"job_id": job["id"], "max_model_calls": 100})
        with patch.object(server.jobs, "launch", return_value=Mock(pid=os.getpid())) as launch:
            for _ in range(2):
                with post('/api/jobs/start', {"job_id": job["id"]}) as response:
                    self.assertEqual(json.load(response)["status"], "queued")
            self.assertEqual(launch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
