"""同证据历史对照；不调用模型，不把助手草稿或模型自评计入人工指标。"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from check_materials import PROJECT_ROOT
from local_storage import atomic_json
from run_workflow import check_saved_verification
from verify_tutorial import load_review_run
from workflow_logs import redact


RUBRIC = {
    "version": 1,
    "support": "结合观察、影响判断、建议和验证计划评判整条建议。supported：证据范围内合理且符合目标；revise：有错误、越界断言或目标错配；insufficient_evidence：材料不足以判断。不要把采样帧未见等同于全片没有，不要把推测的流量变化当事实。",
    "citation": "逐项判断引用是否支持对应观察或推断。supported：有直接支持；revise：引用不相关或与说法冲突；insufficient_evidence：仅凭该引用无法判断。引用 ID 存在不等于引用正确。",
    "pending": "尚未判断用 null；每个已判断项必须写理由。资料不足是有效判断，不等同于尚未评判。",
    "scope": "只评估冻结材料内的建议质量，不评估真实互动提升。总结单列，不混入建议支持比例。",
}
ARMS = ("single_analysis", "analysis_plus_verification")
LABELS = {"single_analysis": "单次分析初稿", "analysis_plus_verification": "分析＋验证候选稿"}
VERDICTS = ("supported", "revise", "insufficient_evidence")


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_path(root, relative):
    path = (root / relative).resolve()
    if path == root.resolve() or root.resolve() not in path.parents:
        raise ValueError("评估文件必须位于评估目录内")
    return path


def event(root, operation, status, started, **details):
    # 每次操作一条摘要；无证据正文、标注正文、模型请求或密钥。
    if not root.is_dir():
        return
    path = root / "events.jsonl"
    if path.exists() and path.stat().st_size > 256 * 1024:
        path.replace(root / "events.previous.jsonl")
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(dict(time=now(), trace_id=root.name, operation=operation,
            status=status, duration_seconds=round(time.monotonic() - started, 3), **details), ensure_ascii=False) + "\n")


def number(value, integer=False):
    valid = type(value) is int if integer else type(value) in (int, float)
    return value if valid and math.isfinite(value) and value >= 0 else None


def resource_total(metadata):
    def total(values):
        return round(sum(values), 3) if all(value is not None for value in values) else None
    return {"recorded_calls": len(metadata),
        "model_seconds": total([number(item.get("elapsed_seconds")) for item in metadata]),
        "tokens": {key: total([number((item.get("usage") or {}).get(key), True) for item in metadata])
                  for key in ("prompt_tokens", "completion_tokens", "total_tokens")}}


def annotation_template(manifest_hash, reviews):
    def blank():
        return {"verdict": None, "reason": ""}
    return {"schema_version": 1, "manifest_sha256": manifest_hash, "status": "draft",
        "provenance": {"actor": "unconfirmed", "reviewer": ""},
        "arms": {arm: {"summary": blank(), "findings": {
            item["id"]: dict(blank(), citations={ref: blank() for ref in dict.fromkeys(item["evidence_ids"])})
            for item in review["findings"]}} for arm, review in reviews.items()}}


def prepare(analysis, verification, output, current_sample):
    """只接受完成的原始两阶段对照；拒绝把旧目标/不同证据的记录混在一起。"""
    started = time.monotonic()
    original, bundle, images, fingerprints = load_review_run(analysis)
    saved, verified_bundle, result, verification_meta = check_saved_verification(verification)
    analysis_meta = read(analysis / "run.json")
    if original != saved or bundle != verified_bundle or any(
            fingerprints[key] != verification_meta.get(key) for key in fingerprints):
        raise ValueError("两组必须使用同一份原始分析与冻结证据")
    if analysis_meta.get("status") != "structure_and_references_valid_semantics_unverified":
        raise ValueError("只接受原始单次分析；返修结果不能冒充单调用基线")
    if any(item.get("automatic_retries") != 0 or item.get("finish_reason") != "stop"
           for item in (analysis_meta, verification_meta)):
        raise ValueError("缺少单次完成记录，无法确定两组调用口径")
    for key in bundle["task"]:
        if bundle["task"][key] != current_sample.get(key):
            raise ValueError("当前样本与历史任务不一致：" + key + "；请按当前目标重新运行")
    if output.exists():
        raise ValueError("输出目录已存在；不会覆盖历史评估")
    output.mkdir(parents=True)
    try:
        files = {}
        def save(relative, value):
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, value)
            files[relative] = sha(path)
        save("rubric.json", RUBRIC)
        reviews = {ARMS[0]: original, ARMS[1]: result["revised_review"]}
        for arm, review in reviews.items():
            save(arm + ".json", review)
        frozen = copy.deepcopy(bundle)
        for index, image in enumerate(images):
            relative = "evidence/image-%03d%s" % (index, ".png" if image["mime"] == "image/png" else ".jpg")
            path = output / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(image["raw"])
            files[relative] = sha(path)
            frozen["catalog"][image["id"]]["local_path"] = relative
        save("evidence-catalog.json", frozen)
        resources = {ARMS[0]: resource_total([analysis_meta]),
                     ARMS[1]: resource_total([analysis_meta, verification_meta])}
        manifest = {"schema_version": 1, "created_at": now(), "sample_id": original["sample_id"],
            "goal_revision": bundle["task"].get("goal_revision"),
            "design": "retrospective_paired_development_case", "held_out": False,
            "labels_frozen_before_model_execution": False, "blind_review": False,
            "source": {"analysis": str(analysis.resolve()), "verification": str(verification.resolve()),
                       **fingerprints, "analysis_metadata_sha256": sha(analysis / "run.json"),
                       "verification_metadata_sha256": sha(verification / "run.json"),
                       "models": [redact(item.get("model"), 100) for item in (analysis_meta, verification_meta)]},
            "files": files, "resources": resources,
            "new_model_calls": 0, "completion_rate": None,
            "limits": ["已有成功案例的回顾对照，未登记失败样本分母，不能计算流程完成率或泛化准确率。",
                       "两组共用同一次分析；验证角色额外看到初稿。候选稿并未再次独立验证。",
                       "当前人工标注发生在模型运行后；不是预先冻结标准答案的独立测试。",
                       "耗时仅累加已记录模型调用，不含抽帧和人工时间；用量不等于费用。",
                       "本地哈希用于发现意外改动，不是防恶意篡改的签名或人工身份认证。"]}
        atomic_json(output / "manifest.json", manifest)
        annotations = annotation_template(sha(output / "manifest.json"), reviews)
        atomic_json(output / "annotations.draft.json", annotations)
        (output / "review-packet.md").write_text(render_packet(output, manifest, frozen, reviews), encoding="utf-8")
        event(output, "prepare", "completed", started, new_model_calls=0)
        score(output)
        return output
    except Exception as error:
        event(output, "prepare", "failed", started, error=redact(str(error)))
        raise


def load_packet(root):
    manifest = read(root / "manifest.json")
    required = {"rubric.json", "evidence-catalog.json", *(arm + ".json" for arm in ARMS)}
    if not required.issubset(manifest["files"]):
        raise ValueError("评估包缺少必要的冻结文件")
    for relative, expected in manifest["files"].items():
        if sha(safe_path(root, relative)) != expected:
            raise ValueError("冻结内容已变化：" + relative)
    reviews = {arm: read(root / (arm + ".json")) for arm in ARMS}
    return manifest, reviews


def validate_annotations(root, annotations, reviews):
    expected = annotation_template(sha(root / "manifest.json"), reviews)
    if annotations.get("schema_version") != 1 or annotations.get("manifest_sha256") != expected["manifest_sha256"]:
        raise ValueError("标注不属于当前冻结评估包")
    if not isinstance(annotations.get("arms"), dict) or set(annotations["arms"]) != set(ARMS):
        raise ValueError("标注必须覆盖两个对照组")
    judged = 0
    def check(item):
        nonlocal judged
        if not isinstance(item, dict) or item.get("verdict") not in (None, *VERDICTS):
            raise ValueError("无效的人工判断状态")
        reason = item.get("reason")
        if not isinstance(reason, str) or len(reason) > 4000:
            raise ValueError("标注理由必须为最多 4000 字的文本")
        if item.get("verdict") is not None:
            if not reason.strip():
                raise ValueError("每个已判断项必须填写理由")
            judged += 1
    for arm in ARMS:
        actual, template = annotations["arms"][arm], expected["arms"][arm]
        check(actual.get("summary"))
        findings = actual.get("findings")
        if not isinstance(findings, dict) or set(findings) != set(template["findings"]):
            raise ValueError("标注建议 ID 必须与冻结结果一致")
        for identifier, finding in findings.items():
            check(finding)
            refs = finding.get("citations")
            if not isinstance(refs, dict) or set(refs) != set(template["findings"][identifier]["citations"]):
                raise ValueError("引用标注必须与冻结结果一致")
            for item in refs.values():
                check(item)
    return judged


def freeze(root, draft, reviewer, human_reviewed=False):
    started = time.monotonic()
    try:
        _, reviews = load_packet(root)
        if not human_reviewed or not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 80:
            raise ValueError("需实际人工完成判断后显式确认，并填写评审代号")
        if draft.stat().st_size > 256 * 1024:
            raise ValueError("标注文件过大")
        annotations = read(draft)
        if annotations.get("status") != "draft" or annotations.get("provenance", {}).get("actor") != "human":
            raise ValueError("只接受明确标为 human 的人工草稿；助手或未确认草稿不能计分")
        if not validate_annotations(root, annotations, reviews):
            raise ValueError("至少需要一项人工判断；未标注内容保持未知")
        annotations.update(status="frozen", frozen_at=now(), provenance={
            "actor": "human", "reviewer": reviewer.strip(), "confirmation": "operator_declared_not_identity_verified"})
        # 独占创建，防止并发覆盖历史提交；意外写入中断会在读取时失败，不默认为有效。
        with (root / "annotations.frozen.json").open("x", encoding="utf-8") as output:
            output.write(json.dumps(annotations, ensure_ascii=False, indent=2) + "\n")
        atomic_json(root / "annotation-seal.json", {"sha256": sha(root / "annotations.frozen.json")})
        event(root, "freeze", "completed", started)
        return score(root)
    except Exception as error:
        event(root, "freeze", "failed", started, error=redact(str(error)))
        raise


def distribution(items):
    counts = {key: sum(item["verdict"] == key for item in items) for key in VERDICTS}
    judged, total = sum(counts.values()), len(items)
    return {"total": total, "judged": judged, "pending": total - judged, **counts,
            "coverage": judged / total if total else None,
            "supported_proportion": counts["supported"] / judged if judged else None}


def score(root):
    started = time.monotonic()
    try:
        manifest, reviews = load_packet(root)
        frozen = root / "annotations.frozen.json"
        if frozen.exists():
            if sha(frozen) != read(root / "annotation-seal.json")["sha256"]:
                raise ValueError("冻结标注已变化")
            annotations = read(frozen)
            if annotations.get("status") != "frozen" or annotations.get("provenance", {}).get("actor") != "human":
                raise ValueError("只有人工冻结标注可用于指标")
            validate_annotations(root, annotations, reviews)
        else:
            # 即使草稿中已有助手判断，也完全不计入人工指标。
            annotations = annotation_template(sha(root / "manifest.json"), reviews)
        result = {"schema_version": 1, "generated_at": now(), "manifest_sha256": sha(root / "manifest.json"),
            "annotations_sha256": sha(frozen) if frozen.exists() else None,
            "status": "human_scored" if frozen.exists() else "awaiting_human_annotations",
            "design": manifest["design"], "completion_rate": None, "arms": {}, "limits": manifest["limits"]}
        for arm in ARMS:
            labels = annotations["arms"][arm]
            result["arms"][arm] = {"summary_verdict": labels["summary"]["verdict"],
                "findings": distribution(list(labels["findings"].values())),
                "citations": distribution([item for finding in labels["findings"].values() for item in finding["citations"].values()]),
                "resources": manifest["resources"][arm]}
        result["annotation_coverage_complete"] = frozen.exists() and all(
            value["summary_verdict"] is not None and all(value[key]["pending"] == 0 for key in ("findings", "citations"))
            for value in result["arms"].values())
        atomic_json(root / "comparison.json", result)
        (root / "comparison.md").write_text(render_comparison(result), encoding="utf-8")
        event(root, "score", "completed", started, annotation_status=result["status"])
        return result
    except Exception as error:
        event(root, "score", "failed", started, error=redact(str(error)))
        raise


def render_comparison(result):
    lines = ["# 同证据历史对照", "", "状态：" + result["status"], "",
             "人工支持比例 = supported / 已判断项（含 revise、insufficient_evidence）；null 不进入分母。请同时查看覆盖率，不能用少量已判断项代表全部结果。", "",
             "| 组别 | 建议支持 / 已判断 / 总数 | 引用支持 / 已判断 / 总数 | 历史模型调用 | 模型秒数 | 总 tokens |", "| --- | --- | --- | --- | --- | --- |"]
    for arm, value in result["arms"].items():
        def counts(key):
            item = value[key]
            return "%s / %s / %s" % (item["supported"], item["judged"], item["total"]) if item["judged"] else "待评判（共 %s 项）" % item["total"]
        resources = value["resources"]
        lines.append("| %s | %s | %s | %s | %s | %s |" % (LABELS[arm], counts("findings"), counts("citations"), resources["recorded_calls"],
                     resources["model_seconds"] if resources["model_seconds"] is not None else "未知",
                     resources["tokens"]["total_tokens"] if resources["tokens"]["total_tokens"] is not None else "未知"))
    lines += ["", "这次生成对照表新增模型调用为 0。两组复用同一次分析，表内历史用量不能再次相加为本次费用。流程完成率未知；仅纳入成功案例。", "",
              "标注入口：[评判材料](review-packet.md) · [人工草稿](annotations.draft.json)", ""]
    lines += ["- " + text for text in result["limits"]]
    return "\n".join(lines) + "\n"


def render_packet(root, manifest, bundle, reviews):
    lines = ["# 人工评判材料", "", "本包为调试案例的历史对照，组别公开，不是独立盲评。", "",
             "目标：" + bundle["task"].get("tutorial_goal", "未记录"), "",
             "在 annotations.draft.json 逐项填写 verdict 和 reason；未判断保留 null。只有人实际评判后才把 provenance.actor 改为 human，并通过 freeze 命令确认。助手草稿请标为 assistant。", ""]
    lines += ["- " + value for key, value in RUBRIC.items() if key != "version"]
    for arm, review in reviews.items():
        lines += ["", "## " + LABELS[arm], "", "总结：" + review["summary"], ""]
        for item in review["findings"]:
            lines += ["### " + item["id"], ""]
            for key, title in (("observation", "观察"), ("interpretation", "影响推断"), ("suggestion", "建议"), ("validation_plan", "验证计划")):
                lines += [title + "：" + item[key], ""]
            lines += ["引用：" + "、".join(item["evidence_ids"]), ""]
        lines += ["局限：" + "；".join(review["limitations"]), ""]
    lines += ["## 冻结证据", "", "覆盖范围：`" + json.dumps(bundle.get("coverage"), ensure_ascii=False) + "`", ""]
    for identifier, item in bundle["catalog"].items():
        lines += ["### " + identifier, ""]
        if "local_path" in item:
            lines += ["![%s](<%s>)" % (identifier, safe_path(root, item["local_path"]).as_posix()), ""]
        lines += ["```json", json.dumps({key: value for key, value in item.items() if key != "local_path"}, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare", help="生成待人工标注的历史对照包，零模型调用")
    create.add_argument("--analysis-dir", type=Path, required=True)
    create.add_argument("--verification-dir", type=Path, required=True)
    create.add_argument("--sample", type=Path, required=True)
    create.add_argument("--output-dir", type=Path)
    submit = commands.add_parser("freeze", help="实际人工评判后提交一次，不能覆盖")
    submit.add_argument("--packet", type=Path, required=True)
    submit.add_argument("--draft", type=Path)
    submit.add_argument("--reviewer", required=True, help="使用评审代号，不必填真实姓名")
    submit.add_argument("--human-reviewed", action="store_true", help="操作者确认这些判断已经由人完成")
    calculate = commands.add_parser("score", help="重新核验冻结内容并计算报告；忽略未提交草稿")
    calculate.add_argument("--packet", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            root = args.output_dir or PROJECT_ROOT / "reports/evaluations" / ("eval-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
            prepare(args.analysis_dir.resolve(), args.verification_dir.resolve(), root.resolve(), read(args.sample))
        else:
            root = args.packet.resolve()
            if args.command == "freeze":
                freeze(root, args.draft or root / "annotations.draft.json", args.reviewer, args.human_reviewed)
            else:
                score(root)
        print("对照报告：" + str(root / "comparison.md"))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("评估失败：" + redact(str(error)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
