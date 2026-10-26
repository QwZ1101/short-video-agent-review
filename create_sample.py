"""登记独立教程样本；不调用模型、不复制旧结论、不执行素材。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from check_materials import PROJECT_ROOT, check_materials, resolve_path


def required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + "不能为空")
    return value.strip()


def input_file(value, root, suffixes, label):
    path = resolve_path(required(value, label).strip('"'), root).resolve()
    if path.suffix.lower() not in suffixes:
        raise ValueError(label + "文件类型不支持")
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(label + "不存在、为空或不是文件")
    return str(path)


def create_sample(video, goal, audience, prior_knowledge, transcript=None,
                  sample_id=None, root=PROJECT_ROOT):
    root = Path(root).resolve()
    folder = root / "data/samples"
    sample = {
        "schema_version": 1,
        "scenario": "教程讲解类短视频复盘",
        "video": {"path": input_file(video, root, {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}, "视频"),
                  "duration_seconds": None, "metadata_source": "尚未探测，后续视频预处理读取"},
        "tutorial_goal": required(goal, "教程目标"),
        "audience": required(audience, "目标受众"),
        "audience_prior_knowledge": required(prior_knowledge, "观众基础"),
        "video_processing": {"frame_interval_seconds": 3, "max_frame_width": 960,
                             "include_subtitle_midpoints": True},
        "transcript_path": input_file(transcript, root, {".srt"}, "字幕") if transcript else None,
        "transcript_source": "用户提供，尚未与音视频逐句核对" if transcript else None,
        "supporting_materials": [],
        "source_delivery": None,
        "platform_metrics": None,
        "review_question": "这条视频能否帮助目标观众完成教程目标，哪些位置需要补充或修改？",
        "status": "registered_unreviewed",
        "registration": {"created_at": datetime.now(timezone.utc).isoformat(),
                         "scope": "仅检查文件可读性；没有完成解码、内容分析或观众测试"},
    }
    if sample_id is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", sample_id):
        raise ValueError("样本 ID 需以小写字母开头，只含小写字母、数字、下划线或连字符，最长 64 位")
    sample["sample_id"] = sample_id or "new-sample"
    report = check_materials(sample, root)
    broken = [item for item in report["materials"] if item["status"] in ("invalid", "missing")]
    if report["status"] == "blocked" or broken:
        raise ValueError("材料检查未通过：" + "；".join(x["message"] for x in broken))
    folder.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        identifier = sample_id or "tutorial-%03d" % number
        sample["sample_id"] = identifier
        path = folder / (identifier + ".json")
        try:
            # 排他创建，避免重复运行覆盖已有样本；自动编号也支持竞争重试。
            with path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(sample, ensure_ascii=False, indent=2) + "\n")
            return path
        except FileExistsError:
            if sample_id:
                raise ValueError("样本已存在，未覆盖：" + str(path))
            number += 1


def main():
    parser = argparse.ArgumentParser(description="登记新教程样本（不调用模型）")
    parser.add_argument("--video")
    parser.add_argument("--goal")
    parser.add_argument("--audience")
    parser.add_argument("--prior-knowledge")
    parser.add_argument("--transcript")
    parser.add_argument("--sample-id")
    parser.add_argument("--interactive", action="store_true", help="逐项填写必要信息")
    args = parser.parse_args()
    if args.interactive:
        for key, prompt in (("video", "视频完整路径"), ("goal", "希望观众看完能完成什么"),
                            ("audience", "目标受众"), ("prior_knowledge", "观众已有基础")):
            if not getattr(args, key):
                setattr(args, key, input(prompt + "：").strip())
        if not args.transcript:
            args.transcript = input("配套 SRT 字幕路径（没有可直接回车）：").strip() or None
    try:
        path = create_sample(args.video, args.goal, args.audience, args.prior_knowledge,
                             args.transcript, args.sample_id)
    except (OSError, ValueError) as error:
        print("未建立任务：" + str(error), file=sys.stderr)
        return 1
    print("已登记：" + str(path))
    print("未运行模型，未继承其他样本的截图、源码包、后台数据或结论。")
    print("下一步先补充配套材料并写下人工预期，再运行材料检查：")
    print('python "%s" --sample "%s" --output-dir "%s"' % (
        PROJECT_ROOT / "check_materials.py", path, PROJECT_ROOT / "reports/materials" / path.stem))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
