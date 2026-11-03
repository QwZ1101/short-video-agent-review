"""仅监听本机；复核页面只读模型结果，启动接口显式运行有预算的固定工作流。"""
import argparse
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import secrets
import time
import uuid
from urllib.parse import urlparse, parse_qs

from check_materials import PROJECT_ROOT
from human_review import ReviewStore, render_feedback
from improvement_tasks import TaskStore, render_plan
from workflow_logs import WorkflowLogs, RequestLog
from workflow_jobs import JobStore
from screenshot_metrics import ScreenshotStore
from creation_cycles import CycleStore

STATIC = {"/": "index.html", "/app.js": "app.js", "/tasks.js": "tasks.js", "/style.css": "style.css",
          "/cycles": "cycles.html", "/cycles.js": "cycles.js", "/workspace.js": "workspace.js", "/embedded.css": "embedded.css", "/cycles.css": "cycles.css",
          "/metrics": "metrics.html", "/metrics.js": "metrics.js", "/metrics.css": "metrics.css",
          "/logs": "logs.html", "/logs.js": "logs.js", "/logs.css": "logs.css",
          "/start": "start.html", "/start.js": "start.js", "/start.css": "start.css"}
LOGGED_ROUTES = set(STATIC) | {"/api/health", "/api/session", "/api/runs", "/api/review", "/api/tasks",
    "/api/tasks/report", "/api/report", "/api/evidence", "/api/draft", "/api/submit",
    "/api/tasks/create", "/api/tasks/save", "/api/logs", "/api/logs/run", "/api/logs/requests",
    "/api/samples", "/api/samples/register", "/api/jobs", "/api/jobs/detail", "/api/jobs/plan", "/api/jobs/start",
    "/api/metrics", "/api/metrics/detail", "/api/metrics/image", "/api/metrics/upload", "/api/metrics/recognize", "/api/metrics/confirm",
    "/api/cycles", "/api/cycles/source", "/api/cycles/sources", "/api/cycles/detail", "/api/cycles/report",
    "/api/cycles/create", "/api/cycles/implement", "/api/cycles/compare", "/api/cycles/defer", "/api/cycles/decide", "/api/cycles/close"}


class ReviewServer(ThreadingHTTPServer):
    def __init__(self, port=0, workspace=PROJECT_ROOT):
        self.store = ReviewStore(workspace)
        self.tasks = TaskStore(self.store)
        self.jobs = JobStore(workspace)
        self.metrics = ScreenshotStore(workspace)
        self.cycles = CycleStore(self.store)
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.workflow_logs = WorkflowLogs(workspace)
        self.request_log = RequestLog(workspace)

    def server_close(self):
        super().server_close()
        if hasattr(self, "request_log"):
            self.request_log.close()


