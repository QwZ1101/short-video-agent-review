"""第二步：用 FFprobe 读取视频，用 FFmpeg 建立带时间位置的画面证据。

采样规则可配置；本步骤尚不判断画面好坏，也不调用大模型。
"""

import argparse
from datetime import datetime
from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

from check_materials import PROJECT_ROOT, inspect_subtitles, load_sample, resolve_path


def run_tool(command, timeout=60):
    """使用参数数组调用工具，带空格或中文的路径也不会被当成命令。"""
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise ValueError(Path(command[0]).name + " 执行失败：" + result.stderr[-2000:])
    return result.stdout


def probe_video(path, ffprobe):
    data = json.loads(run_tool([ffprobe, "-v", "error", "-show_format", "-show_streams",
                                "-of", "json", str(path)]))
    streams = data.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video"
              and not stream.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValueError("文件没有可处理的视频轨道")
    video = videos[0]
    duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法取得有效视频时长")
    try:
        fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        fps = None
    return {
        "duration_seconds": duration, "width": int(video["width"]),
        "height": int(video["height"]), "average_fps": fps,
        "video_stream_index": video["index"], "codec": video.get("codec_name"),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "source": "本次 FFprobe 实测",
    }


def read_settings(sample, interval_override=None):
    config = sample.get("video_processing", {})
    if not isinstance(config, dict):
        raise ValueError("video_processing 必须是对象")
    interval = interval_override if interval_override is not None else config.get("frame_interval_seconds", 5)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
        raise ValueError("采样间隔必须是有限的正数")
    width = config.get("max_frame_width", 960)
    if isinstance(width, bool) or not isinstance(width, int) or not 160 <= width <= 3840:
        raise ValueError("max_frame_width 必须是 160 到 3840 之间的整数")
    include = config.get("include_subtitle_midpoints", True)
    if not isinstance(include, bool):
        raise ValueError("include_subtitle_midpoints 必须是 true 或 false")
    return {"frame_interval_seconds": interval, "max_frame_width": width,
            "include_subtitle_midpoints": include}


def plan_frames(duration, interval, cues, include_midpoints=True, limit=100):
    """定时采样覆盖整个视频；字幕中点补充短句。时间相同的任务合并。"""
    grid_count = math.ceil(duration / interval)
    if grid_count > limit:
        raise ValueError("定时采样超过 %s 张，请增大采样间隔" % limit)
    points = {}

    def add(time, reason):
        time = round(time, 6)
        if 0 <= time < duration:
            points.setdefault(time, []).append(reason)

    for index in range(grid_count):
        add(index * interval, "interval")
    if include_midpoints:
        for cue in cues:
            add((cue["start_seconds"] + cue["end_seconds"]) / 2, "subtitle_midpoint")
    # 距结尾预留半秒，避免在文件结束位置取不到帧。
    if duration > 0.5:
        add(duration - 0.5, "tail")
    if len(points) > limit:
        raise ValueError("总采样超过 %s 张，可增大间隔或关闭字幕中点采样" % limit)
    return [{"requested_time_seconds": time, "reasons": sorted(set(points[time]))}
            for time in sorted(points)]


def subtitles_at(cues, time):
    # 使用左闭右开区间，避免相邻字幕在交界时刻同时匹配。
    return [cue["text"] for cue in cues if cue["start_seconds"] <= time < cue["end_seconds"]]


def format_time(time):
    total_ms = round(time * 1000)
    minutes, remainder = divmod(total_ms, 60000)
    seconds, millis = divmod(remainder, 1000)
    return "%02d:%02d.%03d" % (minutes, seconds, millis)


def build_report(report, run_dir):
    metadata = report["video"]
    lines = ["# 视频证据报告", "", "样本：" + report["sample_id"], "",
             "教程目标：" + report["tutorial_goal"], "",
             "实际视频：%s 秒，%s × %s，平均 %s 帧/秒。" % (
                 metadata["duration_seconds"], metadata["width"], metadata["height"], metadata["average_fps"]), "",
             "共提取 %s 张采样图，读取 %s 条配套字幕。" % (len(report["frames"]), len(report["subtitles"])), "",
             "下列时间是请求截取的位置，不是逐帧测得的精确 PTS；字幕按请求时间关联，未核对配音。", "",
             "## 总览", "", "按从左到右、从上到下的顺序对应下方编号。", "",
             "![画面总览](<%s>)" % (run_dir / "contact-sheet.jpg").as_posix(), ""]
    if report["warnings"]:
        lines.extend(["## 需要注意", ""] + ["- " + warning for warning in report["warnings"]] + [""])
    lines.extend(["## 按时间查看", ""])
    for frame in report["frames"]:
        lines.extend(["### %s · %s" % (frame["id"], format_time(frame["requested_time_seconds"])), "",
                      "字幕：" + (" / ".join(frame["subtitles_at_requested_time"]) or "这个请求时刻没有配套字幕"), "",
                      "![%s](<%s>)" % (frame["id"], (run_dir / frame["image_path"]).as_posix()), ""])
    lines.extend(["## 当前边界", "", "已完成视频解码取样与字幕时间关联，尚未做画面语义分析、语音识别、OCR 或流量归因。",
                  "没有匹配的字幕，不代表视频没有声音、没有画面文字或缺少必要讲解。", ""])
    return "\n".join(lines)


