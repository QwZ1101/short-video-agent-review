"""把已提交复核转换为修改任务；验证结果是注明来源的人工记录。"""
import copy
import uuid

from human_review import LOCK, atomic_json, digest, within
from run_workflow import now, read_json

STATUS_LABELS = {"pending": "待修改", "in_progress": "修改中", "ready_for_validation": "待验证",
                 "needs_revision": "需要再修改", "validated": "验证通过（用户记录）", "blocked": "等待条件"}
TRANSITIONS = {
    "pending": ("pending", "in_progress", "blocked"),
    "in_progress": ("in_progress", "ready_for_validation", "blocked"),
    "ready_for_validation": ("ready_for_validation", "validated", "needs_revision", "in_progress", "blocked"),
    "needs_revision": ("needs_revision", "in_progress", "blocked"),
    "validated": ("validated", "in_progress"),
    "blocked": ("blocked", "pending", "in_progress"),
}
OUTCOMES = ("not_tested", "passed", "failed", "inconclusive")


def empty_validation():
    return {"outcome": "not_tested", "tested_version": "", "method": "", "record": "", "source": "user_reported"}


def progress(plan):
    counts = {status: sum(t["status"] == status for t in plan["tasks"]) for status in STATUS_LABELS}
    completed = counts["validated"]
    return {"counts": counts, "total": len(plan["tasks"]), "validated": completed,
            "status": "validated_user_reported" if plan["tasks"] and completed == len(plan["tasks"]) else "open"}


def render_plan(plan):
    metrics = progress(plan)
    lines = ["# 修改任务与验证记录", "", "样本：" + plan["sample_id"], "",
             "来源复核版本：%s；任务记录版本：%s。" % (plan["source_feedback_revision"], plan["revision"]), "",
             "验证通过（用户记录）：%s / %s。" % (metrics["validated"], metrics["total"]), "",
             "任务由已提交建议直接转换，未调用模型。作品引用是用户填写的定位信息，程序不读取或执行该路径。验证通过表示已记录的测试通过，不代表普遍效果提升。", ""]
    for task in plan["tasks"]:
        lines += ["## " + task["title"], "", "来源：" + task["source_finding_id"], "",
                  "状态：" + STATUS_LABELS[task["status"]], "", "修改要求：" + task["instruction"], "",
                  "建议验证办法：" + task["validation_plan"], "", "执行说明：" + (task["implementation_note"] or "未填写"), "",
                  "作品版本或引用：" + (task["artifact_reference"] or "未填写"), ""]
        test = task["validation"]
        lines += ["验证结果：" + {"not_tested": "未测试", "passed": "通过", "failed": "未通过", "inconclusive": "无法判断"}[test["outcome"]], "",
                  "被测版本：" + (test["tested_version"] or "未填写"), "", "验证方法：" + (test["method"] or "未填写"), "",
                  "实际记录：" + (test["record"] or "未填写"), ""]
    return "\n".join(lines)


