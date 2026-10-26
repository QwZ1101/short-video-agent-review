"""第四步：独立模型调用核对初稿，输出逐项理由与修订候选稿。

使用初稿留存的原始证据，不读取人工核对记录，不重新生成初稿。
"""
import argparse
from datetime import datetime
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sys
import time

from check_materials import PROJECT_ROOT, resolve_path
from review_tutorial import ModelNetworkError, build_request, call_dashscope, validate_review, write_json


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_review_run(source_dir):
    """读取冻结的分析输入，并确认图片内容与当时一致。"""
    review_raw = (source_dir / "review.json").read_bytes()
    evidence_raw = (source_dir / "evidence-catalog.json").read_bytes()
    review, bundle = json.loads(review_raw), json.loads(evidence_raw)
    validate_review(review, bundle)
    images = []
    for identifier, entry in bundle["catalog"].items():
        if entry.get("type") not in ("video_frame", "screenshot"):
            continue
        path = Path(entry["local_path"])
        raw = path.read_bytes()
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise ValueError("证据图片为空或过大：" + identifier)
        if digest(raw) != entry.get("sha256"):
            raise ValueError("证据图片已变化，不能验证旧初稿：" + identifier)
        mime = mimetypes.guess_type(str(path))[0]
        if mime not in ("image/jpeg", "image/png"):
            raise ValueError("证据图片格式不支持：" + identifier)
        images.append({"id": identifier, "mime": mime, "raw": raw})
    return review, bundle, images, {"review_sha256": digest(review_raw), "evidence_sha256": digest(evidence_raw)}


def build_verification_request(review, bundle, images, model):
    prompt = (PROJECT_ROOT / "prompts/tutorial_verify.md").read_text(encoding="utf-8")
    payload = build_request(dict(bundle, original_review=review), images, model, system_prompt=prompt)
    payload["messages"][1]["content"].append({"type": "text", "text":
        "证据已全部提供。现在请按系统规定返回一个完整 JSON 对象，顶层字段为 sample_id、access_check、summary_check、checks、revised_review、limitations。"
        "sample_id 必须为 " + review["sample_id"] + "，checks 必须逐项核对这些 ID：" +
        json.dumps([item["id"] for item in review["findings"]], ensure_ascii=False) +
        "。不可返回数组或空对象；证据不足时仍填写对应检查，标为 insufficient_evidence 并说明原因。"})
    return payload


