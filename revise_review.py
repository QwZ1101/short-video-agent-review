"""按验证反馈修正初稿一次。证据不增加，结果必须再次独立验证。"""
from workflow_definition import role_prompt

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from check_materials import PROJECT_ROOT, resolve_path
from review_tutorial import ModelNetworkError, build_request, call_dashscope, validate_review, write_json
from model_output import parse_model_json
from verify_tutorial import load_review_run
from run_workflow import check_saved_verification


def repair_tasks(result):
    return [{"target": "summary", **result["summary_check"]}] + [
        {"target": item["finding_id"], **{k: item[k] for k in ("status", "reason", "evidence_ids")}}
        for item in result["checks"]]


def validate_revision(revised, original, bundle, tasks):
    validate_review(revised, bundle)
    if {f["id"] for f in revised["findings"]} != {f["id"] for f in original["findings"]}:
        raise ValueError("返修必须保留所有原建议 ID，不能新增或删除")
    supported = {t["target"] for t in tasks if t["status"] == "supported"}
    if "summary" in supported and revised["summary"] != original["summary"]:
        raise ValueError("不得改写验证已支持的总结")
    new_items = {f["id"]: f for f in revised["findings"]}
    for item in original["findings"]:
        if item["id"] in supported and new_items[item["id"]] != item:
            raise ValueError("不得改写验证已支持的建议：" + item["id"])


def revision_request(original, bundle, images, model, tasks):
    prompt = role_prompt("review_reviser")
    return build_request(dict(bundle, original_review=original, repair_tasks=tasks), images, model, system_prompt=prompt)


def main():
    parser = argparse.ArgumentParser(description="仅执行一次语义修正调用，生成待重新验证的初稿")
    parser.add_argument("--verification-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model", default=os.environ.get("DASHSCOPE_MODEL") or "qwen3.8-flash")
    args = parser.parse_args()
    root, metadata = None, {}
    try:
        source = resolve_path(args.verification_dir, PROJECT_ROOT).resolve()
        original, bundle, result, _ = check_saved_verification(source)
        tasks = repair_tasks(result)
        if any(t["status"] == "insufficient_evidence" for t in tasks) or not any(t["status"] == "revise" for t in tasks):
            raise ValueError("仅对存在需修正项且无证据不足项的结果调用返修")
        root = resolve_path(args.output_dir, PROJECT_ROOT) / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        root.mkdir(parents=True)
        # 先冻结同一份输入，load_review_run 会再次检查原图哈希。
        write_json(root / "review.json", original)
        write_json(root / "evidence-catalog.json", bundle)
        _, _, images, fingerprints = load_review_run(root)
        (root / "review.json").rename(root / "input-review.json")
        write_json(root / "repair-tasks.json", tasks)
        metadata = dict(fingerprints, status="prepared", model=args.model, source_verification_dir=str(source),
            verification_sha256=hashlib.sha256((source / "verification.json").read_bytes()).hexdigest(),
            automatic_retries=0, output_status="not_reverified")
        payload = revision_request(original, bundle, images, args.model, tasks)
        (root / "prompt.md").write_text(payload["messages"][0]["content"], encoding="utf-8")
        write_json(root / "run.json", metadata)
        started = time.monotonic()
        response = call_dashscope(payload)
        choice = response["choices"][0]
        content = choice["message"]["content"]
        (root / "model-output.txt").write_text(content, encoding="utf-8")
        metadata.update(elapsed_seconds=round(time.monotonic() - started, 3), usage=response.get("usage"),
            response_id=response.get("id"), finish_reason=choice.get("finish_reason"), transport=response.get("transport"))
        if choice.get("finish_reason") != "stop":
            raise ValueError("返修回答未正常结束，不接受部分结果")
        revised = parse_model_json(content, metadata)
        validate_revision(revised, original, bundle, tasks)
        write_json(root / "review.json", revised)
        metadata.update(status="completed_structure_valid", changed=revised != original)
        write_json(root / "run.json", metadata)
        write_json(root.parent / "latest.json", {"run_dir": str(root)})
        print("返修稿已留档，尚未重新验证：" + str(root))
        return 0
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        if root:
            metadata.update(status="failed", error=str(error))
            if isinstance(error, ModelNetworkError):
                metadata.update(error_kind="network_or_incomplete_stream", transport=error.transport)
                if error.partial_content:
                    (root / "partial-output.txt").write_text(error.partial_content, encoding="utf-8")
            write_json(root / "run.json", metadata)
        print("返修停止：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
