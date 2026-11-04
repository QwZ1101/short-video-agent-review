"""第三步：组织证据，调用 DashScope，检查输出结构及引用。

仅使用标准库。不会执行源码包，不读取人工基准答案，不保存 API 密钥。
"""
import argparse
import base64
from datetime import datetime
import hashlib
import http.client
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from check_materials import PROJECT_ROOT, load_sample, resolve_path
from platform_metrics import calculate_metrics


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_frames(frames, limit):
    if limit < 2 or limit > 32:
        raise ValueError("画面数量上限必须在 2 到 32 之间")
    if not frames:
        raise ValueError("没有视频采样图，请先提取视频证据")
    if len(frames) <= limit:
        return frames
    return [frames[round(i * (len(frames) - 1) / (limit - 1))] for i in range(limit)]


def read_zip_description(path):
    with zipfile.ZipFile(path) as archive:
        files = archive.infolist()
        if len(files) > 500:
            raise ValueError("源码包文件数量超过当前分析上限")
        readmes = []
        for info in files:
            if Path(info.filename).name.lower() == "readme.md":
                if info.file_size > 64000:
                    raise ValueError("README 超过当前分析上限")
                readmes.append({"filename": info.filename,
                                "text": archive.read(info).decode("utf-8-sig")})
        return {"files": [item.filename for item in files], "readmes": readmes,
                "scope": "仅读取文件清单和 README，未执行程序"}


def build_evidence(sample, evidence_path, frame_limit=16):
    video = json.loads(evidence_path.read_text(encoding="utf-8"))
    if video.get("sample_id") != sample["sample_id"]:
        raise ValueError("视频证据的样本 ID 与任务单不一致")
    if Path(video["source_video"]).resolve() != resolve_path(sample["video"]["path"], PROJECT_ROOT).resolve():
        raise ValueError("视频证据来自另一个视频，请重新提取")
    catalog, images = {}, []

    def add(identifier, entry, image_path=None):
        if identifier in catalog:
            raise ValueError("证据 ID 重复：" + identifier)
        if image_path:
            raw = image_path.read_bytes()
            if not raw or len(raw) > 10 * 1024 * 1024:
                raise ValueError("图片为空或超过 10MB：" + str(image_path))
            mime = mimetypes.guess_type(str(image_path))[0]
            if mime not in ("image/jpeg", "image/png"):
                raise ValueError("当前仅支持 JPEG 和 PNG 图片")
            entry = dict(entry, local_path=str(image_path), sha256=hashlib.sha256(raw).hexdigest())
            images.append({"id": identifier, "mime": mime, "raw": raw})
        catalog[identifier] = entry

    chosen = select_frames(video["frames"], frame_limit)
    for frame in chosen:
        add(frame["id"], {"type": "video_frame", "requested_time_seconds": frame["requested_time_seconds"],
                          "timestamp_kind": video["timestamp_kind"]}, evidence_path.parent / frame["image_path"])
    for number, cue in enumerate(video["subtitles"], 1):
        add("subtitle-%03d" % number, dict(cue, type="subtitle", source=sample.get("transcript_source")))
    for material in sample.get("supporting_materials", []):
        path = resolve_path(material["path"], PROJECT_ROOT)
        identifier = "material-" + material["id"]
        if material["type"] == "screenshot":
            add(identifier, {"type": "screenshot"}, path)
        elif material["type"] == "zip":
            add(identifier, dict(read_zip_description(path), type="zip_description"))
    add("context-delivery", {"type": "user_provided_context", "content": sample.get("source_delivery"),
                             "scope": "任务单记录，本次没有重新访问网站或测试运行"})
    metrics = calculate_metrics(sample.get("platform_metrics"))
    if metrics["status"] == "completed":
        add("metrics-calculated", dict(metrics, type="calculated_metrics"))
    task = {key: sample.get(key) for key in ("sample_id", "tutorial_goal", "audience", "audience_prior_knowledge", "review_question", "platform_metrics", "goal_revision", "success_criteria", "out_of_scope")}
    coverage = {"available_frames": len(video["frames"]), "selected_frames": len(chosen),
                "subtitle_count": len(video["subtitles"]), "image_count": len(images),
                "selection": "按已提取画面索引均匀选择，含首尾；未观看连续视频或听取音频",
                "warnings": video.get("warnings", [])}
    return {"task": task, "coverage": coverage, "catalog": catalog}, images


