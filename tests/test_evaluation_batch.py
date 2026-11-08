import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from evaluation_batch import audit


class BatchTests(unittest.TestCase):
    def test_invalidated_input_excluded_without_hiding_recorded_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            root = workspace / 'reports/workflows/run-one'
            root.mkdir(parents=True)
            (workspace / 'data/samples').mkdir(parents=True)
            sample = {'tutorial_goal': 'like', 'platform_metrics': {'source': {'record_id': 'metric-test'}}}
            (root / 'sample.json').write_text(json.dumps(sample))
            (workspace / 'data/samples/video-one.json').write_text(json.dumps(sample))
            (root / 'workflow.json').write_text(json.dumps({'mode': 'live', 'sample_id': 'video-one', 'status': 'human_reviewed'}))
            with patch('screenshot_metrics.ScreenshotStore.read', return_value={'status': 'invalidated'}), patch('evaluation_batch.prepare') as prepare:
                result = audit(workspace, workspace / 'audit')
            prepare.assert_not_called()
            self.assertEqual(result['successful_live_attempts'], 1)
            self.assertEqual(result['cases'][0]['status'], 'excluded')
            self.assertIn('输入错配', result['cases'][0]['reason'])

    def test_failed_and_unscorable_success_stay_in_denominator(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            states = [('one', 'live', 'failed'), ('two', 'live', 'human_reviewed'),
                      ('three', 'replay', 'human_reviewed'), ('four', 'live', 'running')]
            for name, mode, status in states:
                root = workspace / 'reports/workflows' / name
                root.mkdir(parents=True)
                (root / 'workflow.json').write_text(json.dumps({'mode': mode, 'sample_id': name, 'status': status,
                    'stages': [{'id': 'analysis', 'status': 'failed'}] if status == 'failed' else []}))
            output = workspace / 'audit'
            result = audit(workspace, output)
            self.assertEqual(result['live_attempts'], 3)
            self.assertEqual(result['terminal_live_attempts'], 2)
            self.assertEqual(result['recorded_technical_completion_rate'], .5)
            self.assertEqual(result['failure_stages'], {'analysis': 1})
            self.assertEqual(result['cases'][0]['status'], 'excluded')
            self.assertEqual(result['new_model_calls'], 0)
            self.assertTrue((output / 'report.md').exists())
            with self.assertRaises(ValueError):
                audit(workspace, output)
