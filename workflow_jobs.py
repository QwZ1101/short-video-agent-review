"""本机网页的计划、单次启动与独立进程；不执行用户提供的命令。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

from check_materials import PROJECT_ROOT, check_materials, load_sample, resolve_path
from create_sample import create_sample
from local_storage import atomic_json
from run_workflow import create_handoff, new_state, run_live, save_state, write_failure_report
from workflow_logs import redact
from workflow_plan import build_plan

SAMPLE_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
JOB_ID = re.compile(r"job-[a-f0-9]{32}")
TERMINAL = {"awaiting_human_review", "failed"}


def read(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("配置或状态文件过大")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def event(root, operation, **details):
    with (root / "events.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(dict(time=now(), trace_id=root.name, operation=operation, **details), ensure_ascii=False) + "\n")


def process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # 无权限时保守视作仍在运行。
        code = wintypes.DWORD()
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class JobStore:
    def __init__(self, workspace=PROJECT_ROOT):
        self.workspace = Path(workspace).resolve()
        self.folder = self.workspace / "reports/web-jobs"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.slot = self.folder / "active.json"

    def root(self, identifier):
        if not isinstance(identifier, str) or not JOB_ID.fullmatch(identifier):
            raise ValueError("启动任务编号无效")
        root = (self.folder / identifier).resolve()
        if root.parent != self.folder.resolve():
            raise ValueError("任务路径不在工作目录内")
        return root

    def sample_path(self, identifier):
        if not isinstance(identifier, str) or not SAMPLE_ID.fullmatch(identifier):
            raise ValueError("样本编号无效")
        folder = (self.workspace / "data/samples").resolve()
        path = (folder / (identifier + ".json")).resolve()
        if path.parent != folder:
            raise ValueError("样本路径不在登记目录内")
        return path

    def samples(self):
        result, unreadable = [], 0
        folder = self.workspace / "data/samples"
        paths = sorted(folder.glob("*.json"))[:200]
        for path in paths:
            try:
                sample = load_sample(self.sample_path(path.stem))
                if sample["sample_id"] != path.stem:
                    raise ValueError("样本编号与文件名不一致")
                result.append({"sample_id": sample["sample_id"], "goal": sample["tutorial_goal"],
                               "audience": sample["audience"], "goal_revision": sample.get("goal_revision")})
            except (OSError, ValueError, KeyError, TypeError):
                unreadable += 1
        return {"samples": result, "unreadable": unreadable, "limit": 200}

    def register(self, payload):
        allowed = {"video", "goal", "audience", "prior_knowledge", "transcript", "sample_id"}
        if set(payload) - allowed or any(value is not None and (not isinstance(value, str) or len(value) > 8000) for value in payload.values()):
            raise ValueError("登记字段无效或过长")
        path = create_sample(root=self.workspace, **payload)
        return {"sample_id": path.stem}

    def inputs(self, identifier):
        path = self.sample_path(identifier)
        if path.stat().st_size > 256 * 1024:
            raise ValueError("样本配置过大")
        sample = load_sample(path)
        if sample["sample_id"] != identifier:
            raise ValueError("样本编号与文件名不一致")
        # 冻结配置中的素材绝对路径，后台进程不依赖浏览器或启动终端的目录。
        paths = [sample["video"]] + sample.get("supporting_materials", [])
        for item in paths:
            item["path"] = str(resolve_path(item["path"], self.workspace).resolve())
        if sample.get("transcript_path"):
            sample["transcript_path"] = str(resolve_path(sample["transcript_path"], self.workspace).resolve())
        signatures = []
        for value in [item["path"] for item in paths] + ([sample["transcript_path"]] if sample.get("transcript_path") else []):
            file = Path(value)
            stat = file.stat()
            signatures.append({"path": value, "size": stat.st_size, "modified_ns": stat.st_mtime_ns})
        return sample, {"sample_sha256": digest(path), "materials": signatures}

    def preflight(self, sample):
        report = check_materials(sample, self.workspace)
        blockers = [item["id"] + "：" + redact(item["message"]) for item in report["materials"]
                    if item["status"] in ("invalid", "missing")]
        if not os.environ.get("DASHSCOPE_API_KEY"):
            blockers.append("后台进程未读取到 DASHSCOPE_API_KEY；请在配置环境变量后重启工作台")
        base = urlsplit(os.environ.get("DASHSCOPE_BASE_URL", "").strip())
        if base.scheme != "https" or not (base.hostname or "").endswith(".aliyuncs.com") or base.username or base.password or base.query or base.fragment:
            blockers.append("DASHSCOPE_BASE_URL 未配置为阿里云官方 HTTPS 基础接口地址")
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                blockers.append("未找到 " + tool)
        return blockers, report["missing_context"]

    def plan(self, payload):
        required = {"sample_id", "max_model_calls", "max_repair_rounds"}
        if not required <= set(payload) or set(payload) - required - {'metrics_record_id', 'metrics_match_confirmed'}:
            raise ValueError("计划请求字段不正确")
        budget, rounds = payload["max_model_calls"], payload["max_repair_rounds"]
        if type(rounds) is not int or rounds not in (0, 1, 2) or type(budget) is not int or budget != 2 + 2 * rounds:
            raise ValueError("网页预算需为 2 / 4 / 6 次，分别对应 0 / 1 / 2 轮可选返修")
        sample, fingerprint = self.inputs(payload["sample_id"])
        self.attach_metrics(sample, fingerprint, payload)
        blockers, warnings = self.preflight(sample)
        plan = build_plan(sample, budget)
        if plan['platform_data']['status'] == 'completed':
            warnings = [w for w in warnings if w != '后台数据已填写，但本步骤尚未验证其字段、口径和数值']
        root = self.root("job-" + uuid.uuid4().hex)
        root.mkdir()
        atomic_json(root / "sample.json", sample)
        request = dict(payload, model=os.environ.get("DASHSCOPE_MODEL") or "qwen3.8-flash",
                       fingerprint=fingerprint, snapshot_sha256=digest(root / "sample.json"))
        atomic_json(root / "request.json", request)
        state = {"id": root.name, "status": "planned", "sample_id": sample["sample_id"], "created_at": now(),
                 "model_stage_budget": budget, "max_repair_rounds": rounds, "plan": plan,
                 "ready": not blockers, "blockers": blockers, "warnings": warnings,
                 "request_sha256": digest(root / "request.json"), "run_id": "run-web-" + root.name[4:]}
        atomic_json(root / "job.json", state)
        event(root, "plan_created", ready=state["ready"], model_stage_budget=budget)
        return self.detail(root.name)

    def attach_metrics(self, sample, fingerprint, request):
        identifier = request.get('metrics_record_id')
        if not identifier:
            return
        # Lazy import avoids the screenshot store / job store module cycle.
        from screenshot_metrics import ScreenshotStore
        store = ScreenshotStore(self.workspace)
        sample['platform_metrics'] = store.for_workflow(identifier, sample['sample_id'], request.get('metrics_match_confirmed'))
        image, _ = store.image(identifier)
        sample.setdefault('supporting_materials', []).append({
            'id': identifier, 'type': 'screenshot', 'path': str(image)})
        fingerprint['metrics_record_sha256'] = digest(store.root(identifier) / 'record.json')

    def launch(self, root):
        flags = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with (root / "worker.log").open("wb") as log:
            return subprocess.Popen([sys.executable, str(PROJECT_ROOT / "workflow_jobs.py"),
                "--execute", root.name, "--workspace", str(self.workspace)], cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **flags)

    def release(self, identifier):
        if self.slot.exists() and read(self.slot).get("job_id") == identifier:
            self.slot.unlink()

    def start(self, identifier):
        root = self.root(identifier)
        if (root / "start.claim").exists():
            return self.detail(identifier)  # 同一个请求只执行一次，包括失败后重发。
        state, request = read(root / "job.json"), read(root / "request.json")
        if state["status"] != "planned" or digest(root / "request.json") != state["request_sha256"] or digest(root / "sample.json") != request["snapshot_sha256"]:
            raise ValueError("计划内容已改变，请重新生成计划")
        sample, fingerprint = self.inputs(request["sample_id"])
        self.attach_metrics(sample, fingerprint, request)
        if fingerprint != request["fingerprint"]:
            raise ValueError("样本或材料在预览后发生变化，请重新生成计划")
        blockers, _ = self.preflight(sample)
        if blockers:
            raise ValueError("；".join(blockers))
        try:
            with self.slot.open("x", encoding="utf-8") as output:
                output.write(json.dumps({"job_id": identifier, "created_at": now()}))
        except FileExistsError:
            if read(self.slot).get("job_id") == identifier:
                return self.detail(identifier)
            raise ValueError("已有网页任务占用运行槽，请查看当前任务；不会重复调用模型")
        try:
            with (root / "start.claim").open("x", encoding="utf-8") as output:
                output.write(now())
            state.update(status="queued", started_at=now())
            atomic_json(root / "job.json", state)
            event(root, "launch_requested", model_stage_budget=state["model_stage_budget"])
            child = self.launch(root)
        except Exception as error:
            state.update(status="failed", error=redact(str(error)), finished_at=now())
            atomic_json(root / "job.json", state)
            event(root, "launch_failed", error=redact(str(error)))
            self.release(identifier)
            raise
        # 已成功启动进程后若写 PID 失败，保留占用，不冒险重试或释放。
        atomic_json(root / "process.json", {"pid": child.pid})
        return self.detail(identifier)

    def detail(self, identifier):
        root = self.root(identifier)
        state = read(root / "job.json")
        result = {key: state.get(key) for key in ("id", "status", "sample_id", "created_at", "started_at", "finished_at",
                  "model_stage_budget", "max_repair_rounds", "plan", "ready", "blockers", "warnings", "run_id", "error")}
        workflow = self.workspace / "reports/workflows" / state["run_id"] / "workflow.json"
        if workflow.exists():
            live = read(workflow)
            result["workflow"] = {"status": live["status"], "model_stage_attempts": live["model_stage_attempts"],
                "stop_reason": redact(live.get("stop_reason")), "stages": [{"name": item["name"], "status": item["status"]} for item in live["stages"]]}
            for stage in live["stages"]:
                if stage["status"] != "failed" or stage.get("id") not in ("analysis", "verification", "repair-1", "repair-2", "reverify-1", "reverify-2"):
                    continue
                directory = workflow.parent / stage["id"]
                for pattern in ("run-*/failure.json", "run-*/run.json"):
                    for path in sorted(directory.glob(pattern), reverse=True)[:4]:
                        failure = read(path)
                        if failure.get("error"):
                            result["workflow"]["error_detail"] = redact(failure["error"])
                            break
                    if result["workflow"].get("error_detail"):
                        break
        if state["status"] in ("queued", "running") and (root / "process.json").exists():
            if not process_alive(read(root / "process.json")["pid"]):
                result.update(status="interrupted", error="后台进程已退出且未完成收尾；保留运行槽与记录，请先排查，不会自动重跑")
        return result

    def jobs(self):
        items, unreadable = [], 0
        for root in sorted(self.folder.glob("job-*"), key=lambda path: path.stat().st_mtime_ns, reverse=True)[:50]:
            try:
                item = self.detail(root.name)
                items.append({key: item[key] for key in ("id", "status", "sample_id", "created_at")})
            except (OSError, ValueError, KeyError, TypeError):
                unreadable += 1
        active = read(self.slot).get("job_id") if self.slot.exists() else None
        return {"jobs": items, "active_job": active, "unreadable": unreadable}


def execute(store, identifier):
    root = store.root(identifier)
    if not store.slot.exists() or read(store.slot).get("job_id") != identifier:
        raise ValueError("任务没有持有运行槽")
    with (root / "worker.claim").open("x", encoding="utf-8") as output:
        output.write(now())
    job, request = read(root / "job.json"), read(root / "request.json")
    run = store.workspace / "reports/workflows" / job["run_id"]
    created_run = False
    state = new_state("live", job["model_stage_budget"], job["sample_id"])
    try:
        if job["status"] != "queued" or digest(root / "request.json") != job["request_sha256"] or digest(root / "sample.json") != request["snapshot_sha256"]:
            raise ValueError("启动快照不匹配")
        sample, fingerprint = store.inputs(request["sample_id"])
        store.attach_metrics(sample, fingerprint, request)
        if fingerprint != request["fingerprint"]:
            raise ValueError("启动前材料已变化，停止调用")
        atomic_json(root / "process.json", {"pid": os.getpid()})
        job.update(status="running")
        atomic_json(root / "job.json", job)
        event(root, "worker_started")
        run.mkdir(parents=True)
        created_run = True
        save_state(run, state, "web_job_started", job_id=identifier)
        paths = run_live(run, state, root / "sample.json", request["model"], request["max_repair_rounds"])
        create_handoff(run, state, paths)
        atomic_json(run.parent / "latest.json", {"run_dir": str(run), "report_path": str(run / "workflow.md"), "mode": "live", "status": state["status"]})
        job.update(status="awaiting_human_review", finished_at=now())
        event(root, "worker_completed", model_stage_attempts=state["model_stage_attempts"])
    except Exception as error:
        job.update(status="failed", error=redact(str(error)), finished_at=now())
        state.update(status="failed", stop_reason=redact(str(error)), finished_at=now())
        for stage in state["stages"]:
            if stage["status"] in ("pending", "running"):
                stage.update(status="skipped" if stage["status"] == "pending" else "failed")
        if created_run:
            write_failure_report(run, state)
            save_state(run, state, "workflow_failed", error=redact(str(error)))
        event(root, "worker_failed", error=redact(str(error)), model_stage_attempts=state["model_stage_attempts"])
    finally:
        atomic_json(root / "job.json", job)
        store.release(identifier)
    return 0 if job["status"] == "awaiting_human_review" else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", required=True)
    parser.add_argument("--workspace", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    sys.exit(execute(JobStore(args.workspace), args.execute))
