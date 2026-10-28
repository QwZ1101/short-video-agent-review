"""根据材料生成可解释任务计划。规则调度，非模型自主规划。"""
from platform_metrics import calculate_metrics


def build_plan(sample, budget):
    data = calculate_metrics(sample.get("platform_metrics"))
    specs = [
        ("materials", "检查材料", "material_checker", "check_materials.py", [], ["sample"], ["materials_report"]),
        ("video", "提取视频证据", "video_tool", "prepare_video.py", ["materials"], ["sample"], ["video_evidence"]),
        ("metrics", "计算平台数据", "data_calculator", "platform_metrics.calculate_metrics", ["materials"], ["platform_metrics"], ["metrics_result"]),
        ("analysis", "内容分析", "content_reviewer", "review_tutorial.py", ["video", "metrics"], ["video_evidence", "metrics_result"], ["review", "evidence_catalog"]),
        ("verification", "证据验证", "evidence_verifier", "verify_tutorial.py", ["analysis"], ["review", "evidence_catalog"], ["verified_candidate"]),
    ]
    tasks = []
    for identifier, name, role, tool, deps, inputs, outputs in specs:
        skip = identifier == "metrics" and data["status"] == "skipped"
        tasks.append({"id": identifier, "name": name, "role": role, "tool": tool, "depends_on": deps,
            "inputs": inputs, "outputs": outputs, "action": "skip" if skip else "execute",
            "reason": data["reason"] if skip else "提供数据，使用确定性计算" if identifier == "metrics" else "完成复盘与证据核对所需步骤",
            "model_calls": 1 if identifier in ("analysis", "verification") else 0})
    return {"schema_version": 1, "planner": "rules_v1", "sample_id": sample["sample_id"],
        "goal_revision": sample.get("goal_revision"), "goal": sample["tutorial_goal"],
        "model_stage_budget": budget, "required_model_stages": 2,
        "ready": budget >= 2, "blocked_reason": "" if budget >= 2 else "完整分析与验证至少需要两个模型阶段预算",
        "execution_policy": "sequential_with_conditional_skip", "tasks": tasks,
        "human_review_required": True,
        "limitations": ["规则决定分支；可选返修由主流程显式启用，非模型自主规划", "缺少真实数据时跳过计算，不补造指标"]}


if __name__ == "__main__":
    import argparse
    import json
    from datetime import datetime
    from check_materials import PROJECT_ROOT, load_sample, resolve_path
    parser = argparse.ArgumentParser(description="只生成任务计划，不抽帧、不调用模型")
    parser.add_argument("--sample", required=True)
    parser.add_argument("--max-model-calls", type=int, choices=(0, 1, 2), default=2)
    args = parser.parse_args()
    plan = build_plan(load_sample(resolve_path(args.sample, PROJECT_ROOT)), args.max_model_calls)
    root = PROJECT_ROOT / "reports/plans" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True)
    (root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(root / "plan.json"))
