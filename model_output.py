"""Parse a complete model response without inventing or repairing its contents."""
import json
import re


def parse_model_json(content, metadata=None):
    if not isinstance(content, str) or not content.strip():
        raise ValueError('模型返回空回答，未取得可解析的 JSON')
    text = content.strip().lstrip('\ufeff').strip()
    wrapped = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n```', text,
                           flags=re.DOTALL | re.IGNORECASE)
    if wrapped:
        text = wrapped.group(1).strip()
    if metadata is not None:
        metadata['output_format'] = 'markdown_json_fence' if wrapped else 'json'

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('模型 JSON 含重复字段：' + key)
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('模型 JSON 含非标准数值：' + value)

    try:
        return json.loads(text, object_pairs_hook=unique_fields, parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        raise ValueError('模型未返回完整有效的 JSON（第 %s 行，第 %s 列）；原始回答已保留，未补造内容' %
                         (error.lineno, error.colno)) from None
