"""对同一作品、同一观察窗口的人工录入数据做可复算汇总，不调用模型。"""
import argparse
import json
import math
from datetime import datetime
from pathlib import Path


LIMITATIONS = [
    "数据为用户录入，程序未连接平台核验。",
    "点赞与索要链接分别统计，不能相加为去重人数。",
    "每百次播放对应的行为数不是观众转化概率；播放次数可能包含重复观看。",
    "单次汇总不能定位观众流失秒数，不能证明修改导致流量或互动变化。",
]


def calculate_metrics(raw):
    if raw is None:
        return {"status": "skipped", "reason": "未提供同一观察窗口的平台数据", "calculations": [],
                "limitations": list(LIMITATIONS)}
    if isinstance(raw, dict) and type(raw.get('schema_version')) is int and raw['schema_version'] == 2:
        return calculate_screenshot(raw)
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("platform_metrics 必须是 schema_version=1 的对象或 null")
    fields = {"schema_version", "source", "observation_hours", "plays", "likes", "link_requests",
              "plays_definition", "likes_definition", "link_requests_definition"}
    if set(raw) - fields:
        raise ValueError("平台数据包含不支持的字段，请按 metrics.example.json 录入")
    for key in ("source", "plays_definition"):
        if not isinstance(raw.get(key), str) or not 0 < len(raw[key].strip()) <= 1000:
            raise ValueError("平台数据缺少统计口径：" + key)
    hours = raw.get("observation_hours")
    if type(hours) not in (int, float) or not math.isfinite(hours) or hours <= 0:
        raise ValueError("observation_hours 必须是发布后经过的正数小时")
    plays = raw.get("plays")
    if type(plays) is not int or not 0 <= plays <= 10**12:
        raise ValueError("plays 必须是非负整数，且不超过一万亿")
    calculations = []
    for key, label in (("likes", "点赞"), ("link_requests", "索要链接")):
        count = raw.get(key)
        if count is None:
            continue  # 未记录与确实为零分开。
        if type(count) is not int or not 0 <= count <= 10**12:
            raise ValueError(key + " 必须是非负整数或 null")
        definition = raw.get(key + "_definition")
        if not isinstance(definition, str) or not 0 < len(definition.strip()) <= 1000:
            raise ValueError("平台数据缺少统计口径：" + key + "_definition")
        calculations.append({"metric": key, "label": label, "count": count, "plays": plays,
            "formula": key + " / plays * 100", "per_100_plays": round(count / plays * 100, 4) if plays else None,
            "reason": "播放次数为零，不计算比值" if not plays else "按所填同一观察窗口计算"})
    if not calculations:
        raise ValueError("至少填写点赞或索要链接中的一项实际计数；未知项使用 null")
    return {"status": "completed", "source": dict(raw), "calculations": calculations,
            "limitations": list(LIMITATIONS)}


def calculate_screenshot(raw):
    """Confirmed same-screenshot card values; no invented observation duration."""
    counts = {'plays': '播放', 'likes': '点赞', 'comments': '评论', 'shares': '分享',
              'favorites': '收藏', 'danmaku': '弹幕'}
    rates = {'completion_rate': '完播率', 'bounce_2s_rate': '2秒跳出率'}
    required = {'schema_version', 'source', 'observed_at', 'scope', 'note', 'metrics', 'approximate_fields'}
    if not required <= set(raw) or set(raw) - required - {'published_at'}:
        raise ValueError('截图平台数据字段不完整')
    if not isinstance(raw['source'], dict) or set(raw['source']) != {'record_id', 'sha256', 'confirmed_at', 'title', 'sample_id'}:
        raise ValueError('截图平台数据缺少来源')
    if any(not isinstance(v, str) or not v.strip() for v in raw['source'].values()):
        raise ValueError('截图来源字段无效')
    try:
        if datetime.fromisoformat(raw['observed_at']).tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('截图统计时间无效') from None
    if 'published_at' in raw:
        try:
            published = datetime.fromisoformat(raw['published_at'])
            if published.tzinfo is None or published > datetime.fromisoformat(raw['observed_at']):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('截图发布时间无效或晚于统计时间') from None
    if raw['scope'] not in ('unknown', 'cumulative', 'period') or not isinstance(raw['note'], str):
        raise ValueError('截图统计范围无效')
    if raw['scope'] == 'period' and not raw['note'].strip():
        raise ValueError('指定时段缺少统计说明')
    values = raw['metrics']
    if not isinstance(values, dict) or set(values) != set(counts) | set(rates):
        raise ValueError('截图指标不完整')
    for key, value in values.items():
        if value is None:
            continue
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                or value > (100 if key in rates else 10**12)
                or (key in counts and type(value) is not int)):
            raise ValueError('截图指标数值无效：' + key)
    if all(v is None for v in values.values()):
        raise ValueError('截图没有已核对指标')
    if not isinstance(raw['approximate_fields'], list) or any(k not in counts for k in raw['approximate_fields']):
        raise ValueError('截图精度标记无效')
    limitations = list(LIMITATIONS) + ['截图未记录索要链接人数；评论量不能代替索要链接人数。',
        '已保存核对后的发布时间；跨视频比较仍需确认统计口径和观察窗口一致。' if 'published_at' in raw else
        '未提供发布时间，不推算发布后经过小时数；不能据此进行同发布时长的跨视频比较。']
    calculations = []
    plays = values['plays']
    if raw['scope'] == 'unknown':
        limitations.append('统计范围尚未确认，仅展示已核对值，不计算每百次播放比值。')
    elif plays is not None:
        for key in ('likes', 'comments', 'shares', 'favorites', 'danmaku'):
            if values[key] is not None:
                calculations.append(dict(metric=key, label=counts[key], count=values[key], plays=plays,
                    formula=key + ' / plays * 100', per_100_plays=round(values[key]/plays*100, 4) if plays else None,
                    reason='同张截图、用户确认统计范围' if plays else '播放次数为零，不计算比值'))
    if raw['approximate_fields']:
        limitations.append('含万／亿的展示值已换算，比值为近似值。')
    return dict(status='completed', source=dict(raw), calculations=calculations,
                reported_metrics=dict(values), limitations=limitations)


def main():
    parser = argparse.ArgumentParser(description="计算一份人工录入的平台数据；不调用模型")
    parser.add_argument("--input", required=True, help="平台数据 JSON")
    parser.add_argument("--output", required=True, help="结果 JSON")
    args = parser.parse_args()
    result = calculate_metrics(json.loads(Path(args.input).read_text(encoding="utf-8-sig")))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(output))


if __name__ == "__main__":
    main()
