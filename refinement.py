"""验证反馈驱动的有界修正循环；证据不足、预算不足、失败都停止。"""
from pathlib import Path

from run_workflow import check_saved_verification, execute_stage, read_json, run_script, save_state
from review_tutorial import write_json
from verify_tutorial import load_review_run
from revise_review import repair_tasks, validate_revision
from feedback_policy import route_feedback
from workflow_definition import load_template


STOP_REASONS = {
    "awaiting_evidence": "验证标记了证据不足，停止模型调用，等待补证及人工复核。",
    "supported": "本轮检查在所提供证据内支持，仍需人工复核；验证员生成的候选改稿不能视为再次验证过。",
    "round_limit": "达到返修轮数上限，保留未解决项供人工复核。",
    "budget_limit": "剩余预算不足以完成一次修正和再次验证，未开始新一轮。",
    "no_change": "返修稿未改变总结或建议，停止重复调用，保留原验证结果。",
}


def decide(tasks, rounds, maximum, remaining):
    return route_feedback(tasks, rounds, maximum, remaining)['action']


def run_refinement_loop(root, state, paths, model, max_rounds):
    if type(max_rounds) is not int or not 0 <= max_rounds <= 2:
        raise ValueError("返修最多允许 0 到 2 轮")
    definition = state.get('plan', {}).get('workflow_definition') or load_template()
    max_rounds = min(max_rounds, definition['feedback_policy']['max_rounds'])
    state["refinement"] = {"status": "running", "max_rounds": max_rounds, "rounds_started": 0,
                           "rounds": [], "policy": "stop_on_any_insufficient_evidence"}
    control = state["refinement"]
    try:
        while True:
            original, bundle, result, _ = check_saved_verification(Path(paths["verification_dir"]))
            tasks = repair_tasks(result)
            control["tasks"] = tasks
            decision = route_feedback(tasks, control["rounds_started"], max_rounds,
                                      state["model_stage_budget"] - state["model_stage_attempts"])
            control.setdefault('decisions', []).append(decision)
            save_state(root, state, 'feedback_routed', **decision)
            gate = decision['action']
            if gate != "repair":
                control.update(status=gate, stop_reason=STOP_REASONS[gate])
                write_json(root / "repair-tasks.json", {"status": gate, "tasks": tasks, "reason": STOP_REASONS[gate]})
                save_state(root, state, "refinement_stopped", reason=gate)
                return paths
            number = control["rounds_started"] + 1
            repair_id, verify_id = "repair-%d" % number, "reverify-%d" % number
            record = {"round": number, "source_analysis_dir": paths["analysis_dir"],
                      "source_verification_dir": paths["verification_dir"], "tasks": tasks, "status": "running"}
            control["rounds"].append(record)
            control["rounds_started"] = number
            for identifier, name in ((repair_id, "第%d轮修正" % number), (verify_id, "第%d轮再次验证" % number)):
                state["stages"].append({"id": identifier, "name": name, "status": "pending"})
            save_state(root, state, "refinement_round_started", round=number)

            def revise():
                output = root / "refinement" / ("round-%d" % number) / "analysis"
                run_script(root, repair_id, "revise_review.py", ["--verification-dir", paths["verification_dir"],
                           "--output-dir", str(output), "--model", model])
                directory = Path(read_json(output / "latest.json")["run_dir"])
                revised, same_bundle, _, _ = load_review_run(directory)
                if same_bundle != bundle:
                    raise ValueError("返修不得更换原始证据")
                validate_revision(revised, original, bundle, tasks)
                record["analysis_dir"] = str(directory)
                return {"analysis_dir": str(directory)}
            artifacts = execute_stage(root, state, repair_id, revise, model_stage=True)
            revised = read_json(Path(artifacts["analysis_dir"]) / "review.json")
            if revised["summary"] == original["summary"] and revised["findings"] == original["findings"]:
                state["stages"][-1].update(status="skipped", reason="正文无变化，停止重复验证")
                record["status"] = "no_change"
                control.update(status="no_change", stop_reason=STOP_REASONS["no_change"])
                write_json(root / "repair-tasks.json", {"status": "no_change", "tasks": tasks, "reason": STOP_REASONS["no_change"]})
                save_state(root, state, "refinement_stopped", reason="no_change")
                return paths

            def reverify():
                output = root / "refinement" / ("round-%d" % number) / "verification"
                run_script(root, verify_id, "verify_tutorial.py", ["--review-dir", artifacts["analysis_dir"],
                           "--output-dir", str(output), "--model", model])
                directory = Path(read_json(output / "latest.json")["run_dir"])
                verified_input, verified_bundle, _, _ = check_saved_verification(directory)
                if verified_input != revised or verified_bundle != bundle:
                    raise ValueError("再次验证的输入与本轮返修稿不一致")
                return {"verification_dir": str(directory)}
            verified = execute_stage(root, state, verify_id, reverify, model_stage=True)
            paths.update(artifacts, **verified)
            record.update(status="completed", verification_dir=verified["verification_dir"])
            save_state(root, state, "refinement_round_completed", round=number)
    except Exception as error:
        control.update(status="failed", stop_reason="返修或再次验证失败；保留原始文件，不自动重试。")
        save_state(root, state, "refinement_failed", error=str(error))
        raise
