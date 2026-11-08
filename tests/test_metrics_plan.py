import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from platform_metrics import calculate_metrics
from workflow_plan import build_plan
from review_tutorial import build_evidence
from run_workflow import new_state, run_live


EXAMPLE = {"schema_version": 1, "source": "合成测试", "observation_hours": 24, "plays": 1000,
           "plays_definition": "24小时累计播放次数", "likes": 40, "likes_definition": "同窗口点赞数",
           "link_requests": 5, "link_requests_definition": "同窗口去重评论账号数"}


class MetricsPlanTests(unittest.TestCase):
    def test_separate_counts_and_recomputable_formulas(self):
        result = calculate_metrics(EXAMPLE)
        self.assertEqual([v["per_100_plays"] for v in result["calculations"]], [4, .5])
        self.assertEqual([v["count"] for v in result["calculations"]], [40, 5])
        self.assertNotIn("total_users", result)
        self.assertEqual(result["source"], EXAMPLE)

    def test_zero_denominator_does_not_invent_rate(self):
        result = calculate_metrics(dict(EXAMPLE, plays=0, likes=0, link_requests=0))
        self.assertEqual([v["per_100_plays"] for v in result["calculations"]], [None, None])

    def test_missing_is_not_zero(self):
        self.assertEqual(calculate_metrics(None)["status"], "skipped")
        result = calculate_metrics(dict(EXAMPLE, link_requests=None))
        self.assertEqual(len(result["calculations"]), 1)
        self.assertEqual(result["calculations"][0]["metric"], "likes")

    def test_invalid_numbers_definitions_and_unknown_fields_rejected(self):
        for changes in ({"likes": -1}, {"plays": True}, {"link_requests": 1.5},
                        {"observation_hours": float("nan")}, {"observation_hours": 0},
                        {"plays_definition": ""}, {"link_requests_definition": ""},
                        {"likes": None, "link_requests": None}, {"conversion_rate": 99}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                calculate_metrics(dict(EXAMPLE, **changes))

    def test_plan_branches_and_budget(self):
        sample = {"sample_id": "test", "tutorial_goal": "索要链接或点赞", "goal_revision": 2}
        without = build_plan(sample, 2)
        with_data = build_plan(dict(sample, platform_metrics=EXAMPLE), 2)
        self.assertEqual(without["tasks"][2]["action"], "skip")
        self.assertEqual(with_data["tasks"][2]["action"], "execute")
        self.assertFalse(build_plan(sample, 1)["ready"])
        self.assertEqual(without["goal_revision"], 2)

    def test_calculation_enters_citable_model_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "frame.jpg").write_bytes(b"synthetic-fixture")
            video = root / "video.mp4"
            evidence = root / "evidence.json"
            evidence.write_text(json.dumps({"sample_id": "test", "source_video": str(video),
                "timestamp_kind": "requested", "frames": [{"id": "frame-1", "image_path": "frame.jpg", "requested_time_seconds": 0}], "subtitles": []}))
            sample = {"sample_id": "test", "video": {"path": str(video)}, "platform_metrics": EXAMPLE}
            bundle, _ = build_evidence(sample, evidence)
            self.assertEqual(bundle["catalog"]["metrics-calculated"]["calculations"][1]["per_100_plays"], .5)

    def run_fixture(self, metrics, budget=2):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sample = {"schema_version": 1, "sample_id": "test", "tutorial_goal": "点赞", "audience": "观众",
                      "video": {"path": "placeholder.mp4"}, "platform_metrics": metrics}
            source = root / "input.json"
            source.write_text(json.dumps(sample), encoding="utf-8")
            state = new_state("live", budget, "test")
            called = []
            def script(run_root, stage, script, args, **kwargs):
                called.append(stage)
                output = Path(args[args.index("--output-dir") + 1])
                output.mkdir()
                (output / "latest.json").write_text(json.dumps({"data_path": str(root / "video-evidence.json"), "run_dir": str(output)}))
            with patch("run_workflow.run_script", side_effect=script):
                if budget < 2:
                    with self.assertRaisesRegex(ValueError, "预算"):
                        run_live(root, state, source, "fake")
                else:
                    run_live(root, state, source, "fake")
            events = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            return state, events, called

    def test_live_missing_data_is_skipped_before_analysis(self):
        state, events, called = self.run_fixture(None)
        self.assertEqual([s["status"] for s in state["stages"]], ["completed", "completed", "skipped", "completed", "completed"])
        self.assertIn("stage_skipped", [e["event"] for e in events])
        self.assertEqual(state["model_stage_attempts"], 2)
        self.assertEqual(called, ["materials", "video", "analysis", "verification"])

    def test_live_supplied_data_runs_without_extra_model_call(self):
        state, events, called = self.run_fixture(EXAMPLE)
        self.assertEqual(state["data_analysis"]["status"], "completed")
        self.assertEqual(state["model_stage_attempts"], 2)
        self.assertTrue(all(s["status"] == "completed" for s in state["stages"]))
        dispatched = [e for e in events if e['event'] == 'scheduler_decision' and e['operation'] == 'dispatch']
        self.assertEqual(len(dispatched), 5)
        self.assertEqual(dispatched[1]['task'], 'metrics')
        self.assertEqual(dispatched[-1]['task'], 'verification')

    def test_insufficient_budget_stops_before_tools(self):
        state, events, called = self.run_fixture(None, budget=1)
        self.assertEqual(called, [])
        self.assertEqual(state["model_stage_attempts"], 0)