class Handler(BaseHTTPRequestHandler):
    def handle_one_request(self):
        self.request_id = uuid.uuid4().hex
        self.request_started = time.monotonic()
        return super().handle_one_request()

    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, format, *args):
        # 不记录正文、令牌、证据路径或模型凭据。
        pass

    def send(self, body, mime="application/json; charset=utf-8", status=200):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", self.request_id)
        # Workspace tools can only be embedded by this same local origin.
        ancestors = "'self'" if urlparse(self.path).path in ('/metrics', '/start', '/') else "'none'"
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; frame-ancestors " + ancestors + "; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        finally:
            route = urlparse(self.path).path
            self.server.request_log.write(self.request_id, self.command,
                route if route in LOGGED_ROUTES else "[other]", status,
                (time.monotonic() - self.request_started) * 1000)

    def trusted(self):
        host = "127.0.0.1:%s" % self.server.server_port
        return self.headers.get("Host") == host and self.headers.get("Origin", "http://" + host) == "http://" + host

    def do_GET(self):
        if not self.trusted():
            return self.send({"error": "仅允许本机工作台访问"}, status=403)
        route = urlparse(self.path)
        query = parse_qs(route.query)
        try:
            if route.path == '/' and 'run' not in query:
                return self.send((PROJECT_ROOT / 'review-ui/dist/cycles.html').read_bytes(), 'text/html; charset=utf-8')
            if route.path == "/api/health":
                return self.send({"app": "tutorial-human-review", "workspace": str(self.server.store.workspace)})
            if route.path == "/api/session":
                return self.send({"token": self.server.token})
            if route.path == '/api/cycles':
                return self.send(self.server.cycles.listing())
            if route.path == '/api/cycles/sources':
                return self.send(self.server.cycles.sources())
            if route.path == '/api/cycles/source':
                return self.send(self.server.cycles.source(query.get('run', [''])[0]))
            if route.path == '/api/cycles/detail':
                return self.send(self.server.cycles.load(query.get('id', [''])[0]))
            if route.path == '/api/cycles/report':
                return self.send(self.server.cycles.report(query.get('id', [''])[0]).encode('utf-8'), 'text/plain; charset=utf-8')
            if route.path == "/api/metrics":
                return self.send(self.server.metrics.listing())
            if route.path == "/api/metrics/detail":
                return self.send(self.server.metrics.read(query.get('id', [''])[0]))
            if route.path == "/api/metrics/image":
                path, mime = self.server.metrics.image(query.get('id', [''])[0])
                return self.send(path.read_bytes(), mime)
            if route.path == "/api/runs":
                return self.send(self.server.store.list_runs())
            if route.path == "/api/samples":
                return self.send(self.server.jobs.samples())
            if route.path == "/api/jobs":
                return self.send(self.server.jobs.jobs())
            if route.path == "/api/jobs/detail":
                return self.send(self.server.jobs.detail(query.get("job", [""])[0]))
            if route.path == "/api/logs":
                return self.send(self.server.workflow_logs.list_runs())
            if route.path == "/api/logs/requests":
                return self.send(self.server.request_log.recent())
            identifier = query.get("run", [""])[0]
            if route.path == "/api/logs/run":
                return self.send(self.server.workflow_logs.detail(identifier))
            if route.path == "/api/review":
                return self.send(self.server.store.detail(identifier))
            if route.path == "/api/tasks":
                return self.send({"plan": self.server.tasks.load(identifier)})
            if route.path == "/api/tasks/report":
                return self.send(render_plan(self.server.tasks.load(identifier)).encode("utf-8"), "text/plain; charset=utf-8")
            if route.path == "/api/report":
                feedback = self.server.store.detail(identifier)["feedback"]
                if not feedback["revision"]:
                    raise ValueError("请先保存一版人工反馈")
                return self.send(render_feedback(feedback).encode("utf-8"), "text/plain; charset=utf-8")
            if route.path == "/api/evidence":
                path = self.server.store.image(identifier, query.get("id", [""])[0])
                return self.send(path.read_bytes(), mimetypes.guess_type(str(path))[0])
            if route.path in STATIC:
                path = PROJECT_ROOT / "review-ui/dist" / STATIC[route.path]
                mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}[path.suffix]
                return self.send(path.read_bytes(), mime + "; charset=utf-8")
            self.send({"error": "页面不存在"}, status=404)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.send({"error": "无法读取复核材料：" + self.safe_error(error)}, status=400)

    @staticmethod
    def safe_error(error):
        return str(error) if isinstance(error, ValueError) else "文件缺失或数据结构异常，请检查本机报告"

    def do_POST(self):
        if not self.trusted() or not secrets.compare_digest(self.headers.get("X-Review-Token", ""), self.server.token):
            return self.send({"error": "访问校验失败，请刷新页面"}, status=403)
        route = urlparse(self.path)
        if route.path not in ("/api/draft", "/api/submit", "/api/tasks/create", "/api/tasks/save",
                               "/api/cycles/create", "/api/cycles/implement", "/api/cycles/compare", "/api/cycles/defer", "/api/cycles/decide", "/api/cycles/close",
                               "/api/metrics/upload", "/api/metrics/recognize", "/api/metrics/confirm",
                               "/api/samples/register", "/api/jobs/plan", "/api/jobs/start"):
            return self.send({"error": "接口不存在"}, status=404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            limit = 15*1024*1024 if route.path == '/api/metrics/upload' else 300000
            if not 0 < size <= limit or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("请求类型或大小不正确")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("请求格式不正确")
            if route.path.startswith('/api/cycles/'):
                operation = route.path.rsplit('/', 1)[1]
                return self.send(getattr(self.server.cycles, operation)(payload))
            if route.path == '/api/metrics/upload':
                return self.send(self.server.metrics.upload(payload))
            if route.path == '/api/metrics/recognize':
                if set(payload) != {'id'}:
                    raise ValueError('识别请求仅接受截图编号')
                return self.send(self.server.metrics.start(payload['id']))
            if route.path == '/api/metrics/confirm':
                if set(payload) != {'id', 'confirmation'}:
                    raise ValueError('确认请求结构不正确')
                return self.send(self.server.metrics.confirm(payload['id'], payload['confirmation']))
            if route.path == "/api/samples/register":
                return self.send(self.server.jobs.register(payload))
            if route.path == "/api/jobs/plan":
                return self.send(self.server.jobs.plan(payload))
            if route.path == "/api/jobs/start":
                if set(payload) != {"job_id"}:
                    raise ValueError("启动请求仅接受已预览的任务编号")
                return self.send(self.server.jobs.start(payload["job_id"]))
            if route.path == "/api/tasks/create":
                return self.send({"plan": self.server.tasks.create(payload.get("run"))})
            if route.path == "/api/tasks/save":
                return self.send({"plan": self.server.tasks.save(payload.get("run"), payload.get("task"))})
            if not isinstance(payload, dict) or not isinstance(payload.get("feedback"), dict):
                raise ValueError("反馈格式不正确")
            feedback = self.server.store.save(payload.get("run"), payload["feedback"], route.path == "/api/submit")
            self.send({"feedback": feedback})
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.send({"error": self.safe_error(error)}, status=409)


def create_server(port=0, preferred_port=0):
    try:
        return ReviewServer(port or preferred_port)
    except OSError as error:
        # Never stop a different process that has taken the previous port.
        if not port and preferred_port and (error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048):
            return ReviewServer(0)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--preferred-port", type=int, default=0)
    args = parser.parse_args()
    server = create_server(args.port, args.preferred_port)
    print("http://127.0.0.1:%s" % server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
