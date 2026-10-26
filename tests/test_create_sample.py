import json
from pathlib import Path
import tempfile
import unittest

from check_materials import load_sample
from create_sample import create_sample


class SampleRegistrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "教程 甲.mp4").write_bytes(b"fixture-only-not-decodable")

    def create(self, **kwargs):
        args = dict(video='"教程 甲.mp4"', goal="完成操作", audience="初学者",
                    prior_knowledge="会下载", root=self.root)
        args.update(kwargs)
        return create_sample(**args)

    def test_independent_registration_compatible_with_pipeline(self):
        path = self.create()
        sample = load_sample(path)
        self.assertEqual(sample["video"]["path"], str(self.root / "教程 甲.mp4"))
        self.assertIsNone(sample["video"]["duration_seconds"])
        self.assertEqual(sample["supporting_materials"], [])
        self.assertIsNone(sample["source_delivery"])
        self.assertIsNone(sample["platform_metrics"])
        self.assertEqual(sample["status"], "registered_unreviewed")

    def test_next_id_preserves_existing_bytes(self):
        first = self.create()
        before = first.read_bytes()
        second = self.create(goal="另一目标")
        self.assertEqual(second.name, "tutorial-002.json")
        self.assertEqual(first.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.create(sample_id="tutorial-001")
        self.assertEqual(first.read_bytes(), before)

    def test_bad_id_cannot_escape_samples_directory(self):
        for identifier in ("../outside", "C:/outside", "", "A", "a" * 65):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.create(sample_id=identifier)
        self.assertFalse((self.root / "data").exists())

    def test_missing_file_and_context_do_not_create_sample(self):
        for override in (dict(video="missing.mp4"), dict(goal=" "), dict(audience=None), dict(prior_knowledge="")):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.create(**override)
        self.assertFalse((self.root / "data").exists())

    def test_broken_subtitle_rejected_before_registration(self):
        (self.root / "bad.srt").write_text("not srt", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "材料检查未通过"):
            self.create(transcript="bad.srt")
        self.assertFalse((self.root / "data").exists())

    def test_optional_subtitle_is_independent(self):
        (self.root / "字幕.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n示例\n", encoding="utf-8")
        sample = json.loads(self.create(transcript="字幕.srt").read_text(encoding="utf-8"))
        self.assertEqual(sample["transcript_path"], str(self.root / "字幕.srt"))


if __name__ == "__main__":
    unittest.main()