def build_request(bundle, images, model, system_prompt=None):
    # 本机路径仅留在审计清单，发给模型的是证据 ID、内容及图片。
    catalog = {key: {k: v for k, v in item.items() if k != "local_path"}
               for key, item in bundle["catalog"].items()}
    content = [{"type": "text", "text": json.dumps(dict(bundle, catalog=catalog), ensure_ascii=False)}]
    for item in images:
        content.extend([{"type": "text", "text": "下一张图片对应证据 ID：" + item["id"]},
                        {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (
                            item["mime"], base64.b64encode(item["raw"]).decode("ascii"))}}])
    if system_prompt is None:
        system_prompt = (PROJECT_ROOT / "prompts/tutorial_review.md").read_text(encoding="utf-8")
        system_prompt += '\n本次完整报告的 sample_id 必须原样填写：' + json.dumps(bundle['task']['sample_id'], ensure_ascii=False)
        system_prompt += '\n返回前检查：最外层是对象，包含 sample_id、summary、findings、limitations；建议数组只能放在 findings 内，不能单独返回数组。'
    return {"model": model, "enable_thinking": False, "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 5000,
            "response_format": {"type": "json_object"}, "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}]}


class ReviewFormatError(ValueError):
    """The response arrived, but its report envelope is missing or belongs to another sample."""

    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


def validate_review(review, bundle):
    """只验证结构与引用存在性；不将其等同于事实正确性。"""
    if not isinstance(review, dict):
        detail = '只返回了建议列表' if isinstance(review, list) else '未返回完整报告对象'
        raise ReviewFormatError('模型返回格式不完整：' + detail + '；需要包含 sample_id、summary、findings、limitations 的 JSON 对象。已保留原始回答，未自动重试。', 'invalid_report_envelope')
    missing = [key for key in ('sample_id', 'summary', 'findings', 'limitations') if key not in review]
    if missing:
        raise ReviewFormatError('模型报告缺少必要字段：' + '、'.join(missing) + '。已保留原始回答，未自动补造内容。', 'missing_report_fields')
    if review.get("sample_id") != bundle["task"]["sample_id"]:
        raise ReviewFormatError("模型输出的样本 ID 不匹配，已停止使用该报告", 'sample_id_mismatch')
    def require_text(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(label + " 必须是非空文本")
    require_text(review.get("summary"), "summary")
    findings = review.get("findings")
    if not isinstance(findings, list) or len(findings) > 3:
        raise ValueError("findings 必须是最多三项的数组")
    ids = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("finding 必须是对象")
        for key in ("id", "observation", "interpretation", "suggestion", "validation_plan"):
            require_text(finding.get(key), key)
        if finding["id"] in ids:
            raise ValueError("建议 ID 重复")
        ids.add(finding["id"])
        refs = finding.get("evidence_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in bundle["catalog"] for ref in refs):
            raise ValueError("建议引用了不存在的证据或没有证据")
        if finding.get("confidence") not in ("high", "medium", "low"):
            raise ValueError("confidence 不在允许范围内")
    limits = review.get("limitations")
    if not isinstance(limits, list) or not limits:
        raise ValueError("limitations 必须是非空数组")
    for item in limits:
        require_text(item, "limitation")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelNetworkError(ValueError):
    """尚未收到有效模型响应；不能据此判断服务端是否处理或计费。"""

    def __init__(self, message, partial_content="", transport=None):
        super().__init__(message)
        self.partial_content = partial_content
        self.transport = transport or {}


def read_sse_response(response, started=None):
    """合并 SSE 分片；用量分片的 choices 可以为空，不能将它当成回答。"""
    started = started if started is not None else time.monotonic()
    pieces, event_lines = [], []
    usage, response_id, model, finish = None, None, None, None
    done, chunks, byte_count, first_event = False, 0, 0, None

    def consume(data):
        nonlocal usage, response_id, model, finish, done, chunks, first_event
        if data == "[DONE]":
            done = True
            return
        chunk = json.loads(data)
        if not isinstance(chunk, dict) or "error" in chunk:
            raise ValueError("DashScope 流式接口返回错误事件，未接受结果")
        if first_event is None:
            first_event = round(time.monotonic() - started, 3)
        chunks += 1
        response_id = chunk.get("id") or response_id
        model = chunk.get("model") or model
        if chunk.get("usage") is not None:
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            content = (choice.get("delta") or {}).get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ValueError("流式回答分片必须为文本")
                pieces.append(content)
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]

    try:
        for raw in response:
            byte_count += len(raw)
            if byte_count > 16 * 1024 * 1024:
                raise ValueError("流式响应超过接收上限")
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if event_lines:
                    consume("\n".join(event_lines))
                    event_lines = []
                if done:
                    break
            elif line.startswith("data:"):
                event_lines.append(line[5:].lstrip(" "))
        if event_lines and not done:
            consume("\n".join(event_lines))
    except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
        raise ModelNetworkError("DashScope 流式连接中断；部分文本未作为有效结果接受。", "".join(pieces),
                                {"chunks": chunks, "error_type": type(error).__name__}) from None
    transport = {"mode": "sse", "chunks": chunks, "received_bytes": byte_count,
                 "first_event_seconds": first_event, "done_received": done}
    if not done or finish is None:
        raise ModelNetworkError("DashScope 流式响应缺少结束标记，拒绝将半份回答当作成功。", "".join(pieces), transport)
    return {"id": response_id, "model": model, "choices": [{"message": {"role": "assistant", "content": "".join(pieces)},
            "finish_reason": finish}], "usage": usage, "transport": transport}


def call_dashscope(payload):
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    base = os.environ.get("DASHSCOPE_BASE_URL", "").strip().rstrip("/")
    if not key or not base:
        raise ValueError("请在本机环境变量配置 DASHSCOPE_API_KEY 和 DASHSCOPE_BASE_URL")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".aliyuncs.com") or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("DASHSCOPE_BASE_URL 应为阿里云官方 HTTPS 基础接口地址")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + "/chat/completions", data=body,
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        started = time.monotonic()
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=120) as response:
            if payload.get("stream"):
                if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
                    raise ValueError("服务端未返回请求的 SSE 响应，未接受结果")
                result = read_sse_response(response, started)
                result["transport"]["request_bytes"] = len(body)
                return result
            return json.load(response)
    except urllib.error.HTTPError as error:
        # 不回显服务端原始错误体，防止其中包含请求材料或凭证。
        raise ValueError("DashScope HTTP %s；请核对模型权限、地域和额度。本次不自动重试。" % error.code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.RemoteDisconnected) as error:
        transport = {'failure_phase': 'awaiting_response', 'error_type': type(error).__name__,
                     'request_bytes': len(body)}
        if isinstance(error, urllib.error.URLError):
            transport['cause_type'] = type(error.reason).__name__
        raise ModelNetworkError("DashScope 连接失败、超时或在返回响应前中断；未取得有效模型回答，本次不自动重试。", transport=transport) from None


