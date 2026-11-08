import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from evaluation_batch import audit


class BatchTests(unittest.TestCase):
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
