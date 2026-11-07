"""规则编排与数据条件分支，明确总调用预算，保留人工复核入口。

live 重新检查材料和取帧，再分析、验证；replay 只回放保存的真实结果。
这不是模型自主规划器；可显式启用有预算上限的故障重试，不自动接受候选稿。
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from check_materials import PROJECT_ROOT, load_sample, resolve_path
from review_tutorial import render_report, validate_review, write_json
from verify_tutorial import load_review_run, render_verification, validate_verification
from workflow_plan import build_plan
from workflow_definition import load_template, template_identity
from platform_metrics import calculate_metrics
from local_storage import atomic_json


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def now():
    return datetime.now(timezone.utc).isoformat()


def new_state(mode, budget, sample_id):
    names = {"live": [('materials', '检查材料'), ('video', '提取视频证据'), ('analysis', '内容分析'), ('verification', '证据验证')],
             "replay": [('import', '导入已有真实结果'), ('verification', '检查历史结果结构及来源')],
             "verify": [('source', '核对并复用已有分析'), ('verification', '重新执行证据验证')]}[mode]
    return {"schema_version": 1, "mode": mode, "sample_id": sample_id, "status": "running", "started_at": now(),
            "model_stage_budget": budget, "model_stage_attempts": 0,
            "stages": [{"id": key, "name": name, "status": "pending"} for key, name in names],
            "data_analysis": {"status": "skipped", "reason": "本轮尚未执行数据计算；回放仅保留历史证据"},
            "human_review": {"status": "pending"}}


def save_state(root, state, event, **details):
    # 同一任务不并行写状态；先写临时文件再替换，避免半份 JSON。
    atomic_json(root / "workflow.json", state)
    with (root / "events.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(dict(time=now(), event=event, **details), ensure_ascii=False) + "\n")


def execute_stage(root, state, identifier, action, model_stage=False):
    index = next(i for i, step in enumerate(state["stages"]) if step["id"] == identifier)
    step = state["stages"][index]
    if state["status"] != "running" or step["status"] != "pending" or any(
            item["status"] != "completed" and not (item["id"] == "metrics" and
            item["status"] == "skipped" and item.get("execution") == "conditional_no_data")
            for item in state["stages"][:index]):
        raise ValueError("阶段依赖未完成或阶段已经执行：" + identifier)
    step.update(status="running", started_at=now())
    save_state(root, state, "stage_started", stage=identifier)
    try:
        if model_stage:
            if state["model_stage_attempts"] >= state["model_stage_budget"]:
                raise ValueError("达到模型阶段次数上限，停止后续调用")
            state["model_stage_attempts"] += 1
            save_state(root, state, "model_budget_reserved", stage=identifier)
        artifacts = action()
        step.update(status="completed", finished_at=now(), artifacts=artifacts)
        save_state(root, state, "stage_completed", stage=identifier)
        return artifacts
    except Exception as error:
        step.update(status="failed", finished_at=now(), error=str(error))
        for remaining in state["stages"][index + 1:]:
            remaining.update(status="skipped", reason="前置阶段失败，未执行")
        state.update(status="failed", stop_reason=str(error), finished_at=now())
        save_state(root, state, "stage_failed", stage=identifier, error=str(error))
        raise


def run_script(root, stage, script, arguments, timeout=240):
    plan_path = root / 'plan.json'
    if plan_path.is_file():
        identity = read_json(plan_path).get('template_identity')
        if identity and identity != template_identity(load_template()):
            raise ValueError('工作流模板或角色提示词在本轮运行期间发生变化，请重新生成计划')
    log_path = root / (stage + ".log")
    print("正在运行：" + stage, flush=True)
    with log_path.open("a", encoding="utf-8") as log:
        result = subprocess.run([sys.executable, str(PROJECT_ROOT / script)] + arguments,
                                cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise ValueError("%s 阶段退出码 %s，详情见 %s" % (stage, result.returncode, log_path))


def check_saved_verification(directory):
    metadata = read_json(directory / "run.json")
    if metadata.get("status") != "completed_structure_valid":
        raise ValueError("只能回放已完成结构检查的真实验证结果")
    for filename, field in (("original-review.json", "review_sha256"), ("evidence-catalog.json", "evidence_sha256")):
        if hashlib.sha256((directory / filename).read_bytes()).hexdigest() != metadata.get(field):
            raise ValueError("历史初稿或证据目录已改变：" + filename)
    original = read_json(directory / "original-review.json")
    bundle = read_json(directory / "evidence-catalog.json")
    validate_review(original, bundle)
    result = read_json(directory / "verification.json")
    decision = validate_verification(result, original, bundle)
    if result["revised_review"] != read_json(directory / "revised-review.json"):
        raise ValueError("候选稿与验证结果不一致")
    return original, bundle, result, dict(metadata, decision=decision)


def run_replay(root, state, source):
    paths = {}

    def import_saved():
        original, bundle, _, _ = check_saved_verification(source)
        stage_dir = root / "analysis"
        stage_dir.mkdir()
        write_json(stage_dir / "review.json", original)
        write_json(stage_dir / "evidence-catalog.json", bundle)
        load_review_run(stage_dir)  # 验证每张图片哈希，不调用模型。
        destination = root / "verification"
        destination.mkdir()
        for name in ("original-review.json", "evidence-catalog.json", "verification.json", "revised-review.json", "run.json", "prompt.md", "model-output.txt"):
            shutil.copyfile(source / name, destination / name)
        paths.update(analysis_dir=str(stage_dir), verification_dir=str(destination), source_verification_dir=str(source))
        return dict(paths)

    execute_stage(root, state, "import", import_saved)

    def inspect_saved():
        original, bundle, result, metadata = check_saved_verification(Path(paths["verification_dir"]))
        state["sample_id"] = original["sample_id"]
        state["platform_metrics_provided"] = bundle["task"].get("platform_metrics") is not None
        (Path(paths["verification_dir"]) / "verification.md").write_text(render_verification(result, original, bundle, metadata), encoding="utf-8")
        return {"decision": metadata["decision"], "execution": "replayed_saved_result_no_api"}

    execute_stage(root, state, "verification", inspect_saved)
    return paths


def run_live(root, state, sample_path, model, max_repair_rounds=0, model_retry_limit=0):
    if type(model_retry_limit) is not int or model_retry_limit not in (0, 1):
        raise ValueError('每个模型阶段最多重试一次')
    if type(max_repair_rounds) is not int or not 0 <= max_repair_rounds <= 2:
        raise ValueError("返修最多允许 0 到 2 轮")
    sample = load_sample(sample_path)
    state["sample_id"] = sample["sample_id"]
    state["platform_metrics_provided"] = sample.get("platform_metrics") is not None
    # 保存任务单快照供复核；代码仍使用原任务单中的项目相对材料路径。
    snapshot = root / "sample.json"
    write_json(snapshot, sample)
    paths = {}
    plan = build_plan(sample, state["model_stage_budget"])
    plan["max_repair_rounds"] = max_repair_rounds
    plan["repair_policy"] = "遇到证据不足停止；每轮修正和再次验证需要两个剩余调用预算"
    state["plan"] = plan
    state['model_retry_limit'] = model_retry_limit
    plan['model_retry_limit'] = model_retry_limit
    write_json(root / "plan.json", plan)
    state["stages"] = [{"id": task["id"], "name": task["name"], "status": "pending"} for task in plan["tasks"]]
    save_state(root, state, "plan_created", planner=plan["planner"], ready=plan["ready"])
    if not plan["ready"]:
        raise ValueError(plan["blocked_reason"])

    def materials():
        destination = root / "materials"
        run_script(root, "materials", "check_materials.py", ["--sample", str(snapshot), "--output-dir", str(destination)])
        return {"report": str(destination / "materials-report.md")}
    execute_stage(root, state, "materials", materials)

    def video():
        destination = root / "video"
        run_script(root, "video", "prepare_video.py", ["--sample", str(snapshot), "--output-dir", str(destination)], timeout=600)
        paths["video_evidence"] = read_json(destination / "latest.json")["data_path"]
        return {"video_evidence": paths["video_evidence"]}
    execute_stage(root, state, "video", video)

    def metrics():
        result = calculate_metrics(sample.get("platform_metrics"))
        destination = root / "metrics.json"
        write_json(destination, result)
        paths["metrics_result"] = str(destination)
        state["data_analysis"] = {"status": result["status"], "reason": result.get("reason", "已计算；仅使用用户录入数据"),
                                  "report": str(destination), "execution": "deterministic_no_model_call"}
        return {"metrics_result": str(destination)}
    if next(task for task in plan["tasks"] if task["id"] == "metrics")["action"] == "skip":
        artifacts = metrics()
        step = next(stage for stage in state["stages"] if stage["id"] == "metrics")
        step.update(status="skipped", reason=state["data_analysis"]["reason"], execution="conditional_no_data", artifacts=artifacts)
        save_state(root, state, "stage_skipped", stage="metrics", reason=step["reason"])
    else:
        execute_stage(root, state, "metrics", metrics)

    def analysis():
        destination = root / "analysis"
        run_script(root, "analysis", "review_tutorial.py", ["--sample", str(snapshot), "--video-evidence", paths["video_evidence"],
                   "--output-dir", str(destination), "--model", model])
        paths["analysis_dir"] = read_json(destination / "latest.json")["run_dir"]
        return {"analysis_dir": paths["analysis_dir"]}
    def model_action(identifier, action):
        step = next(s for s in state['stages'] if s['id'] == identifier)
        step['attempts'] = []
        for attempt in range(1 + model_retry_limit):
            started = now()
            try:
                result = action()
                step['attempts'].append(dict(number=attempt+1,status='completed',started_at=started,finished_at=now()))
                return result
            except Exception:
                candidates = sorted((root/identifier).glob('run-*/run.json'), reverse=True)
                meta = read_json(candidates[0]) if candidates else {}
                kind = meta.get('error_kind')
                step['attempts'].append(dict(number=attempt+1,status='failed',error_kind=kind,
                    started_at=started,finished_at=now(),metadata=str(candidates[0]) if candidates else None))
                # Reserve one call for verification when retrying analysis. Never retry local/preflight failures.
                reserve = 1 if identifier == 'analysis' else 0
                retryable = kind in ('network_or_incomplete_stream','invalid_report_envelope','missing_report_fields','invalid_model_output')
                if attempt >= model_retry_limit or not retryable or state['model_stage_budget'] - state['model_stage_attempts'] <= reserve:
                    raise
                state['model_stage_attempts'] += 1
                state['retry_count'] = state.get('retry_count',0) + 1
                save_state(root,state,'model_stage_retry_reserved',stage=identifier,attempt=attempt+2,error_kind=kind,
                    reason='故障恢复：'+kind+'；开始本阶段第 '+str(attempt+2)+' 次尝试，计入总预算',
                    reused_analysis=identifier=='verification')
                time.sleep(1)
    execute_stage(root, state, "analysis", lambda: model_action('analysis',analysis), model_stage=True)

    def verification():
        destination = root / "verification"
        run_script(root, "verification", "verify_tutorial.py", ["--review-dir", paths["analysis_dir"], "--output-dir", str(destination), "--model", model])
        paths["verification_dir"] = read_json(destination / "latest.json")["run_dir"]
        return {"verification_dir": paths["verification_dir"]}
    execute_stage(root, state, "verification", lambda: model_action('verification',verification), model_stage=True)
    if max_repair_rounds:
        from refinement import run_refinement_loop
        run_refinement_loop(root, state, paths, model, max_repair_rounds)
    return paths


def run_verify_only(root, state, source_dir, model):
    paths = {}

    def reuse():
        original, bundle, _, fingerprints = load_review_run(source_dir)
        state["sample_id"] = original["sample_id"]
        state["platform_metrics_provided"] = bundle["task"].get("platform_metrics") is not None
        destination = root / "analysis" / "reused"
        destination.mkdir(parents=True)
        for name in ("review.json", "evidence-catalog.json", "run.json", "prompt.md", "model-output.txt"):
            if (source_dir / name).is_file():
                shutil.copyfile(source_dir / name, destination / name)
        paths.update(analysis_dir=str(destination), source_analysis_dir=str(source_dir))
        return dict(paths, **fingerprints, execution="reused_analysis_no_model_call")
    execute_stage(root, state, "source", reuse)

    def verify():
        output = root / "verification"
        run_script(root, "verification", "verify_tutorial.py", ["--review-dir", paths["analysis_dir"], "--model", model, "--output-dir", str(output)])
        paths["verification_dir"] = read_json(output / "latest.json")["run_dir"]
        return {"verification_dir": paths["verification_dir"]}
    execute_stage(root, state, "verification", verify, model_stage=True)
    return paths


def create_handoff(root, state, paths):
    directory = Path(paths["verification_dir"])
    original, bundle, result, metadata = check_saved_verification(directory)
    state.update(status="awaiting_human_review", verification_decision=metadata["decision"], artifacts=paths,
                 stop_reason="模型输出可能有误；本版所有候选稿都进入人工复核，不自动接受或返修。", finished_at=now())
    if state.get("refinement"):
        state["stop_reason"] = state["refinement"]["stop_reason"]
    candidate = directory / "revised-review.json"
    state["human_review"] = {"status": "pending", "form": str(root / "human-review.json")}
    form = {"sample_id": original["sample_id"], "workflow_dir": str(root), "candidate_path": str(candidate),
            "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "status": "pending", "summary": {"decision": "pending", "note": ""},
            "findings": [{"id": item["id"], "decision": "pending", "note": ""} for item in result["revised_review"]["findings"]],
            "instructions": "双击启动复核工作台.cmd，在页面逐项复核并保存或提交。该文件保留初始候选稿绑定；手工编辑此文件不会自动接受候选稿。实际反馈另存 human-feedback.json。"}
    write_json(root / "human-review.json", form)
    # 原始模型、验证和人工输入分别留存。
    analysis_meta_path = Path(paths["analysis_dir"]) / "run.json"
    analysis_meta = read_json(analysis_meta_path) if analysis_meta_path.is_file() else {}
    model = analysis_meta.get("model", "历史初稿（该快照未单独记录其模型）")
    (root / "original-review.md").write_text(render_report(original, bundle, {"model": model}), encoding="utf-8")
    mode_label = {"replay": "历史真实结果回放（本轮未调用模型）", "live": "本轮重新执行材料检查、取帧、分析和验证",
                  "resume": "只恢复失败的验证阶段，前置步骤复用原流程结果",
                  "refine": "复用已完成的分析和验证，按反馈执行有上限的语义返修或停止等待补证",
                  "verify": "复用已有分析与原始证据，真实执行一次新的验证"}[state["mode"]]
    lines = ["# 复盘流程总览", "", "**当前状态：等待人工复核。候选稿尚未接受。**", "",
             "运行方式：" + mode_label, "", "样本：" + state["sample_id"], "",
             "模型阶段尝试次数：%s / %s（阶段启动数，不等于服务端计费用量）。" % (state["model_stage_attempts"], state["model_stage_budget"]), "",
             "| 步骤 | 执行状态 |", "| --- | --- |"]
    lines.extend("| %s | %s |" % (stage["name"], "复用原流程成功结果" if stage.get("execution") == "reused_from_parent_no_rerun" else stage["status"]) for stage in state["stages"])
    if state.get("parent_workflow") and state["mode"] == "resume":
        lines.extend(["", "原流程已尝试 %s 个模型阶段，本次恢复单独限定 1 次。原失败记录保留：[%s](<%s>)。" % (
            state["parent_model_stage_attempts"], "原流程报告", (Path(state["parent_workflow"]) / "workflow.md").as_posix())])
    if state.get("refinement"):
        control = state["refinement"]
        lines.extend(["", "## 受控返修", "", control["stop_reason"], "",
            "已开始返修轮数：%s / %s。各轮输入、反馈和输出均保留，详情见 workflow.json 的 refinement。" % (control["rounds_started"], control["max_rounds"])])
        if state.get("parent_workflow"):
            lines.extend(["", "来源流程已尝试 %s 次，本轮追加尝试 %s 次，总计 %s 次阶段尝试。" % (
                state["parent_model_stage_attempts"], state["model_stage_attempts"], state["parent_model_stage_attempts"] + state["model_stage_attempts"])])
    data_note = "已有本地计算工具汇总，位置见 workflow.json 的 data_analysis.report；数据本身为用户录入。" if state.get("data_analysis", {}).get("status") == "completed" else "未提供，跳过数据比较。" if not state["platform_metrics_provided"] else "已有数据输入，但本轮未执行数据计算。"
    lines.extend(["", "后台数据：" + data_note, "",
                  "验证角色判定：`" + metadata["decision"] + "`。即使模型均判定支持，本版也保留人工复核。", "",
                  "## 查看结果", "",
                  "- [原始分析](<%s>)" % (root / "original-review.md").as_posix(),
                  "- [验证理由与修改前后对照](<%s>)" % (directory / "verification.md").as_posix(),
                  "- [人工反馈表](<%s>)" % (root / "human-review.json").as_posix(),
                  "- [运行状态与停止理由](<%s>)" % (root / "workflow.json").as_posix(), "",
                  "## 复核入口", "", "双击项目目录的启动复核工作台.cmd。先读验证报告，再检查候选稿是否真的修正了问题。人工反馈默认全部 pending；在页面中保存草稿或提交，实际反馈另存 human-feedback.json。", "",
                  "当前为规则编排；新 live 流程生成 plan.json 并执行数据条件分支，可显式启用有预算上限的修正与再次验证。证据不足时停止，所有候选结果保留人工复核；没有模型自主规划或发布操作。", ""])
    (root / "workflow.md").write_text("\n".join(lines), encoding="utf-8")
    save_state(root, state, "human_review_required", decision=metadata["decision"])


def write_failure_report(root, state):
    state["human_review"] = {"status": "unavailable", "reason": "流程失败，未产生本轮通过结构检查的验证候选稿"}
    lines = ["# 复盘流程停止报告", "", "**本轮未完成验证，不能将初稿当作已通过验证的结果。**", "",
             "停止原因：" + state["stop_reason"], "",
             "模型阶段尝试次数：%s / %s；每个阶段的额外重试上限为 %s。" % (state["model_stage_attempts"], state["model_stage_budget"], state.get('model_retry_limit',0)), "",
             "| 步骤 | 状态 |", "| --- | --- |"]
    lines.extend("| %s | %s |" % (item["name"], item["status"]) for item in state["stages"])
    lines.extend(["", "已经生成的文件和原始模型输出均保留。排查后是否重跑需要另行决定；本程序不会把旧结果补成新结果。", "",
                  "- [完整状态记录](<%s>)" % (root / "workflow.json").as_posix()])
    for item in state["stages"]:
        log = root / (item["id"] + ".log")
        if log.is_file():
            lines.append("- [%s日志](<%s>)" % (item["name"], log.as_posix()))
    lines.append("")
    (root / "workflow.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="运行教程复盘工作流，或回放已有真实模型结果")
    parser.add_argument("--mode", choices=("live", "replay", "verify"), default="replay")
    parser.add_argument("--review-dir", help="verify 模式必填：复用的成功分析目录")
    parser.add_argument("--sample", default="data/samples/tutorial-001.json")
    parser.add_argument("--verification-dir", help="回放的验证结果目录；默认最近一次独立验证结果")
    parser.add_argument("--max-model-calls", type=int, choices=range(7), default=2)
    parser.add_argument("--max-repair-rounds", type=int, choices=(0, 1, 2), default=0,
                        help="仅 live：最多返修轮数，每轮修正+再次验证消耗两个总预算名额；默认不启用")
    parser.add_argument("--model", default=os.environ.get("DASHSCOPE_MODEL") or "qwen3.8-flash")
    args = parser.parse_args()
    if args.mode == "verify" and not args.review_dir:
        parser.error("verify 模式需要 --review-dir")
    if args.max_repair_rounds and args.mode != "live":
        parser.error("只有 live 支持 --max-repair-rounds；已有结果使用 refine_workflow.py")
    root = PROJECT_ROOT / "reports/workflows" / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    root.mkdir(parents=True)
    budget = 0 if args.mode == "replay" else min(args.max_model_calls, 1) if args.mode == "verify" else args.max_model_calls
    state = new_state(args.mode, budget, None)
    save_state(root, state, "workflow_started", mode=args.mode)
    try:
        if args.mode == "live":
            paths = run_live(root, state, resolve_path(args.sample, PROJECT_ROOT), args.model, args.max_repair_rounds)
        elif args.mode == "verify":
            paths = run_verify_only(root, state, resolve_path(args.review_dir, PROJECT_ROOT).resolve(), args.model)
        else:
            source = args.verification_dir or read_json(PROJECT_ROOT / "reports/verification/latest.json")["run_dir"]
            paths = run_replay(root, state, resolve_path(source, PROJECT_ROOT))
        create_handoff(root, state, paths)
        write_json(root.parent / "latest.json", {"run_dir": str(root), "report_path": str(root / "workflow.md"), "mode": args.mode, "status": state["status"]})
        print("流程已到人工复核环节：" + str(root / "workflow.md"))
        return 0
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as error:
        state.update(status="failed", stop_reason=str(error), finished_at=now())
        for stage in state["stages"]:
            if stage["status"] == "pending":
                stage.update(status="skipped", reason="流程提前停止")
        write_failure_report(root, state)
        save_state(root, state, "workflow_failed", error=str(error))
        print("流程停止：%s\n阅读失败报告：%s" % (error, root / "workflow.md"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
