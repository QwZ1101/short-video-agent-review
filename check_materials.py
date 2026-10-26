"""第一步：把一条复盘任务的材料检查结果转换成结构化报告。

只使用 Python 标准库。读取本地文件，不调用模型、不执行源码包。
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
TIME_LINE = re.compile(
    r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+"
    r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})$"
)


def resolve_path(value, root):
    """样本里的相对路径始终相对于项目根目录，不受终端位置影响。"""
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_sample(path):
    """先检查任务单结构，避免后续代码收到错误类型。"""
    sample = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(sample, dict) or sample.get("schema_version") != 1:
        raise ValueError("样本必须是 schema_version 为 1 的 JSON 对象")
    for name in ("sample_id", "tutorial_goal", "audience"):
        if not isinstance(sample.get(name), str) or not sample[name].strip():
            raise ValueError("必填文本缺失：" + name)
    video = sample.get("video")
    if not isinstance(video, dict) or not isinstance(video.get("path"), str) or not video["path"].strip():
        raise ValueError("video.path 必须是非空文件路径")
    duration = video.get("duration_seconds")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0):
        raise ValueError("video.duration_seconds 必须是正数或 null")
    transcript = sample.get("transcript_path")
    if transcript is not None and (not isinstance(transcript, str) or not transcript.strip()):
        raise ValueError("transcript_path 必须是非空路径或 null")
    materials = sample.get("supporting_materials", [])
    if not isinstance(materials, list):
        raise ValueError("supporting_materials 必须是数组")
    ids = {"video", "transcript"}
    for item in materials:
        if not isinstance(item, dict):
            raise ValueError("每份配套材料必须是对象")
        for name in ("id", "type", "path"):
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise ValueError("配套材料缺少文本字段：" + name)
        if item["id"] in ids:
            raise ValueError("材料标识重复：" + item["id"])
        ids.add(item["id"])
    return sample


def seconds(parts):
    hours, minutes, secs, millis = map(int, parts)
    if minutes >= 60 or secs >= 60:
        raise ValueError("字幕时间的分或秒超出范围")
    return hours * 3600 + minutes * 60 + secs + millis / 1000


def inspect_subtitles(path, duration):
    """读取 SRT，检查每条字幕的格式与时间，保留可回看的时间范围。"""
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise ValueError("字幕为空")
    cues = []
    previous_start = -1
    for number, block in enumerate(re.split(r"\n\s*\n", text), start=1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError("第 %s 个字幕块结构不完整" % number)
        match = TIME_LINE.fullmatch(lines[1].strip())
        if not match:
            raise ValueError("第 %s 个字幕块时间格式错误" % number)
        start, end = seconds(match.groups()[:4]), seconds(match.groups()[4:])
        if start >= end or start < previous_start:
            raise ValueError("第 %s 个字幕块起止或排序错误" % number)
        if duration is not None and end > duration + 0.05:
            raise ValueError("第 %s 个字幕块超出配置中的视频时长" % number)
        content = "\n".join(lines[2:]).strip()
        if not content:
            raise ValueError("第 %s 个字幕块没有文本" % number)
        cues.append({"start_seconds": start, "end_seconds": end, "text": content})
        previous_start = start
    return {"cue_count": len(cues), "cues": cues, "time_check_basis": "样本配置中的时长；本步骤未重新探测视频"}


def inspect_file(material_id, kind, value, root, required=False, duration=None):
    result = {"id": material_id, "type": kind, "required": required, "path": value}
    if not value:
        result.update(status="not_provided", message="未提供")
        return result
    path = resolve_path(value, root)
    try:
        if not path.exists():
            result.update(status="missing", message="路径不存在")
            return result
        if not path.is_file():
            raise ValueError("路径指向目录，不是文件")
        result["size_bytes"] = path.stat().st_size
        if result["size_bytes"] == 0:
            raise ValueError("文件为空")
        with path.open("rb") as handle:
            handle.read(32)
        if kind == "srt":
            result["details"] = inspect_subtitles(path, duration)
            message = "字幕已解析，时间范围检查通过"
        elif kind == "zip":
            with zipfile.ZipFile(path) as archive:
                entries = [entry for entry in archive.infolist() if not entry.is_dir()]
                if not entries:
                    raise ValueError("源码包没有文件")
                result["details"] = {
                    "file_count": len(entries),
                    "files": [entry.filename for entry in entries],
                    "html_files": [entry.filename for entry in entries if entry.filename.lower().endswith(".html")],
                    "readme_files": [entry.filename for entry in entries if Path(entry.filename).name.lower().startswith("readme")],
                }
            message = "ZIP 目录可读；尚未检查全部文件内容或运行源码"
        else:
            message = "文件存在、非空且可读取；尚未解码或理解内容"
        result.update(status="available", message=message)
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as error:
        result.update(status="invalid", message=str(error))
    return result


def check_materials(sample, root=PROJECT_ROOT):
    """把所有材料交给对应检查函数，再整理缺失信息与后续工作。"""
    results = [inspect_file("video", "video", sample["video"]["path"], root, required=True)]
    results.append(inspect_file("transcript", "srt", sample.get("transcript_path"), root,
                                duration=sample["video"].get("duration_seconds")))
    for item in sample.get("supporting_materials", []):
        results.append(inspect_file(item["id"], item["type"], item["path"], root))

    missing_context = []
    if not sample.get("audience_prior_knowledge"):
        missing_context.append("观众基础未填写：后续判断教程难度时需说明假设")
    if not sample.get("platform_metrics"):
        missing_context.append("未提供后台数据：当前不能分析留存数值或具体流量原因")
    else:
        missing_context.append("后台数据已填写，但本步骤尚未验证其字段、口径和数值")

    blocked = any(item["required"] and item["status"] != "available" for item in results)
    issues = [item for item in results if item["status"] != "available"]
    status = "blocked" if blocked else ("ready_with_gaps" if issues or missing_context else "ready_for_analysis")
    return {
        "report_version": 1,
        "sample_id": sample["sample_id"],
        "tutorial_goal": sample["tutorial_goal"],
        "audience": sample["audience"],
        "status": status,
        "summary": {"total": len(results), "available": len(results) - len(issues), "unavailable": len(issues)},
        "materials": results,
        "missing_context": missing_context,
        "scope": [
            "本报告由本地检查程序生成，尚未接入大模型或 Agent",
            "视频和截图只检查文件可读性，尚未识别画面、声音或截图文字",
            "字幕与实际音视频是否一致，需后续核对",
            "未重新访问线上站点，也未执行下载包或验证观众能独立完成任务",
        ],
        "next_steps": ["补齐或修正不可用的材料", "提取视频关键帧，与字幕和配套材料一起分析", "将每项分析结论关联到原始证据"],
    }


def render_markdown(report):
    labels = {"available": "可读取", "missing": "缺失", "invalid": "有问题", "not_provided": "未提供"}
    statuses = {"blocked": "必需材料有问题，需先修复", "ready_with_gaps": "可以进入后续分析，部分信息尚缺", "ready_for_analysis": "材料检查通过，可以进入后续分析"}
    lines = ["# 材料检查报告", "", "样本：" + report["sample_id"], "",
             "教程目标：" + report["tutorial_goal"], "", "状态：" + statuses[report["status"]], "",
             "已读取 %s / %s 份材料。文件可读取不代表内容正确或已完成复盘。" % (report["summary"]["available"], report["summary"]["total"]), "", "## 材料", ""]
    for item in report["materials"]:
        lines.append("- **%s：%s**。%s" % (item["id"], labels[item["status"]], item["message"]))
        details = item.get("details", {})
        if "cue_count" in details:
            lines.append("  - 共 %s 条字幕；时间核对依据为配置中的视频时长。" % details["cue_count"])
        if "file_count" in details:
            lines.append("  - 包含 %s 个文件；HTML：%s。" % (details["file_count"], "、".join(details["html_files"]) or "未找到"))
    for title, items in (("待补充信息", report["missing_context"]), ("本次检查范围", report["scope"]), ("下一步", report["next_steps"])):
        lines.extend(["", "## " + title, ""])
        lines.extend("- " + item for item in items)
        if not items:
            lines.append("无。")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="检查教程复盘样本的本地材料")
    parser.add_argument("--sample", default="data/samples/tutorial-001.json", help="样本 JSON 路径")
    parser.add_argument("--output-dir", default="reports", help="报告目录")
    args = parser.parse_args()
    try:
        sample = load_sample(resolve_path(args.sample, PROJECT_ROOT))
        report = check_materials(sample)
        output = resolve_path(args.output_dir, PROJECT_ROOT)
        output.mkdir(parents=True, exist_ok=True)
        json_path = output / "materials-report.json"
        markdown_path = output / "materials-report.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
    except (OSError, ValueError) as error:
        print("检查失败：" + str(error), file=sys.stderr)
        return 2
    print("样本：" + report["sample_id"])
    print("材料：%s / %s 可读取" % (report["summary"]["available"], report["summary"]["total"]))
    print("状态：" + report["status"])
    print("阅读报告：" + str(markdown_path))
    print("程序数据：" + str(json_path))
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
