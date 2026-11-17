import json
import unittest

from model_output import parse_model_json


class ModelOutputTests(unittest.TestCase):
    def test_exact_fence_unwrapped_without_changing_data(self):
        value = {'sample_id': 't-02', 'reason': '中文证据', 'text': '保留 ``` 字符'}
        raw = json.dumps(value, ensure_ascii=False)
        for text in (raw, '\ufeff' + raw, '```json\n' + raw + '\n```', '```\r\n' + raw + '\r\n```'):
            with self.subTest(text=text):
                meta = {}
                self.assertEqual(parse_model_json(text, meta), value)
                self.assertIn(meta['output_format'], ('json', 'markdown_json_fence'))

    def test_no_extraction_from_prose_partial_or_multiple_objects(self):
        for text in ('', '结论：{"a":1}', '```json\n{}', '{} {}', '```json\n{}\n```\n再见', '{"x":', '{"a":1,"a":2}', '{"a":NaN}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_model_json(text)

    def test_arrays_are_not_fabricated_into_valid_report(self):
        self.assertEqual(parse_model_json('```json\n[]\n```'), [])
