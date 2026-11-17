import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from intelligent_planner import validate_proposal, plan_with_model
from workflow_plan import build_plan
from run_workflow import new_state, run_live


def proposal(plan):
    return {'sample_id': plan['sample_id'], 'focus': 'action', 'sampling_interval': 2,
            'max_workers': 1, 'reason': '测试：目标为互动，检查引导',
            'tasks': [{k: t[k] for k in ('id', 'tool', 'action', 'depends_on', 'priority', 'reason')}
                      for t in plan['tasks']]}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.sample = {'sample_id': 'test', 'tutorial_goal': '点赞', 'video': {'path': 'synthetic.mp4'}}
        self.plan = build_plan(self.sample, 3)

    def test_reject_tools_skips_missing_deps_cycles_and_budget_fields(self):
        variants = []
        for field, value in [('tool', 'shell'), ('action', 'skip'), ('depends_on', [])]:
            item = proposal(self.plan)
            item['tasks'][3][field] = value
            variants.append(item)
        item = proposal(self.plan)
        item['tasks'][0]['depends_on'] = ['verification']
        variants.append(item)
        item = proposal(self.plan)
        item['budget'] = 999
        variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_proposal(item, self.plan)
        self.assertEqual(validate_proposal(proposal(self.plan), self.plan)['intelligent_strategy']['focus'], 'action')

    def test_invalid_output_retained_no_silent_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '[]'}}]}
            with patch('review_tutorial.call_dashscope', return_value=response) as call:
                with self.assertRaises(ValueError):
                    plan_with_model(Path(temp), self.sample, self.plan, 'fake')
            self.assertEqual(call.call_count, 1)
            self.assertEqual((Path(temp) / 'planning/model-output.txt').read_text(), '[]')
            self.assertEqual(json.loads((Path(temp) / 'planning/run.json').read_text(encoding='utf-8'))['status'], 'failed')

    def test_real_executor_consumes_validated_strategy_and_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'input.json'
            source.write_text(json.dumps(self.sample), encoding='utf-8')
            calls = []
            def script(run_root, stage, script, args, **kwargs):
                calls.append((stage, args))
                output = Path(args[args.index('--output-dir') + 1])
                output.mkdir()
                (output / 'latest.json').write_text(json.dumps({'data_path': 'fixture', 'run_dir': str(output)}))
            response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(proposal(self.plan))}}]}
            state = new_state('live', 3, 'test')
            with patch('run_workflow.load_sample', return_value=self.sample), patch('run_workflow.run_script', side_effect=script), patch('review_tutorial.call_dashscope', return_value=response):
                run_live(root, state, source, 'fake', intelligent_planning=True)
            self.assertEqual(state['model_stage_attempts'], 3)
            self.assertEqual(state['stages'][0]['status'], 'completed')
            self.assertIn('--interval', calls[1][1])
            self.assertIn('--focus', calls[2][1])
            self.assertEqual(calls[2][1][-1], 'action')
            events = [json.loads(line) for line in (root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(any(e['event'] == 'intelligent_plan_accepted' for e in events))

    def test_budget_preflight_stops_before_call(self):
        with tempfile.TemporaryDirectory() as temp, patch('review_tutorial.call_dashscope') as call:
            with self.assertRaisesRegex(ValueError, '三次预算'):
                run_live(Path(temp), new_state('live', 2, 'test'), Path('none'), 'fake', intelligent_planning=True)
            call.assert_not_called()

    def test_fenced_real_response_shape_requests_json_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temp:
            content = '```json\n' + json.dumps(proposal(self.plan)) + '\n```'
            response = {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}
            with patch('review_tutorial.call_dashscope', return_value=response) as call:
                plan = plan_with_model(Path(temp), self.sample, self.plan, 'fake')
            self.assertEqual(plan['sample_id'], 'test')
            self.assertEqual(call.call_args.args[0]['response_format'], {'type': 'json_object'})
            self.assertEqual((Path(temp)/'planning/model-output.txt').read_text(), content)
            meta = json.loads((Path(temp)/'planning/run.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['output_format'], 'markdown_json_fence')
            self.assertEqual(meta['status'], 'validated')

    def test_fenced_unauthorized_tool_still_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            item = proposal(self.plan)
            item['tasks'][0]['tool'] = 'shell'
            content = '```json\n' + json.dumps(item) + '\n```'
            with patch('review_tutorial.call_dashscope', return_value={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}):
                with self.assertRaisesRegex(ValueError, '未授权'):
                    plan_with_model(Path(temp), self.sample, self.plan, 'fake')
            self.assertFalse((Path(temp)/'planning/validated-plan.json').exists())
