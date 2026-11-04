import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from run_workflow import new_state, run_live


class StageRetryTests(unittest.TestCase):
    def exercise(self, failures, budget=4, limit=1):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);source=root/'input.json'
        source.write_text(json.dumps(dict(schema_version=1,sample_id='test',tutorial_goal='like',audience='viewers',
            video={'path':'fixture.mp4'},platform_metrics=None)),encoding='utf-8')
        calls=[];counts={};state=new_state('live',budget,'test')
        def script(run,stage,script,args,**kwargs):
            calls.append(stage);counts[stage]=counts.get(stage,0)+1
            dest=Path(args[args.index('--output-dir')+1]);dest.mkdir(exist_ok=True)
            if stage in ('analysis','verification'):
                attempt=dest/('run-%03d'%counts[stage]);attempt.mkdir()
                kind=failures.get((stage,counts[stage]))
                (attempt/'run.json').write_text(json.dumps({'error_kind':kind,'status':'failed' if kind else 'complete'}))
                if kind: raise ValueError('fixture stage failed')
                (dest/'latest.json').write_text(json.dumps({'run_dir':str(attempt)}))
            elif stage=='video': (dest/'latest.json').write_text(json.dumps({'data_path':str(dest/'evidence.json')}))
        with patch('run_workflow.run_script',side_effect=script),patch('run_workflow.time.sleep'):
            try: run_live(root,state,source,'fixture',model_retry_limit=limit)
            except ValueError: pass
        return state,calls,[json.loads(line) for line in (root/'events.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_analysis_and_verification_each_recover_once_within_total_budget(self):
        state,calls,events=self.exercise({('analysis',1):'invalid_report_envelope',('verification',1):'network_or_incomplete_stream'})
        self.assertEqual(calls,['materials','video','analysis','analysis','verification','verification'])
        self.assertEqual(state['model_stage_attempts'],4)
        self.assertEqual([s['status'] for s in state['stages']],['completed','completed','skipped','completed','completed'])
        retries=[e for e in events if e['event']=='model_stage_retry_reserved']
        self.assertEqual(len(retries),2);self.assertTrue(retries[1]['reused_analysis'])
        self.assertEqual(state['stages'][3]['attempts'][0]['status'],'failed')

    def test_verification_retry_does_not_regenerate_successful_analysis(self):
        state,calls,_=self.exercise({('verification',1):'network_or_incomplete_stream'})
        self.assertEqual(calls.count('analysis'),1);self.assertEqual(calls.count('verification'),2)
        self.assertEqual(state['model_stage_attempts'],3)

    def test_repeated_failure_stops_after_limit_and_preserves_both_attempts(self):
        state,calls,_=self.exercise({('analysis',1):'network_or_incomplete_stream',('analysis',2):'network_or_incomplete_stream'})
        self.assertEqual(state['status'],'failed');self.assertEqual(calls.count('analysis'),2)
        self.assertNotIn('verification',calls);self.assertEqual(len(state['stages'][3]['attempts']),2)

    def test_no_retry_for_local_failure_or_without_budget_for_verification(self):
        for failures,budget in (({('analysis',1):'local_failure'},4),({('analysis',1):'invalid_report_envelope'},2)):
            with self.subTest(budget=budget):
                state,calls,_=self.exercise(failures,budget=budget)
                self.assertEqual(calls.count('analysis'),1);self.assertEqual(state['model_stage_attempts'],1)

    def test_old_no_retry_requests_retain_original_behavior(self):
        state,calls,_=self.exercise({('analysis',1):'network_or_incomplete_stream'},limit=0)
        self.assertEqual(calls.count('analysis'),1);self.assertEqual(state['status'],'failed')
