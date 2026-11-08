"""根据材料生成可解释任务计划。规则调度，非模型自主规划。"""
from platform_metrics import calculate_metrics
from workflow_definition import load_template, template_identity


def build_plan(sample, budget):
    data = calculate_metrics(sample.get("platform_metrics"))
    template = load_template()
    specs = [(t["id"], t["name"], t["role"], t["tool"], t["depends_on"], t["inputs"], t["outputs"]) for t in template["tasks"]]
    tasks = []
    for identifier, name, role, tool, deps, inputs, outputs in specs:
        skip = identifier == "metrics" and data["status"] == "skipped"
        tasks.append({"id": identifier, "name": name, "role": role, "tool": tool, "depends_on": deps,
            "inputs": inputs, "outputs": outputs, "action": "skip" if skip else "execute",
            "reason": data["reason"] if skip else "提供数据，使用确定性计算" if identifier == "metrics" else "完成复盘与证据核对所需步骤",
            "priority": next(t.get("priority", 0) for t in template["tasks"] if t["id"] == identifier),
            "model_calls": 1 if identifier in ("analysis", "verification") else 0})
    return {"schema_version": 1, "planner": "validated_template_v1", "workflow_definition": template,
        "template_identity": template_identity(template), "sample_id": sample["sample_id"],
        "goal_revision": sample.get("goal_revision"), "goal": sample["tutorial_goal"],
        "model_stage_budget": budget, "required_model_stages": 2, "platform_data": data,
        "ready": budget >= 2, "blocked_reason": "" if budget >= 2 else "完整分析与验证至少需要两个模型阶段预算",
        "execution_policy": "dependency_ready_priority_role_capacity", "tasks": tasks,
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
