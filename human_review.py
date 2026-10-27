"""本地人工复核：冻结来源、并发版本检查、原子反馈提交和可恢复状态投影。"""
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid

from check_materials import PROJECT_ROOT
from review_tutorial import validate_review
from run_workflow import check_saved_verification, now, read_json
from local_storage import atomic_json

FIELDS = ("observation", "interpretation", "suggestion", "validation_plan")
LOCK = threading.RLock()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def within(path, parent):
    path = Path(path).resolve()
    try:
        path.relative_to(Path(parent).resolve())
    except ValueError:
        raise ValueError("文件不在允许的目录中")
    return path


def render_feedback(feedback):
    labels = {"pending": "待复核", "accept": "保留候选稿", "revise": "修改后采用", "unsure": "暂不确定"}
    lines = ["# 人工复核记录", "", "样本：" + feedback["sample_id"], "",
        "状态：" + {"draft": "草稿，尚未提交", "needs_evidence": "已提交反馈，等待补充证据", "accepted": "人工复核已提交"}[feedback["status"]], "",
        "保存时间：" + feedback["saved_at"], "", "版本：" + str(feedback["revision"]), "",
        "范围：复核程序不自动执行源码或观众试用；后续补证按注明的来源解释。保留的证据引用与置信度来自模型候选稿。", "",
        "候选稿 SHA256：`" + feedback["candidate_sha256"] + "`", "",
        "## 整体总结", "", feedback["summary"]["text"], "",
        "决定：" + labels[feedback["summary"]["decision"]], "", "理由：" + (feedback["summary"]["note"] or "未填写"), ""]
    provenance = feedback.get("provenance", {})
    if provenance.get("actor") == "assistant_on_user_request":
        lines.extend(["提交方式：助手按用户明确授权整理并提交，不代表用户逐字手填。", "",
                      "授权原文：" + provenance["authorization_quote"], ""])
    for item in feedback.get("supplemental_evidence", []):
        lines.extend(["补充记录：[" + item["path"] + "](<" + item["path"] + ">)", "",
                      "补充记录 SHA256：`" + item["sha256"] + "`。提交时的内容快照保存在同版 JSON 中。", ""])
    for item in feedback["findings"]:
        lines.extend(["## " + item["id"], "", "决定：" + labels[item["decision"]], "", "理由：" + (item["note"] or "未填写"), ""])
        for key, label in zip(FIELDS, ("观察", "可能的影响", "建议", "验证计划")):
            lines.extend(["**" + label + "：** " + item[key], ""])
    if "reviewed_result" in feedback:
        lines.extend(["## 原模型声明的局限（生成时记录，后续补证见上文）", ""] + ["- " + x for x in feedback["reviewed_result"]["limitations"]])
    return "\n".join(lines) + "\n"


