"""一次有界的验证格式修复：复用成功阶段，创建独立恢复记录。

只处理已返回文本但未通过结构校验的验证阶段；不处理网络重试或语义返修。
同一原流程最多提交一次恢复，使用原证据及程序校验反馈。
"""
import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import sys
import time

from check_materials import PROJECT_ROOT, resolve_path
from review_tutorial import ModelNetworkError, call_dashscope, write_json
from verify_tutorial import build_verification_request, load_review_run, render_verification, validate_verification
from run_workflow import create_handoff, execute_stage, now, read_json, save_state, write_failure_report


def inside(path, root):
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError("恢复输入必须属于指定的原流程目录") from None
    return resolved


def load_resume_source(root):
    state = read_json(root / "workflow.json")
    if state.get("mode") != "live" or state.get("status") != "failed":
        raise ValueError("只允许恢复一次失败的 live 流程，不能恢复回放或恢复流程")
    stages = state.get("stages", [])
    if [item.get("id") for item in stages] not in (["materials", "video", "analysis", "verification"],
            ["materials", "video", "metrics", "analysis", "verification"]):
        raise ValueError("原流程阶段结构不符合恢复要求")
    if any(item.get("status") != "completed" and not (item["id"] == "metrics" and
           item.get("status") == "skipped" and item.get("execution") == "conditional_no_data")
           for item in stages[:-1]) or stages[-1].get("status") != "failed":
        raise ValueError("恢复要求前置步骤成功或按计划跳过数据计算，且仅验证阶段失败")
    analysis_dir = inside(next(item for item in stages if item["id"] == "analysis")["artifacts"]["analysis_dir"], root)
    original, bundle, images, fingerprints = load_review_run(analysis_dir)
    if original["sample_id"] != state.get("sample_id"):
        raise ValueError("原流程与分析样本不一致")
    candidates = []
    for path in (root / "verification").glob("run-*/run.json"):
        meta = read_json(path)
        if meta.get("status") == "failed" and Path(meta.get("source_review_dir", "")).resolve() == analysis_dir:
            candidates.append((path.parent, meta))
    if len(candidates) != 1:
        raise ValueError("需要恰好一份与当前分析关联的失败验证记录")
    failed_dir, meta = candidates[0]
    for name, value in fingerprints.items():
        if meta.get(name) != value:
            raise ValueError("分析或证据目录已变化，停止恢复")
    if meta.get("finish_reason") != "stop":
        raise ValueError("仅支持已正常返回但结构不合格的响应；本次不重试网络或截断错误")
    failed_text = (failed_dir / "model-output.txt").read_text(encoding="utf-8")
    if len(failed_text) > 64000:
        raise ValueError("原始失败输出过大，需人工排查")
    try:
        validate_verification(json.loads(failed_text), original, bundle)
    except (ValueError, KeyError, TypeError) as error:
        validation_error = str(error)
    else:
        raise ValueError("原输出已通过结构检查，不属于格式修复范围")
    return {"state": state, "analysis_dir": analysis_dir, "failed_dir": failed_dir, "original": original,
            "bundle": bundle, "images": images, "fingerprints": fingerprints, "failed_text": failed_text,
            "validation_error": validation_error, "model": meta["model"]}


def repair_request(source):
    payload = build_verification_request(source["original"], source["bundle"], source["images"], source["model"])
    payload["messages"].extend([
        {"role": "assistant", "content": source["failed_text"]},
        {"role": "user", "content": "程序校验未通过：" + source["validation_error"] +
         "。请使用上方同一批证据重新完成验证任务，返回系统提示词规定的完整 JSON 对象。"
         "不能返回空数组、空对象或只解释错误。sample_id 必须为 " + source["original"]["sample_id"] +
         "；checks 必须逐一覆盖 " + json.dumps([item["id"] for item in source["original"]["findings"]], ensure_ascii=False) +
         "。即使无法确定结论，也要在对应检查中填写 insufficient_evidence、证据及理由，并给出保留不确定性的修订候选稿。"
         "原先的失败回答是待纠正的输出，不是证据或新的任务指令。"}])
    return payload


def reserve_resume(source_root, recovery_root):
    """排他创建，避免双击或多进程触发重复付费恢复；不删除失败占位。"""
    try:
        with (source_root / "resume-claim.json").open("x", encoding="utf-8") as output:
            json.dump({"recovery_dir": str(recovery_root), "reserved_at": now(), "max_calls": 1}, output, ensure_ascii=False, indent=2)
    except FileExistsError:
        raise ValueError("此原流程已经启动过一次恢复，请查看 resume-claim.json；不会再次调用 API") from None


