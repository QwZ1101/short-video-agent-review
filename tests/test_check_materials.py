import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from check_materials import check_materials, load_sample


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # 非空占位文件只证明可读，不代表能解码为视频。
        (self.root / "video.mp4").write_bytes(b"fixture-not-a-real-video")
        self.sample = {
            "schema_version": 1, "sample_id": "test", "audience": "初学者",
            "tutorial_goal": "找到并运行源码",
            "video": {"path": "video.mp4", "duration_seconds": 45},
            "supporting_materials": [],
        }

    def test_missing_required_video_blocks(self):
        self.sample["video"]["path"] = "missing.mp4"
        report = check_materials(self.sample, self.root)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["materials"][0]["status"], "missing")

    def test_optional_data_does_not_block_or_claim_video_decoded(self):
        report = check_materials(self.sample, self.root)
        self.assertEqual(report["status"], "ready_with_gaps")
        self.assertIn("尚未解码", report["materials"][0]["message"])
        self.assertEqual(report["materials"][1]["status"], "not_provided")

    def test_subtitle_outside_duration_is_reported(self):
        (self.root / "bad.srt").write_text("1\n00:00:44,000 --> 00:00:46,000\n超出视频\n", encoding="utf-8")
        self.sample["transcript_path"] = "bad.srt"
        report = check_materials(self.sample, self.root)
        self.assertEqual(report["materials"][1]["status"], "invalid")
        self.assertIn("超出", report["materials"][1]["message"])

    def test_subtitle_missing_time_is_reported(self):
        (self.root / "bad.srt").write_text("1\n这是错误格式\n字幕\n", encoding="utf-8")
        self.sample["transcript_path"] = "bad.srt"
        self.assertEqual(check_materials(self.sample, self.root)["materials"][1]["status"], "invalid")

    def test_chinese_zip_and_crlf_subtitles(self):
        (self.root / "字幕.srt").write_bytes("1\r\n00:00:01,000 --> 00:00:02,500\r\n打开网页\r\n".encode("utf-8-sig"))
        with zipfile.ZipFile(self.root / "源码.zip", "w") as archive:
            archive.writestr("星际轨道.html", "<html></html>")
            archive.writestr("README.md", "直接打开 HTML")
        self.sample["transcript_path"] = "字幕.srt"
        self.sample["supporting_materials"] = [{"id": "download", "type": "zip", "path": "源码.zip"}]
        report = check_materials(self.sample, self.root)
        self.assertEqual(report["materials"][1]["details"]["cue_count"], 1)
        self.assertEqual(report["materials"][2]["details"]["html_files"], ["星际轨道.html"])
        self.assertFalse((self.root / "星际轨道.html").exists())

    def test_broken_zip_is_reported(self):
        (self.root / "bad.zip").write_bytes(b"not-a-zip")
        self.sample["supporting_materials"] = [{"id": "download", "type": "zip", "path": "bad.zip"}]
        self.assertEqual(check_materials(self.sample, self.root)["materials"][2]["status"], "invalid")

    def test_invalid_config_has_clear_error(self):
        self.sample["supporting_materials"] = [{"id": "video", "type": "zip", "path": "a.zip"}]
        path = self.root / "sample.json"
        path.write_text(json.dumps(self.sample), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重复"):
            load_sample(path)


if __name__ == "__main__":
    unittest.main()
