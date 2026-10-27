"""对同一作品、同一观察窗口的人工录入数据做可复算汇总，不调用模型。"""
import argparse
import json
import math
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