def prepare_video(sample, output_dir, interval_override=None):
    ffprobe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise ValueError("需要把 ffmpeg 和 ffprobe 加入 PATH 后再运行")
    settings = read_settings(sample, interval_override)
    path = resolve_path(sample["video"]["path"], PROJECT_ROOT)
    if not path.is_file():
        raise ValueError("视频文件不存在：" + str(path))
    metadata = probe_video(path, ffprobe)
    warnings, cues = [], []
    for field in ("duration_seconds", "width", "height"):
        configured = sample["video"].get(field)
        actual = metadata[field]
        if configured is not None and configured != actual:
            warnings.append("配置中的 %s 为 %s，本次读取为 %s；此次以实测为准。" % (field, configured, actual))
    transcript = sample.get("transcript_path")
    if transcript:
        try:
            cues = inspect_subtitles(resolve_path(transcript, PROJECT_ROOT), metadata["duration_seconds"])["cues"]
        except (OSError, ValueError, UnicodeError) as error:
            warnings.append("字幕未能解析，仅处理画面：" + str(error))
    else:
        warnings.append("未提供配套字幕，本步骤未进行语音转写。")
    plan = plan_frames(metadata["duration_seconds"], settings["frame_interval_seconds"], cues,
                       settings["include_subtitle_midpoints"])
    # 每次单独保存，失败时保留此前成功报告，避免混入上次遗留的帧。
    run_dir = output_dir / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True)
    width = min(settings["max_frame_width"], metadata["width"])
    width -= width % 2
    frames = []
    for number, point in enumerate(plan, start=1):
        image_path = frames_dir / ("frame-%03d.jpg" % number)
        run_tool([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                  "-ss", str(point["requested_time_seconds"]), "-i", str(path),
                  "-map", "0:%s" % metadata["video_stream_index"], "-frames:v", "1",
                  "-vf", "scale=%s:-2,setsar=1" % width, "-q:v", "2", "-update", "1", str(image_path)])
        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise ValueError("未取得 %s 秒位置的画面" % point["requested_time_seconds"])
        frames.append(dict(point, id="frame-%03d" % number,
                           image_path=image_path.relative_to(run_dir).as_posix(),
                           subtitles_at_requested_time=subtitles_at(cues, point["requested_time_seconds"])))
        print("已提取 %s/%s：%s" % (number, len(plan), format_time(point["requested_time_seconds"])), flush=True)
    rows = math.ceil(len(frames) / 4)
    run_tool([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
              "-framerate", "1", "-start_number", "1", "-i", str(frames_dir / "frame-%03d.jpg"),
              "-vf", "scale=320:-2,tile=4x%s:nb_frames=%s:padding=6:margin=6:color=0x17212b" % (rows, len(frames)),
              "-frames:v", "1", "-q:v", "2", "-update", "1", str(run_dir / "contact-sheet.jpg")])
    report = {
        "report_version": 1, "sample_id": sample["sample_id"], "tutorial_goal": sample["tutorial_goal"],
        "audience_prior_knowledge": sample.get("audience_prior_knowledge"),
        "source_video": str(path), "video": metadata, "settings": settings,
        "timestamp_kind": "requested_seek_time_not_measured_frame_pts",
        "subtitles": cues, "frames": frames, "warnings": warnings,
        "supporting_materials": sample.get("supporting_materials", []),
        "supporting_materials_status": "保留原始材料引用，尚未自动解释其内容",
        "status": "evidence_prepared_not_analyzed",
    }
    (run_dir / "video-evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "video-evidence.md").write_text(build_report(report, run_dir), encoding="utf-8")
    latest = {"sample_id": sample["sample_id"], "run_dir": str(run_dir),
              "report_path": str(run_dir / "video-evidence.md"),
              "data_path": str(run_dir / "video-evidence.json"),
              "contact_sheet_path": str(run_dir / "contact-sheet.jpg")}
    (output_dir / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return latest


def main():
    parser = argparse.ArgumentParser(description="读取视频实际信息，提取采样帧并关联字幕")
    parser.add_argument("--sample", default="data/samples/tutorial-001.json")
    parser.add_argument("--output-dir", default="reports/video")
    parser.add_argument("--interval", type=float, help="临时覆盖采样间隔，不修改配置")
    args = parser.parse_args()
    try:
        sample = load_sample(resolve_path(args.sample, PROJECT_ROOT))
        latest = prepare_video(sample, resolve_path(args.output_dir, PROJECT_ROOT), args.interval)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("视频处理失败：" + str(error), file=sys.stderr)
        return 1
    print("视频证据准备完成：" + latest["report_path"])
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
