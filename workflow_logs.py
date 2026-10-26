"""Bounded, read-only workflow diagnostics and rotating HTTP summaries."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import uuid


RUN_ID = re.compile(r"(?:run|resume)-[A-Za-z0-9-]+")
MAX_STATE = 2 * 1024 * 1024
TAIL_BYTES = 65536


def redact(value, limit=600):
    text = "" if value is None else str(value)
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|token|password|authorization|secret)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}]+", r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)", "[REDACTED]", text)
    text = re.sub(r"https?://[^\s<>\"']+", "[URL]", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s\"<>]+", "[LOCAL_PATH]", text)
    return text[:limit]


def elapsed(start, end):
    try:
        return round(max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()), 3)
    except (ValueError, TypeError):
        return None


def read_state(path):
    if path.stat().st_size > MAX_STATE:
        raise ValueError("状态文件过大")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("状态文件格式错误")
    return value


def read_tail(path, count=100):
    if not path.is_file():
        return [], 0, False
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        offset = max(0, size - TAIL_BYTES)
        stream.seek(offset)
        raw = stream.read(TAIL_BYTES)
    if offset:
        raw = raw.partition(b"\n")[2]
    lines = raw.decode("utf-8", errors="replace").splitlines()
    rows, invalid = [], 0
    for line in lines[-count:]:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError()
            rows.append(row)
        except ValueError:
            invalid += 1
    return rows, invalid, bool(offset or len(lines) > count)


class WorkflowLogs:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / "reports/workflows"

    def directory(self, identifier):
        if not isinstance(identifier, str) or not RUN_ID.fullmatch(identifier):
            raise ValueError("无效的流程编号")
        path = (self.root / identifier).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            raise ValueError("流程目录越界")
        return path

    def summary(self, directory):
        state = read_state(directory / "workflow.json")
        return {"id": directory.name, "sample_id": redact(state.get("sample_id"), 80),
                "mode": redact(state.get("mode"), 30), "status": redact(state.get("status"), 60),
                "started_at": redact(state.get("started_at"), 80),
                "duration_seconds": elapsed(state.get("started_at"), state.get("finished_at")),
                "model_stage_attempts": state.get("model_stage_attempts") if type(state.get("model_stage_attempts")) is int else None,
                "model_stage_budget": state.get("model_stage_budget") if type(state.get("model_stage_budget")) is int else None}

    def list_runs(self):
        candidates = sorted((p.name for p in self.root.glob("*") if RUN_ID.fullmatch(p.name)), reverse=True)
        rows, invalid = [], 0
        for identifier in candidates[:100]:
            try:
                rows.append(self.summary(self.directory(identifier)))
            except (OSError, ValueError, TypeError):
                invalid += 1
        rows.sort(key=lambda x: x["started_at"], reverse=True)
        return {"runs": rows, "unreadable": invalid, "limited": len(candidates) > 100,
                "scope": "最多读取 100 个流程目录；历史状态按原始记录展示"}

    def detail(self, identifier):
        directory = self.directory(identifier)
        state = read_state(directory / "workflow.json")
        rows, invalid, truncated = read_tail(directory / "events.jsonl")
        result = self.summary(directory)
        result.update(stop_reason=redact(state.get("stop_reason")),
            stages=[{"id": redact(s.get("id"), 80), "name": redact(s.get("name"), 80),
                     "status": redact(s.get("status"), 60), "error": redact(s.get("error") or s.get("reason")),
                     "duration_seconds": elapsed(s.get("started_at"), s.get("finished_at"))}
                    for s in state.get("stages", [])[:30] if isinstance(s, dict)],
            events=[{k: redact(row.get(k)) for k in ("time", "event", "stage", "error")} for row in rows],
            invalid_event_lines=invalid, events_truncated=truncated)
        return result


class RequestLog:
    def __init__(self, workspace):
        self.path = Path(workspace) / "reports/review-server/requests.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("review-requests-" + uuid.uuid4().hex)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.handler = RotatingFileHandler(self.path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(self.handler)

    def write(self, request_id, method, route, status, duration_ms):
        self.logger.info(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id, "method": method if method in ("GET", "POST") else "OTHER",
            "route": route, "status": status, "duration_ms": round(duration_ms, 3)}, ensure_ascii=False))

    def recent(self):
        rows, invalid, truncated = read_tail(self.path, 50)
        return {"requests": [{k: redact(row.get(k), 160) for k in
                ("time", "request_id", "method", "route", "status", "duration_ms")} for row in reversed(rows)],
                "invalid_lines": invalid, "truncated": truncated}

    def close(self):
        self.handler.close()
        self.logger.removeHandler(self.handler)
