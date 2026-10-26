import unittest

from prepare_video import format_time, plan_frames, read_settings, subtitles_at


class VideoPlanningTests(unittest.TestCase):
    def test_short_caption_gets_its_own_frame(self):
        cues = [{"start_seconds": 1.1, "end_seconds": 1.5, "text": "短字幕"}]
        plan = plan_frames(12, 5, cues)
        self.assertIn(1.3, [frame["requested_time_seconds"] for frame in plan])
        self.assertTrue(all(frame["requested_time_seconds"] < 12 for frame in plan))

    def test_coincident_points_are_combined(self):
        plan = plan_frames(11, 5, [{"start_seconds": 4, "end_seconds": 6}], True)
        points = [point for point in plan if point["requested_time_seconds"] == 5]
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["reasons"], ["interval", "subtitle_midpoint"])

    def test_subtitle_boundaries_do_not_double_match(self):
        cues = [{"start_seconds": 0, "end_seconds": 1, "text": "第一句"},
                {"start_seconds": 1, "end_seconds": 2, "text": "第二句"}]
        self.assertEqual(subtitles_at(cues, 1), ["第二句"])
        self.assertEqual(subtitles_at(cues, 2), [])

    def test_tiny_video_still_has_first_frame(self):
        self.assertEqual(plan_frames(0.02, 5, [])[0]["requested_time_seconds"], 0)

    def test_excessive_sampling_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "增大采样间隔"):
            plan_frames(45, 0.001, [])

    def test_invalid_settings_rejected(self):
        for interval in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                read_settings({"video_processing": {"frame_interval_seconds": interval}})

    def test_format_time_carries_milliseconds(self):
        self.assertEqual(format_time(59.9996), "01:00.000")


if __name__ == "__main__":
    unittest.main()
