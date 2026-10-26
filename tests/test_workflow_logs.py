import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

from review_server import ReviewServer
from workflow_logs import WorkflowLogs, RequestLog, read_tail, redact


class LogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / "reports/workflows/run-test"
        self.run.mkdir(parents=True)
        self.state = {"sample_id": "test", "mode": "live", "status": "failed",
            "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:00:02+00:00",
            "model_stage_attempts": 1, "model_stage_budget": 2,
            "stop_reason": "api_key=secret-example failed at https://example.test/?token=secret-example",
            "stages": [{"id": "analysis", "name": "分析", "status": "failed", "error": "Bearer secret-example"}]}
        (self.run / "workflow.json").write_text(json.dumps(self.state), encoding="utf-8")

    def test_failed_without_review_form_visible_and_redacted(self):
        store = WorkflowLogs(self.root)
        self.assertEqual(store.list_runs()["runs"][0]["status"], "failed")
        detail = store.detail("run-test")
        self.assertEqual(detail["duration_seconds"], 2)
        self.assertNotIn("secret-example", json.dumps(detail))
        self.assertEqual(detail["events"], [])

    def test_traversal_and_large_state_rejected(self):
        store = WorkflowLogs(self.root)
        with self.assertRaises(ValueError):
            store.detail("../run-test")
        (self.run / "workflow.json").write_text(" " * (2 * 1024 * 1024 + 1))
        self.assertEqual(store.list_runs()["unreadable"], 1)

    def test_tail_bounded_and_corrupt_lines_counted(self):
        path = self.run / "events.jsonl"
        path.write_text('\n'.join(json.dumps({"event": str(i), "extra": "x" * 2000}) for i in range(120)) + '\n{broken}\n', encoding="utf-8")
        rows, invalid, truncated = read_tail(path)
        self.assertTrue(truncated)
        self.assertEqual(invalid, 1)
        self.assertEqual(rows[-1]["event"], "119")
        self.assertLess(len(rows), 100)

    def test_unknown_event_fields_not_exposed(self):
        (self.run / "events.jsonl").write_text(json.dumps({"event": "stage_failed", "token": "secret-example", "payload": "private"}), encoding="utf-8")
        event = WorkflowLogs(self.root).detail("run-test")["events"][0]
        self.assertNotIn("token", event)
        self.assertNotIn("payload", event)

    def test_request_log_rotates(self):
        log = RequestLog(self.root)
        try:
            log.handler.maxBytes = 250
            for i in range(15):
                log.write(str(i), "GET", "/api/health", 200, 1)
            self.assertLessEqual(len(list(log.path.parent.glob("requests.jsonl*"))), 4)
            self.assertEqual(log.recent()["requests"][0]["request_id"], "14")
            log.write("zero-duration", "GET", "/api/health", 200, 0)
            self.assertEqual(log.recent()["requests"][0]["duration_ms"], "0")
        finally:
            log.close()

    def test_redacts_credentials_urls_and_windows_paths(self):
        self.assertNotIn("12345678", redact('"token": "12345678", password=12345678 sk-12345678'))
        self.assertNotIn("Users", redact(r"failed C:\Users\Example\private.txt"))

    def test_http_log_page_and_private_query_omission(self):
        server = ReviewServer(workspace=self.root)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%s" % server.server_port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/logs/run?run=run-test&token=secret-example") as response:
            self.assertTrue(response.headers["X-Request-ID"])
            self.assertEqual(json.load(response)["status"], "failed")
        with opener.open(url + "/logs") as response:
            self.assertIn("运行日志", response.read().decode("utf-8"))
        self.assertNotIn("secret-example", server.request_log.path.read_text(encoding="utf-8"))
        with self.assertRaises(urllib.error.HTTPError):
            opener.open(urllib.request.Request(url + "/api/logs", headers={"Host": "evil.test"}))
        with opener.open(url + "/api/logs/requests") as response:
            self.assertTrue(json.load(response)["requests"])