class TaskStore:
    def __init__(self, review_store):
        self.reviews = review_store

    def source(self, identifier):
        detail = self.reviews.detail(identifier)
        feedback = detail["feedback"]
        if feedback["status"] != "accepted":
            raise ValueError("请先提交复核，再生成修改任务")
        root = self.reviews.root(identifier)
        history = within(root / "human-review-history" / feedback["history_file"], root / "human-review-history")
        if read_json(history) != feedback:
            raise ValueError("已提交复核与历史版本不一致，停止生成任务")
        return root, feedback, digest(root / "human-feedback.json")

    def load(self, identifier):
        with LOCK:
            root, feedback, source_hash = self.source(identifier)
            path = root / "improvement-tasks.json"
            if path.exists():
                plan = read_json(path)
                if plan["source_feedback_sha256"] != source_hash:
                    raise ValueError("任务来源复核已改变，不能沿用旧的修改或验证结果")
                history = within(root / "improvement-history" / plan["history_file"], root / "improvement-history")
                if read_json(history) != plan:
                    raise ValueError("任务记录与历史版本不一致")
                self.project(root, plan)
                return plan
            return {"schema_version": 1, "sample_id": feedback["sample_id"], "revision": 0,
                    "source_feedback_revision": feedback["revision"], "source_feedback_sha256": source_hash,
                    "generation": "deterministic_from_accepted_review_no_model_call", "tasks": [
                        {"id": "task-%s" % (i + 1), "title": "修改任务 %s" % (i + 1),
                         "source_finding_id": item["id"], "instruction": item["suggestion"],
                         "validation_plan": item["validation_plan"], "status": "pending",
                         "implementation_note": "", "artifact_reference": "", "validation": empty_validation()}
                        for i, item in enumerate(feedback["reviewed_result"]["findings"])]}

    def project(self, root, plan):
        # 计划文件是提交点；下面的派生输出在再次读取时可以恢复。
        state = read_json(root / "workflow.json")
        if state.get("improvement_tracking", {}).get("revision") == plan["revision"]:
            return
        (root / "improvement-tasks.md").write_text(render_plan(plan), encoding="utf-8")
        state["improvement_tracking"] = dict(progress(plan), revision=plan["revision"],
            path=str(root / "improvement-tasks.json"), report=str(root / "improvement-tasks.md"))
        atomic_json(root / "workflow.json", state)

    def commit(self, root, plan, event, task_id=None):
        plan = copy.deepcopy(plan)
        plan["revision"] += 1
        plan["saved_at"] = now()
        plan["last_event"] = {"type": event, "task_id": task_id, "actor": "user_recorded" if task_id else "program_generated"}
        folder = root / "improvement-history"
        folder.mkdir(exist_ok=True)
        plan["history_file"] = "revision-%04d-%s.json" % (plan["revision"], uuid.uuid4().hex[:8])
        atomic_json(folder / plan["history_file"], plan)
        atomic_json(root / "improvement-tasks.json", plan)
        self.project(root, plan)
        return plan

    def create(self, identifier):
        with LOCK:
            plan = self.load(identifier)
            if plan["revision"]:
                return plan
            return self.commit(self.reviews.root(identifier), plan, "tasks_created")

    def save(self, identifier, payload):
        with LOCK:
            if not isinstance(payload, dict):
                raise ValueError("任务记录格式不正确")
            plan = self.load(identifier)
            if not plan["revision"]:
                raise ValueError("请先生成修改任务")
            if payload.get("revision") != plan["revision"] or payload.get("source_feedback_sha256") != plan["source_feedback_sha256"]:
                raise ValueError("任务版本已过期，请刷新后重新填写")
            task = next((x for x in plan["tasks"] if x["id"] == payload.get("task_id")), None)
            if task is None:
                raise ValueError("修改任务不存在")
            status = payload.get("status")
            if status not in TRANSITIONS[task["status"]]:
                raise ValueError("不能跳过修改和待验证阶段，请按顺序记录")
            clean = {}
            for key in ("implementation_note", "artifact_reference"):
                value = payload.get(key, "")
                if not isinstance(value, str) or len(value) > 10000:
                    raise ValueError("任务说明无效或过长")
                clean[key] = value.strip()
            if status in ("ready_for_validation", "validated", "needs_revision") and not all(clean.values()):
                raise ValueError("请填写修改说明和作品版本，再进入验证")
            if status == "blocked" and not clean["implementation_note"]:
                raise ValueError("等待条件时，请说明缺少什么")
            supplied = payload.get("validation", {})
            if status == "in_progress" and task["status"] in ("validated", "needs_revision", "ready_for_validation"):
                supplied = {}  # 回到修改阶段，旧版本的通过结论只能留在历史记录中。
            if not isinstance(supplied, dict) or supplied.get("outcome", "not_tested") not in OUTCOMES:
                raise ValueError("验证结果无效")
            validation = empty_validation()
            validation["outcome"] = supplied.get("outcome", "not_tested")
            for key in ("tested_version", "method", "record"):
                value = supplied.get(key, "")
                if not isinstance(value, str) or len(value) > 20000:
                    raise ValueError("验证记录无效或过长")
                validation[key] = value.strip()
            if status in ("validated", "needs_revision"):
                expected = ("passed",) if status == "validated" else ("failed", "inconclusive")
                if validation["outcome"] not in expected or not all(validation[k] for k in ("tested_version", "method", "record")):
                    raise ValueError("请选择与状态一致的结果，并填写被测版本、方法和实际记录")
                if validation["tested_version"] != clean["artifact_reference"]:
                    raise ValueError("被测版本必须与当前作品版本一致")
                if task["status"] in ("validated", "needs_revision") and clean["artifact_reference"] != task["artifact_reference"]:
                    raise ValueError("更换作品版本后，请先返回修改中，再重新验证")
                validation["recorded_at"] = now()
            elif validation["outcome"] != "not_tested":
                raise ValueError("已有测试结论时，请选择验证通过或需要再修改")
            if status == "in_progress" and task["status"] in ("validated", "needs_revision", "ready_for_validation"):
                validation = empty_validation()
            task.update(clean, status=status, validation=validation)
            return self.commit(self.reviews.root(identifier), plan, "task_updated", task["id"])
