import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from summarize_human_evaluation import summarize


class SummaryTests(unittest.TestCase):
    def test_exclusions_and_partial_snapshots_never_inflate_quality_scores(self):
        counts = {'total': 3, 'judged': 3, 'pending': 0, 'supported': 1, 'revise': 2, 'insufficient_evidence': 0,
                  'supported_proportion': 1/3, 'coverage': 1.0}
        case = {'status': 'human_scored', 'annotation_coverage_complete': True,
                'arms': {arm: {'findings': counts, 'citations': counts, 'summary_verdict': 'revise'}
                         for arm in ('single_analysis', 'analysis_plus_verification')}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch, output = root / 'batch', root / 'output'
            batch.mkdir(); output.mkdir()
            for name in ('tutorial-good', 'tutorial-wrong', 'confirmed-example'):
                (batch / name).mkdir()
            (batch / 'case-exclusions.json').write_text(json.dumps({'cases': {'tutorial-wrong': {'reason': 'wrong video'}}}))
            with patch('summarize_human_evaluation.score', side_effect=lambda p: copy.deepcopy(case)) as score:
                result = summarize(batch, output)
            self.assertEqual(score.call_count, 2)
            self.assertEqual(result['completed_packets'], ['tutorial-good'])
            self.assertEqual(result['aggregate']['single_analysis']['findings']['total'], 3)
            self.assertEqual(result['aggregate']['single_analysis']['findings']['supported_proportion'], 1/3)