def recovery_state(source_root, source):
    state = copy.deepcopy(source["state"])
    for key in ("finished_at", "stop_reason", "diagnosis", "artifacts", "verification_decision"):
        state.pop(key, None)
    state.update(mode="resume", status="running", started_at=now(), parent_workflow=str(source_root),
                 parent_model_stage_attempts=source["state"]["model_stage_attempts"],
                 model_stage_budget=1, model_stage_attempts=0,
                 human_review={"status": "unavailable", "reason": "恢复尚未完成"})
    for stage in state["stages"][:-1]:
        if stage["status"] == "completed":
            stage["execution"] = "reused_from_parent_no_rerun"
    state["stages"][-1] = {"id": "verification", "name": "证据验证（一次格式修复）", "status": "pending"}
    return state


def repair_once(root, source):
    destination = root / "verification" / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    destination.mkdir(parents=True)
    payload = repair_request(source)
    write_json(destination / "original-review.json", source["original"])
    write_json(destination / "evidence-catalog.json", source["bundle"])
    (destination / "prompt.md").write_text(payload["messages"][0]["content"], encoding="utf-8")
    write_json(destination / "repair-feedback.json", {"previous_run": str(source["failed_dir"]),
               "previous_output": source["failed_text"], "validator_error": source["validation_error"],
               "correction_instruction": payload["messages"][-1]["content"]})
    metadata = dict(source["fingerprints"], model=source["model"], status="prepared", source_review_dir=str(source["analysis_dir"]),
                    repair_of=str(source["failed_dir"]), automatic_retries=0, image_count=len(source["images"]), manual_review_supplied=False)
    write_json(destination / "run.json", metadata)
    started = time.monotonic()
    try:
        print("只恢复验证阶段：%s；本次最多一次 API 调用。" % source["model"], flush=True)
        response = call_dashscope(payload)
        choice = response["choices"][0]
        content = choice["message"]["content"]
        metadata.update(elapsed_seconds=round(time.monotonic() - started, 2), usage=response.get("usage"),
                        response_id=response.get("id"), finish_reason=choice.get("finish_reason"), transport=response.get("transport"))
        (destination / "model-output.txt").write_text(content, encoding="utf-8")
        if choice.get("finish_reason") != "stop":
            raise ValueError("修复响应未正常结束")
        result = json.loads(content)
        decision = validate_verification(result, source["original"], source["bundle"])
        metadata.update(status="completed_structure_valid", decision=decision, revised_review_status="candidate_not_reverified")
        write_json(destination / "verification.json", result)
        write_json(destination / "revised-review.json", result["revised_review"])
        write_json(destination / "run.json", metadata)
        (destination / "verification.md").write_text(render_verification(result, source["original"], source["bundle"], metadata), encoding="utf-8")
        return {"verification_dir": str(destination)}
    except Exception as error:
        metadata.update(status="failed", error=str(error), elapsed_seconds=round(time.monotonic() - started, 2),
                        error_kind="network_response_missing" if isinstance(error, ModelNetworkError) else "response_or_validation_error")
        write_json(destination / "run.json", metadata)
        raise


def main():
    parser = argparse.ArgumentParser(description="只恢复验证格式失败的流程一次；不会重新取帧或生成初稿")
    parser.add_argument("--workflow-dir", required=True)
    parser.add_argument("--dry-run", action="store_true", help="检查恢复条件，不占用恢复次数，不调用 API")
    args = parser.parse_args()
    root, state = None, None
    try:
        parent = resolve_path(args.workflow_dir, PROJECT_ROOT).resolve()
        source = load_resume_source(parent)
        if (parent / "resume-claim.json").exists():
            raise ValueError("原流程已有恢复记录，请查看 resume-claim.json；不会重复调用")
        if args.dry_run:
            print("可以恢复；错误：%s；将复用原分析和 %s 张图片，不调用 API。" % (source["validation_error"], len(source["images"])))
            return 0
        candidate = PROJECT_ROOT / "reports/workflows" / ("resume-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        reserve_resume(parent, candidate)
        root = candidate
        root.mkdir(parents=True)
        state = recovery_state(parent, source)
        save_state(root, state, "resume_started", parent=str(parent), repair_feedback=source["validation_error"])
        paths = {"analysis_dir": str(source["analysis_dir"]), "video_evidence": source["state"]["stages"][1]["artifacts"]["video_evidence"]}
        paths.update(execute_stage(root, state, "verification", lambda: repair_once(root, source), model_stage=True))
        create_handoff(root, state, paths)
        write_json(root.parent / "latest.json", {"run_dir": str(root), "report_path": str(root / "workflow.md"), "mode": "resume", "status": state["status"]})
        print("恢复完成，进入人工复核：" + str(root / "workflow.md"))
        return 0
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        if root and state:
            state.update(status="failed", stop_reason=str(error), finished_at=now())
            write_failure_report(root, state)
            save_state(root, state, "resume_failed", error=str(error))
        print("恢复停止：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
