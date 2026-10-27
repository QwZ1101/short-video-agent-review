"""为已有真实 live/verify 结果创建一次独立的有界语义返修流程。"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import subprocess

from check_materials import PROJECT_ROOT, resolve_path
from review_tutorial import write_json
from run_workflow import (check_saved_verification, create_handoff, execute_stage, now, read_json,
                          save_state, write_failure_report)
from verify_tutorial import load_review_run
from refinement import decide, run_refinement_loop
from revise_review import repair_tasks


def load_source(parent):
    parent = parent.resolve()
    parent.relative_to((PROJECT_ROOT / "reports/workflows").resolve())
    state = read_json(parent / "workflow.json")
    if state.get("mode") not in ("live", "verify") or state.get("status") != "awaiting_human_review" or state.get("refinement"):
        raise ValueError("仅允许尚未人工接受、未做过语义返修的 live/verify 流程；不能串联返修预算")
    if (parent / "human-feedback.json").exists() and read_json(parent / "human-feedback.json").get("status") == "accepted":
        raise ValueError("已有人工确认结果，不能自动返修")
    analysis = Path(state["artifacts"]["analysis_dir"]).resolve()
    verification = Path(state["artifacts"]["verification_dir"]).resolve()
    analysis.relative_to(parent)
    verification.relative_to(parent)
    original, bundle, _, _ = load_review_run(analysis)
    verified_input, verified_bundle, result, meta = check_saved_verification(verification)
    if original != verified_input or bundle != verified_bundle or original["sample_id"] != state["sample_id"]:
        raise ValueError("原分析与验证的样本或证据不匹配")
    current = PROJECT_ROOT / "data/samples" / (state["sample_id"] + ".json")
    if current.is_file():
        sample = read_json(current)
        if sample.get("tutorial_goal") != bundle["task"].get("tutorial_goal") or sample.get("goal_revision") != bundle["task"].get("goal_revision"):
            raise ValueError("当前目标已经变化，请重新运行分析，不能用旧目标结果返修")
    return state, analysis, verification, result, meta


def reserve(parent, root):
    try:
        with (parent / "refinement-claim.json").open("x", encoding="utf-8") as output:
            json.dump({"created_at": now(), "refinement_dir": str(root)}, output)
    except FileExistsError:
        raise ValueError("该流程已有返修记录，请查看 refinement-claim.json；不会重复调用") from None


def main():
    parser = argparse.ArgumentParser(description="继续已有真实结果，保留原档案，不允许无限追加预算")
    parser.add_argument("--workflow-dir", required=True)
    parser.add_argument("--max-repair-rounds", type=int, choices=(1, 2), default=1)
    parser.add_argument("--max-model-calls", type=int, choices=range(5), default=2, help="本次追加预算，默认2、最多4")
    parser.add_argument("--dry-run", action="store_true", help="只检查调度决定，不占用继续次数或调用模型")
    args = parser.parse_args()
    root, state = None, None
    try:
        parent = resolve_path(args.workflow_dir, PROJECT_ROOT).resolve()
        previous, analysis, verification, result, meta = load_source(parent)
        if (parent / "refinement-claim.json").exists():
            raise ValueError("该流程已经创建过返修记录，不重复执行")
        gate = decide(repair_tasks(result), 0, args.max_repair_rounds, args.max_model_calls)
        if args.dry_run:
            print(json.dumps({"decision": gate, "additional_call_budget": args.max_model_calls,
                              "max_rounds": args.max_repair_rounds, "api_called": False}, ensure_ascii=False))
            return 0
        root = PROJECT_ROOT / "reports/workflows" / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        reserve(parent, root)
        root.mkdir(parents=True)
        state = {"schema_version": 1, "mode": "refine", "sample_id": previous["sample_id"], "status": "running",
            "started_at": now(), "model_stage_budget": args.max_model_calls, "model_stage_attempts": 0,
            "parent_workflow": str(parent), "parent_model_stage_attempts": previous["model_stage_attempts"],
            "platform_metrics_provided": previous.get("platform_metrics_provided", False),
            "stages": [{"id": "import", "name": "核对并复制已有结果", "status": "pending"}],
            "human_review": {"status": "pending"}}
        save_state(root, state, "refinement_workflow_started", parent=parent.name)
        paths = {}
        def import_source():
            for source, name in ((analysis, "analysis"), (verification, "verification")):
                destination = root / name
                destination.mkdir()
                for filename in ("review.json", "original-review.json", "evidence-catalog.json", "verification.json",
                                 "revised-review.json", "run.json", "prompt.md", "model-output.txt", "verification.md"):
                    if (source / filename).is_file():
                        shutil.copyfile(source / filename, destination / filename)
                paths[name + "_dir"] = str(destination)
            load_review_run(Path(paths["analysis_dir"]))
            check_saved_verification(Path(paths["verification_dir"]))
            return dict(paths)
        execute_stage(root, state, "import", import_source)
        run_refinement_loop(root, state, paths, meta["model"], args.max_repair_rounds)
        create_handoff(root, state, paths)
        write_json(root.parent / "latest.json", {"run_dir": str(root), "report_path": str(root / "workflow.md"),
                                               "mode": "refine", "status": state["status"]})
        print(state["refinement"]["stop_reason"] + "\n" + str(root / "workflow.md"))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired) as error:
        if root and state:
            state.update(status="failed", stop_reason=str(error), finished_at=now())
            write_failure_report(root, state)
            save_state(root, state, "workflow_failed", error=str(error))
        print("返修流程停止：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