def validate_verification(result, original, bundle):
    """拒绝漏检、重复检查、未知引用、修改样本或丢失原建议 ID。"""
    if not isinstance(result, dict):
        raise ValueError("验证结果必须是 JSON 对象，不能是数组或其他类型")
    if result.get("sample_id") != original["sample_id"]:
        raise ValueError("验证结果样本 ID 不匹配")

    access = result.get("access_check")
    if not isinstance(access, dict) or any(not isinstance(access.get(key), str) or not access[key].strip() for key in ("creator_side", "viewer_side")):
        raise ValueError("必须区分创作者与观众的操作路径")
    refs = access.get("evidence_ids")
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in bundle["catalog"] for ref in refs):
        raise ValueError("操作路径检查必须引用现有证据")

    def check_item(item):
        if not isinstance(item, dict) or item.get("status") not in ("supported", "revise", "insufficient_evidence"):
            raise ValueError("检查状态无效")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError("检查必须提供理由")
        refs = item.get("evidence_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in bundle["catalog"] for ref in refs):
            raise ValueError("验证理由引用了不存在的证据或没有证据")

    check_item(result.get("summary_check"))
    checks = result.get("checks")
    if not isinstance(checks, list):
        raise ValueError("checks 必须是数组")
    expected_ids = {item["id"] for item in original["findings"]}
    actual_ids = []
    for item in checks:
        check_item(item)
        identifier = item.get("finding_id")
        if not isinstance(identifier, str):
            raise ValueError("finding_id 必须是文本")
        actual_ids.append(identifier)
    if len(actual_ids) != len(expected_ids) or set(actual_ids) != expected_ids:
        raise ValueError("每条原建议必须恰好检查一次")
    revised = result.get("revised_review")
    validate_review(revised, bundle)
    if {item["id"] for item in revised["findings"]} != expected_ids:
        raise ValueError("修订稿必须保留原建议 ID")
    limitations = result.get("limitations")
    if not isinstance(limitations, list) or not limitations or any(not isinstance(item, str) or not item.strip() for item in limitations):
        raise ValueError("验证结果必须说明局限")
    # 程序据逐项结果决定状态，不采用模型另行自报的 overall_pass。
    statuses = [result["summary_check"]["status"]] + [item["status"] for item in checks]
    return "supported_within_provided_evidence" if all(status == "supported" for status in statuses) else "needs_human_review"


def render_verification(result, original, bundle, metadata):
    labels = {"supported": "本次证据范围内支持", "revise": "需要修改", "insufficient_evidence": "证据不足"}
    lines = ["# 教程复盘验证报告", "", "模型：" + metadata["model"] + "（独立调用；判断仍可能与初稿出现相同偏差）", "",
             "**修订稿是候选稿，仍待人工核对。第二次模型调用不保证正确。**", "",
             "程序状态：" + metadata["decision"], "",
             "输入仅含初稿与其留存证据；没有向模型提供人工核对记录。提示词改进使用了本样本暴露的问题，因此这不是独立测试集评测。", "",
             "## 谁能操作哪个入口", "",
             "**创作者一侧：** " + result["access_check"]["creator_side"], "",
             "**观众一侧：** " + result["access_check"]["viewer_side"], "",
             "## 总体判断核对", "", labels[result["summary_check"]["status"]] + "：" + result["summary_check"]["reason"], "",
             "**修订后总体判断：** " + result["revised_review"]["summary"], ""]

    def evidence_links(refs):
        lines.extend(["证据：", ""])
        for ref in refs:
            item = bundle["catalog"][ref]
            if "local_path" in item:
                label = ref
                if "requested_time_seconds" in item:
                    label += " · 请求截取位置 %.3f 秒" % item["requested_time_seconds"]
                lines.append("- [%s](<%s>)" % (label, Path(item["local_path"]).as_posix()))
            else:
                lines.append("- " + ref + "（内容见同目录 evidence-catalog.json）")
        lines.append("")

    evidence_links(list(dict.fromkeys(result["access_check"]["evidence_ids"] + result["summary_check"]["evidence_ids"])))
    checks = {item["finding_id"]: item for item in result["checks"]}
    revised = {item["id"]: item for item in result["revised_review"]["findings"]}
    for old in original["findings"]:
        check, new = checks[old["id"]], revised[old["id"]]
        lines.extend(["## " + old["id"] + " · " + labels[check["status"]], "",
                      "**核对理由：** " + check["reason"], "",
                      "**原建议：** " + old["suggestion"], "", "**修订建议：** " + new["suggestion"], "",
                      "**修订观察：** " + new["observation"], "", "**影响判断：** " + new["interpretation"], "",
                      "**如何验证建议效果：** " + new["validation_plan"], ""])
        evidence_links(list(dict.fromkeys(check["evidence_ids"] + new["evidence_ids"])))
    lines.extend(["## 仍需核对", ""] + ["- " + item for item in result["limitations"] + result["revised_review"]["limitations"]])
    lines.extend(["", "API 用量：`" + json.dumps(metadata.get("usage"), ensure_ascii=False) + "`", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="独立调用 DashScope 验证已有复盘；默认产生一次 API 用量")
    parser.add_argument("--review-dir", help="待验证的初稿运行目录；默认最近一次成功初稿")
    parser.add_argument("--output-dir", default="reports/verification", help="本次验证输出的父目录")
    parser.add_argument("--dry-run", action="store_true", help="只检查证据、准备输入，不调用 API")
    parser.add_argument("--model", default=os.environ.get("DASHSCOPE_MODEL") or "qwen3.8-flash")
    args = parser.parse_args()
    run_dir, metadata = None, {}
    try:
        source = args.review_dir
        if not source:
            source = json.loads((PROJECT_ROOT / "reports/review/latest.json").read_text(encoding="utf-8"))["run_dir"]
        source_dir = resolve_path(source, PROJECT_ROOT).resolve()
        original, bundle, images, fingerprints = load_review_run(source_dir)
        payload = build_verification_request(original, bundle, images, args.model)
        run_dir = resolve_path(args.output_dir, PROJECT_ROOT) / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        run_dir.mkdir(parents=True)
        write_json(run_dir / "original-review.json", original)
        write_json(run_dir / "evidence-catalog.json", bundle)
        (run_dir / "prompt.md").write_text(payload["messages"][0]["content"], encoding="utf-8")
        metadata = dict(fingerprints, status="prepared", source_review_dir=str(source_dir), model=args.model,
                        image_count=len(images), automatic_retries=0, manual_review_supplied=False)
        write_json(run_dir / "run.json", metadata)
        if args.dry_run:
            print("验证输入准备完成，未调用 API：" + str(run_dir))
            return 0
        print("验证角色调用 %s，检查 %s 条建议、%s 张图片。" % (args.model, len(original["findings"]), len(images)), flush=True)
        started = time.monotonic()
        response = call_dashscope(payload)
        choice = response["choices"][0]
        content = choice["message"]["content"]
        (run_dir / "model-output.txt").write_text(content, encoding="utf-8")
        metadata.update(elapsed_seconds=round(time.monotonic() - started, 2), usage=response.get("usage"),
                        response_id=response.get("id"), finish_reason=choice.get("finish_reason"), transport=response.get("transport"))
        if choice.get("finish_reason") != "stop":
            raise ValueError("模型回答未正常结束，未接受验证结果")
        result = json.loads(content)
        decision = validate_verification(result, original, bundle)
        metadata.update(status="completed_structure_valid", decision=decision, revised_review_status="candidate_not_reverified")
        write_json(run_dir / "verification.json", result)
        write_json(run_dir / "revised-review.json", result["revised_review"])
        write_json(run_dir / "run.json", metadata)
        (run_dir / "verification.md").write_text(render_verification(result, original, bundle, metadata), encoding="utf-8")
        write_json(run_dir.parent / "latest.json", {"run_dir": str(run_dir), "report_path": str(run_dir / "verification.md"), "decision": decision})
        print("验证调用完成：" + decision + "\n阅读报告：" + str(run_dir / "verification.md"))
        return 0
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        if run_dir:
            metadata.update(status="failed", error=str(error))
            if isinstance(error, ModelNetworkError):
                metadata.update(error_kind="network_or_incomplete_stream", transport=error.transport)
                if error.partial_content:
                    (run_dir / "partial-output.txt").write_text(error.partial_content, encoding="utf-8")
            write_json(run_dir / "run.json", metadata)
        print("验证失败：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