def render_report(review, bundle, metadata):
    coverage = bundle["coverage"]
    lines = ["# 教程内容 AI 复盘", "", "模型：" + metadata["model"], "",
             "状态：结构及引用检查通过；内容仍待人工核对。", "",
             "本次使用 %s/%s 张视频采样图、%s 条字幕，共 %s 张图片（含配套截图）。" % (
                 coverage["selected_frames"], coverage["available_frames"], coverage["subtitle_count"], coverage["image_count"]),
             "", "目标：" + bundle["task"]["tutorial_goal"], "", review["summary"], ""]
    for item in review["findings"]:
        lines.extend(["## " + item["id"], "", "**观察：** " + item["observation"], "",
                      "**可能影响（待验证）：** " + item["interpretation"], "",
                      "**建议：** " + item["suggestion"], "", "**验证方式：** " + item["validation_plan"], "",
                      "模型自报把握程度：" + item["confidence"] + "（未经校准）", "", "证据：", ""])
        for ref in item["evidence_ids"]:
            evidence = bundle["catalog"][ref]
            label = ref
            if "requested_time_seconds" in evidence:
                label += " · 请求截取位置 %.3f 秒" % evidence["requested_time_seconds"]
            if "local_path" in evidence:
                lines.append("- [%s](<%s>)" % (label, Path(evidence["local_path"]).as_posix()))
            else:
                lines.append("- " + label + "：" + json.dumps(evidence, ensure_ascii=False))
        lines.append("")
    lines.extend(["## 分析边界", ""] + ["- " + line for line in review["limitations"]])
    lines.extend(["", "本报告由一次模型调用生成。未执行源码、未测观众操作、未分析音频；引用通过不等于结论正确。", "",
                  "API 用量（服务端返回）：`" + json.dumps(metadata.get("usage"), ensure_ascii=False) + "`", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="调用 DashScope 分析教程证据；默认会产生 API 用量")
    parser.add_argument("--sample", default="data/samples/tutorial-001.json")
    parser.add_argument("--video-evidence", help="不指定时读取最近一次视频处理结果")
    parser.add_argument("--output-dir", default="reports/review", help="本次分析输出的父目录")
    parser.add_argument("--frame-limit", type=int, default=16)
    parser.add_argument("--model", default=os.environ.get("DASHSCOPE_MODEL") or "qwen3.8-flash")
    parser.add_argument("--dry-run", action="store_true", help="只整理证据，不调用 API")
    args = parser.parse_args()
    run_dir, metadata, started = None, {}, None
    try:
        sample = load_sample(resolve_path(args.sample, PROJECT_ROOT))
        path = args.video_evidence
        if not path:
            latest = json.loads((PROJECT_ROOT / "reports/video/latest.json").read_text(encoding="utf-8"))
            path = latest["data_path"]
        bundle, images = build_evidence(sample, resolve_path(path, PROJECT_ROOT), args.frame_limit)
        payload = build_request(bundle, images, args.model)
        run_dir = resolve_path(args.output_dir, PROJECT_ROOT) / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        run_dir.mkdir(parents=True)
        write_json(run_dir / "evidence-catalog.json", bundle)
        (run_dir / "prompt.md").write_text(payload["messages"][0]["content"], encoding="utf-8")
        metadata = {"model": args.model, "status": "prepared", "coverage": bundle["coverage"],
                    "video_evidence_path": str(resolve_path(path, PROJECT_ROOT)), "automatic_retries": 0}
        write_json(run_dir / "run.json", metadata)
        if args.dry_run:
            print("证据整理完成，未调用 API：" + str(run_dir))
            return 0
        print("调用 DashScope：%s，发送 %s 张图片；等待模型返回。" % (args.model, len(images)), flush=True)
        started = time.monotonic()
        response = call_dashscope(payload)
        choice = response["choices"][0]
        content = choice["message"]["content"]
        # 仅保存回答及用量，不保存请求中的图片编码和 Authorization。
        (run_dir / "model-output.txt").write_text(content, encoding="utf-8")
        metadata.update(elapsed_seconds=round(time.monotonic() - started, 2), usage=response.get("usage"),
                        response_id=response.get("id"), finish_reason=choice.get("finish_reason"), transport=response.get("transport"))
        write_json(run_dir / "run.json", metadata)
        if choice.get("finish_reason") != "stop":
            raise ValueError("模型回答未正常结束，保留原始输出供检查")
        review = json.loads(content)
        validate_review(review, bundle)
        write_json(run_dir / "review.json", review)
        (run_dir / "review.md").write_text(render_report(review, bundle, metadata), encoding="utf-8")
        metadata["status"] = "structure_and_references_valid_semantics_unverified"
        write_json(run_dir / "run.json", metadata)
        write_json(run_dir.parent / "latest.json", {"run_dir": str(run_dir), "report_path": str(run_dir / "review.md")})
        print("AI 复盘完成，结构及引用检查通过：" + str(run_dir / "review.md"))
        return 0
    except (OSError, ValueError, KeyError, IndexError, TypeError, zipfile.BadZipFile) as error:
        message = str(error)
        if run_dir:
            metadata.update(status="failed", error=message)
            if isinstance(error, ReviewFormatError):
                metadata['error_kind'] = error.kind
            if started is not None:
                metadata["elapsed_seconds"] = round(time.monotonic() - started, 2)
            if isinstance(error, ModelNetworkError):
                metadata.update(error_kind="network_or_incomplete_stream", transport=error.transport)
                if error.partial_content:
                    (run_dir / "partial-output.txt").write_text(error.partial_content, encoding="utf-8")
            write_json(run_dir / "run.json", metadata)
            write_json(run_dir / "failure.json", {"status": "failed", "error": message})
        print("复盘失败：" + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
