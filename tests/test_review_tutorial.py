import copy
import http.client
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from review_tutorial import ModelNetworkError, ReviewFormatError, build_request, build_evidence, call_dashscope, read_zip_description, select_frames, validate_review
from review_tutorial import main as review_main


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {"task": {"sample_id": "example"}, "catalog": {"frame-001": {}}}
        self.review = {"sample_id": "example", "summary": "有待验证", "findings": [
            {"id": "finding-1", "observation": "观察", "interpretation": "待验证的影响", "evidence_ids": ["frame-001"],
             "suggestion": "修改", "validation_plan": "实际操作", "confidence": "medium"}], "limitations": ["未运行"]}

    def test_valid_review(self):
        validate_review(self.review, self.bundle)

    def test_list_response_is_format_error_not_sample_mismatch(self):
        with self.assertRaises(ReviewFormatError) as caught:
            validate_review(self.review['findings'], self.bundle)
        self.assertEqual(caught.exception.kind, 'invalid_report_envelope')
        self.assertNotIn('ID 不匹配', str(caught.exception))

    def test_missing_envelope_fields_are_reported_without_fabrication(self):
        incomplete = {'findings': self.review['findings']}
        with self.assertRaises(ReviewFormatError) as caught:
            validate_review(incomplete, self.bundle)
        self.assertEqual(caught.exception.kind, 'missing_report_fields')
        self.assertEqual(set(incomplete), {'findings'})

    def test_default_prompt_binds_report_id_without_changing_other_agents(self):
        payload = build_request(self.bundle, [], 'fixture')
        self.assertIn('"example"', payload['messages'][0]['content'])
        self.assertIn('不能单独返回数组', payload['messages'][0]['content'])
        # Screenshot OCR / verifier / reviser use their own schemas and prompts.
        custom = build_request({'catalog': {}}, [], 'fixture', system_prompt='custom contract')
        self.assertEqual(custom['messages'][0]['content'], 'custom contract')

    def test_complete_but_malformed_response_is_retained_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle['coverage'] = {'image_count': 0}
            raw = json.dumps(self.review['findings'])
            response = {'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}]}
            with patch('sys.argv', ['review_tutorial.py', '--video-evidence', 'fixture.json', '--output-dir', directory]), \
                 patch('review_tutorial.load_sample', return_value={}), \
                 patch('review_tutorial.build_evidence', return_value=(self.bundle, [])), \
                 patch('review_tutorial.call_dashscope', return_value=response) as model:
                self.assertEqual(review_main(), 1)
            run = next(Path(directory).glob('run-*'))
            metadata = json.loads((run / 'run.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['error_kind'], 'invalid_report_envelope')
            self.assertEqual((run / 'model-output.txt').read_text(encoding='utf-8'), raw)
            self.assertFalse((run / 'review.json').exists())
            model.assert_called_once()

    def test_disconnect_is_reported_as_network_error_without_retry(self):
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-placeholder", "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1"}):
            with patch("review_tutorial.urllib.request.build_opener") as builder:
                builder.return_value.open.side_effect = http.client.RemoteDisconnected("closed")
                with self.assertRaisesRegex(ModelNetworkError, "响应前中断"):
                    call_dashscope({"model": "test-model"})
                builder.return_value.open.assert_called_once()

    def test_rejects_invented_reference(self):
        self.review["findings"][0]["evidence_ids"] = ["frame-999"]
        with self.assertRaisesRegex(ValueError, "证据"):
            validate_review(self.review, self.bundle)

    def test_analysis_network_failure_updates_metadata_and_preserves_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle["coverage"] = {"image_count": 0}
            with patch("sys.argv", ["review_tutorial.py", "--video-evidence", "fixture.json", "--output-dir", directory]), \
                 patch("review_tutorial.load_sample", return_value={}), \
                 patch("review_tutorial.build_evidence", return_value=(self.bundle, [])), \
                 patch("review_tutorial.call_dashscope", side_effect=ModelNetworkError("fixture connection closed", partial_content="unfinished")) as model:
                self.assertEqual(review_main(), 1)
            run = next(Path(directory).glob("run-*"))
            metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "failed")
            self.assertEqual(metadata["error_kind"], "network_or_incomplete_stream")
            self.assertIn("elapsed_seconds", metadata)
            self.assertEqual((run / "partial-output.txt").read_text(), "unfinished")
            self.assertFalse((run / "review.json").exists())
            model.assert_called_once()

    def test_rejects_wrong_sample(self):
        self.review["sample_id"] = "other"
        with self.assertRaisesRegex(ValueError, "样本"):
            validate_review(self.review, self.bundle)

    def test_requires_limitations_and_validation_plan(self):
        for key in ("validation_plan", "observation"):
            review = copy.deepcopy(self.review)
            del review["findings"][0][key]
            with self.assertRaises(ValueError):
                validate_review(review, self.bundle)
        self.review["limitations"] = []
        with self.assertRaises(ValueError):
            validate_review(self.review, self.bundle)

    def test_limits_findings(self):
        self.review["findings"] *= 4
        with self.assertRaises(ValueError):
            validate_review(self.review, self.bundle)

    def test_selection_includes_ends(self):
        result = select_frames(list(range(31)), 16)
        self.assertEqual(len(set(result)), 16)
        self.assertEqual((result[0], result[-1]), (0, 30))

    def test_reads_only_zip_readme_and_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("README.md", "打开 星际轨道.html")
                archive.writestr("main.js", "DO NOT SEND THIS SOURCE")
            result = read_zip_description(path)
            self.assertIn("main.js", result["files"])
            self.assertNotIn("DO NOT SEND", json.dumps(result))
            self.assertEqual(result["readmes"][0]["text"], "打开 星际轨道.html")

    def test_rejects_wrong_video_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps({"sample_id": "example", "source_video": "other.mp4"}))
            with self.assertRaisesRegex(ValueError, "另一个视频"):
                build_evidence({"sample_id": "example", "video": {"path": "expected.mp4"}}, path)


if __name__ == "__main__":
    unittest.main()