class ReviewStore:
    def __init__(self, workspace=PROJECT_ROOT):
        self.workspace = Path(workspace).resolve()
        self.runs = self.workspace / "reports/workflows"

    def root(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"(?:run|resume)-[A-Za-z0-9-]+", identifier):
            raise ValueError("无效的任务编号")
        root = within(self.runs / identifier, self.runs)
        if not (root / "human-review.json").is_file():
            raise ValueError("该任务尚未生成可复核候选稿")
        return root

    def sources(self, root):
        state = read_json(root / "workflow.json")
        form = read_json(root / "human-review.json")
        directory = within(state["artifacts"]["verification_dir"], root)
        original, bundle, verification, metadata = check_saved_verification(directory)
        candidate_hash = digest(directory / "revised-review.json")
        if candidate_hash != form["candidate_sha256"]:
            raise ValueError("候选稿已改变，请重新生成复核任务")
        for item in bundle["catalog"].values():
            if "local_path" in item:
                path = within(item["local_path"], self.workspace)
                if digest(path) != item["sha256"]:
                    raise ValueError("证据图片已改变，请重新生成复核任务")
        source_hash = hashlib.sha256("".join(digest(directory / name) for name in
            ("verification.json", "original-review.json", "evidence-catalog.json", "run.json")).encode()).hexdigest()
        return state, original, bundle, verification, candidate_hash, source_hash

    def feedback(self, root, candidate, candidate_hash, source_hash):
        path = root / "human-feedback.json"
        if path.exists():
            result = read_json(path)
            if result["candidate_sha256"] != candidate_hash or result["source_sha256"] != source_hash:
                raise ValueError("反馈对应的来源已改变，已阻止覆盖旧反馈")
            return result
        return {"schema_version": 1, "revision": 0, "status": "draft",
                "candidate_sha256": candidate_hash, "source_sha256": source_hash,
                "summary": {"decision": "pending", "note": "", "text": candidate["summary"]},
                "findings": [dict(id=item["id"], decision="pending", note="",
                    **{key: item[key] for key in FIELDS}) for item in candidate["findings"]]}

    def project(self, root, state, feedback):
        """human-feedback.json 是提交点；进程中断后重新读取即可恢复派生状态。"""
        revision = feedback["revision"]
        if not revision or state.get("human_review", {}).get("revision") == revision:
            return state
        status = feedback["status"]
        state["status"] = "human_reviewed" if status == "accepted" else "awaiting_human_review"
        state["human_review"] = {"status": status, "revision": revision,
            "form": str(root / "human-review.json"), "feedback": str(root / "human-feedback.json"),
            "updated_at": feedback["saved_at"]}
        state["stop_reason"] = {"accepted": "复核已提交；源码运行与观众效果须依据各自补证判断。",
            "needs_evidence": "人工标记了暂不确定，等待补充证据。", "draft": "人工复核草稿已保存，尚未提交。"}[status]
        if status == "accepted":
            state["human_review"]["result"] = str(root / "human-review-history" / feedback["history_file"])
        report = root / "human-feedback.md"
        report.write_text(render_feedback(feedback), encoding="utf-8")
        state["human_review"]["report"] = str(report)
        overview = root / "workflow.md"
        text = overview.read_text(encoding="utf-8")
        text = re.sub(r"\*\*当前状态：.*?\*\*", "**当前状态：" + state["stop_reason"] + "**", text, count=1)
        if "human-feedback.md" not in text:
            text += "\n## 人工复核最新记录\n\n见 [人工反馈记录](human-feedback.md)。原始分析与验证文件保留不变。\n"
        overview.write_text(text, encoding="utf-8")
        atomic_json(root / "workflow.json", state)
        latest = self.runs / "latest.json"
        if latest.exists():
            record = read_json(latest)
            if Path(record.get("run_dir", "")).resolve() == root:
                record["status"] = state["status"]
                atomic_json(latest, record)
        return state

    def list_runs(self):
        with LOCK:
            return self._list_runs()

    def _list_runs(self):
        rows = []
        for root in sorted(self.runs.glob("*"), reverse=True):
            if not root.is_dir() or not (root / "human-review.json").exists():
                continue
            try:
                state = read_json(root / "workflow.json")
                if (root / "human-feedback.json").exists():
                    # 列表只展示，完整的来源校验与中断恢复在打开任务时执行。
                    status = read_json(root / "human-feedback.json")["status"]
                    state["status"] = "human_reviewed" if status == "accepted" else "awaiting_human_review"
                rows.append({"id": root.name, "sample_id": state["sample_id"], "status": state["status"], "mode": state["mode"]})
            except (OSError, ValueError, KeyError):
                continue
        latest = self.runs / "latest.json"
        selected = Path(read_json(latest)["run_dir"]).name if latest.exists() else None
        return {"runs": rows, "selected": selected}

    def detail(self, identifier):
        with LOCK:
            root = self.root(identifier)
            state, original, bundle, verification, candidate_hash, source_hash = self.sources(root)
            candidate = verification["revised_review"]
            feedback = self.feedback(root, candidate, candidate_hash, source_hash)
            state = self.project(root, state, feedback)
            catalog = copy.deepcopy(bundle["catalog"])
            for item in catalog.values():
                item.pop("local_path", None)
            current_goal = None
            sample_id = state["sample_id"]
            if isinstance(sample_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", sample_id):
                sample_path = self.workspace / "data/samples" / (sample_id + ".json")
                if sample_path.is_file():
                    current_goal = read_json(sample_path).get("tutorial_goal")
            return {"id": identifier, "sample_id": state["sample_id"], "status": state["status"],
                    "mode": state["mode"], "task": bundle["task"], "coverage": bundle["coverage"],
                    "refinement": {k: state.get("refinement", {}).get(k) for k in ("status", "stop_reason")},
                    "current_goal": current_goal,
                    "goal_changed": bool(current_goal and current_goal != bundle["task"].get("tutorial_goal")),
                    "catalog": catalog, "original": original, "verification": verification,
                    "candidate": candidate, "feedback": feedback}

    def image(self, identifier, evidence_id):
        root = self.root(identifier)
        state = read_json(root / "workflow.json")
        directory = within(state["artifacts"]["verification_dir"], root)
        item = read_json(directory / "evidence-catalog.json")["catalog"].get(evidence_id, {})
        if "local_path" not in item:
            raise ValueError("这条证据不是图片")
        path = within(item["local_path"], self.workspace)
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp") or digest(path) != item["sha256"]:
            raise ValueError("图片格式或指纹不匹配")
        return path

    def save(self, identifier, payload, submit=False, *, actor="user", authorization_quote="", supplemental_paths=()):
        with LOCK:
            if actor not in ("user", "assistant_on_user_request"):
                raise ValueError("未知的提交者类型")
            if actor == "assistant_on_user_request" and not authorization_quote.strip():
                raise ValueError("助手代提交需要记录用户授权原文")
            supplements = []
            for supplied in supplemental_paths:
                path = within(supplied, self.workspace / "reports/runtime-checks")
                raw = path.read_bytes()
                if path.suffix != ".json" or len(raw) > 1000000:
                    raise ValueError("补证必须是大小受限的 JSON 记录")
                supplements.append({"path": path.as_posix(), "sha256": hashlib.sha256(raw).hexdigest(),
                                    "snapshot": json.loads(raw.decode("utf-8"))})
            root = self.root(identifier)
            state, original, bundle, verification, candidate_hash, source_hash = self.sources(root)
            candidate = verification["revised_review"]
            previous = self.feedback(root, candidate, candidate_hash, source_hash)
            for key in ("revision", "candidate_sha256", "source_sha256"):
                if payload.get(key) != previous[key]:
                    raise ValueError("页面版本已过期或来源不匹配，请刷新后重新核对")
            if previous["status"] == "accepted":
                raise ValueError("本轮已提交，不能覆盖已确认结果；请建立新的复核任务")
            items = payload.get("findings")
            if not isinstance(items, list) or [x.get("id") for x in items if isinstance(x, dict)] != [x["id"] for x in candidate["findings"]]:
                raise ValueError("复核条目与候选稿不一致")
            summary = payload.get("summary")
            result = copy.deepcopy(candidate)
            clean = []
            for index, (entry, source) in enumerate([(summary, {"text": candidate["summary"]})] + list(zip(items, candidate["findings"]))):
                if not isinstance(entry, dict) or entry.get("decision") not in ("pending", "accept", "revise", "unsure"):
                    raise ValueError("请选择有效的复核决定")
                decision = entry["decision"]
                keys = ("text",) if index == 0 else FIELDS
                note = entry.get("note", "")
                if not isinstance(note, str) or len(note) > 10000:
                    raise ValueError("复核理由无效或过长")
                value = {"decision": decision, "note": note.strip()}
                for key in keys:
                    text = entry.get(key)
                    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
                        raise ValueError("复核文字不能为空或过长")
                    value[key] = text.strip()
                if submit:
                    if decision == "pending":
                        raise ValueError("请先复核全部条目和总结，再提交")
                    if decision in ("revise", "unsure") and not value["note"]:
                        raise ValueError("修改或暂不确定时，请填写理由")
                    if decision == "accept" and any(value[k] != source[k] for k in keys):
                        raise ValueError("文字已修改，请选择修改后采用")
                    if decision == "revise" and all(value[k] == source[k] for k in keys):
                        raise ValueError("选择修改后采用时，请实际修改候选文字")
                if index == 0:
                    result["summary"] = value["text"]
                else:
                    value["id"] = source["id"]
                    result["findings"][index - 1].update({k: value[k] for k in keys})
                clean.append(value)
            validate_review(result, bundle)
            unresolved = any(x["decision"] == "unsure" for x in clean)
            status = ("needs_evidence" if unresolved else "accepted") if submit else "draft"
            feedback = {"schema_version": 1, "revision": previous["revision"] + 1,
                "candidate_sha256": candidate_hash, "source_sha256": source_hash,
                "status": status, "saved_at": now(), "summary": clean[0], "findings": clean[1:],
                "sample_id": state["sample_id"], "scope": "复核程序不自动执行源码或观众试用；补证按注明来源解释",
                "provenance": {"actor": actor, "authorization_quote": authorization_quote,
                               "based_on_revision": previous["revision"]},
                "supplemental_evidence": supplements}
            if status == "accepted":
                feedback["reviewed_result"] = result
            history = root / "human-review-history"
            history.mkdir(exist_ok=True)
            # 先保留不可变版本，再原子提交当前指针；崩溃留下的孤立版本不视为提交。
            version = history / ("revision-%04d.json" % feedback["revision"])
            if version.exists():
                version = history / ("revision-%04d-%s.json" % (feedback["revision"], uuid.uuid4().hex))
            feedback["history_file"] = version.name
            atomic_json(version, feedback)
            atomic_json(root / "human-feedback.json", feedback)
            self.project(root, state, feedback)
            return feedback
