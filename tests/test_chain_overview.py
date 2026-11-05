import unittest
import json,threading,urllib.request,urllib.error,base64
from unittest.mock import patch
from pathlib import Path
import test_creation_cycles as fixtures
from create_sample import create_sample
from chain_overview import overview
from review_server import ReviewServer
from workflow_jobs import JobStore


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CycleTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store
        root=self.fixture.applied();payload=self.fixture.comparison_payload(root)
        root=self.store.compare(payload)
        self.root=self.store.decide(dict(id=root['id'],revision=root['revision'],decisions=[dict(finding_id='f1',decision='inconclusive',note='synthetic')]))
        self.a,self.b=payload['record_a'],payload['record_b']
        original=self.store.source
        def source(run):
            value=original('run-test')
            return dict(value,run=run,sample_id='test-b') if run=='run-b' else original(run)
        p=patch.object(self.store,'source',side_effect=source);p.start();self.addCleanup(p.stop)

    def child(self,title='child'):
        video=self.fixture.fixture.workspace/'c.mp4';video.write_bytes(b'synthetic')
        if not self.store.jobs.sample_path('test-c').exists():
            create_sample(str(video),'运行','观众','无',sample_id='test-c',root=self.fixture.fixture.workspace)
        child=self.store.create(dict(self.fixture.create,source_run='run-b',parent_id=self.root['id'],title=title))
        return self.store.implement(dict(id=child['id'],revision=child['revision'],sample_id='test-c',executions=[dict(finding_id='f1',status='applied',note='next work',evidence='')]))

    def compare_child(self,child,b=None,scope='cumulative',observed='2026-11-01T12:00:00+08:00'):
        c=self.fixture.screenshot('test-c',likes='80',scope=scope,observed=observed)
        return self.store.compare(dict(id=child['id'],revision=child['revision'],record_a=b or self.b,record_b=c['id'],published_a='2026-10-31T12:00:00+08:00',published_b='2026-10-31T12:00:00+08:00',screenshots_match=True,same_goal_confirmed=True,confounders='synthetic'))

    def test_three_videos_deduplicate_middle_and_do_not_join_unrelated_cycles(self):
        self.compare_child(self.child())
        self.store.create(dict(self.fixture.create,title='unrelated'))
        data=overview(self.store,self.root['id'])
        self.assertEqual([n['sample_id'] for n in data['nodes']],['test','test-b','test-c'])
        self.assertEqual([n['values']['likes_per_100'] for n in data['nodes']],[4,6,8])
        self.assertTrue(data['nodes'][2]['previous_compatible'])
        self.assertEqual(len(data['cycles']),2)

    def test_unknown_scope_and_different_window_are_shown_but_not_comparable(self):
        self.compare_child(self.child(),scope='unknown')
        data=overview(self.store,self.root['id'])
        self.assertEqual(data['nodes'][-1]['status'],'unaligned')
        self.assertEqual(data['nodes'][-1]['values']['likes'],80)
        self.assertFalse(data['nodes'][-1]['previous_compatible'])
        changed=overview(self.store,self.root['id'],48)
        self.assertFalse(any(n['status']=='comparable' for n in changed['nodes']))
        with self.assertRaises(ValueError):overview(self.store,self.root['id'],float('nan'))

    def test_conflicting_confirmations_are_not_automatically_selected(self):
        different=self.fixture.screenshot('test-b',likes='90')
        self.compare_child(self.child(),b=different['id'])
        data=overview(self.store,self.root['id'])
        self.assertEqual(data['nodes'][1]['status'],'conflict')
        self.assertIsNone(data['nodes'][1]['values']['likes'])
        self.assertEqual(len(data['nodes'][1]['record_options']),2)

    def test_fork_requires_selection_and_keeps_shared_ancestors(self):
        first=self.child('branch one');second=self.child('branch two')
        data=overview(self.store,self.root['id'])
        self.assertEqual(len(data['branches']),2)
        self.assertEqual(len(data['nodes']),2)
        selected=overview(self.store,second['id'])
        self.assertEqual([r['id'] for r in selected['cycles']],[self.root['id'],second['id']])

    def test_tampered_screenshot_not_used_and_read_does_not_write_cycles(self):
        before=(self.store.root(self.root['id'])/'record.json').read_bytes()
        image,_=self.store.metrics.image(self.b);image.write_bytes(b'changed')
        data=overview(self.store,self.root['id'])
        self.assertEqual(data['nodes'][1]['status'],'unaligned')
        self.assertIsNone(data['nodes'][1]['values']['likes'])
        self.assertEqual((self.store.root(self.root['id'])/'record.json').read_bytes(),before)

    def test_missing_and_zero_denominator_are_distinct(self):
        child=self.child()
        self.assertEqual(overview(self.store,child['id'])['nodes'][-1]['status'],'missing')
        c=self.fixture.screenshot('test-c',likes='0');image,_=self.store.metrics.image(c['id'])
        new=self.store.metrics.upload({'image_base64':base64.b64encode(image.read_bytes()).decode()})
        payload={k:c['confirmation'][k] for k in ('reviewed','title','sample_id','observed_at','scope','metrics','note')}
        payload['metrics']['plays']='0';payload['metrics']['likes']='0'
        zero=self.store.metrics.confirm(new['id'],dict(payload,published_at='2026-10-31T12:00:00+08:00'))
        self.store.jobs.materials('test-c',dict(sample_id='test-c',revision=0,metrics_record_id=zero['id'],match_confirmed=True))
        node=overview(self.store,child['id'])['nodes'][-1]
        self.assertEqual(node['values']['plays'],0);self.assertEqual(node['values']['likes'],0)
        self.assertIsNone(node['values']['likes_per_100'])

    def test_goal_mismatch_does_not_enter_common_trend(self):
        historical = self.compare_child(self.child())
        historical.pop('chain_goal', None)
        historical['target']['goal'] = 'another goal'
        self.store.commit(historical, 'legacy_test_fixture')
        node=overview(self.store,self.root['id'])['nodes'][-1]
        self.assertEqual(node['status'],'unaligned');self.assertIn('目标口径',node['reason'])

    def test_chain_registers_with_root_goal_and_rejects_mismatch_before_plan(self):
        workspace = self.fixture.fixture.workspace
        jobs = JobStore(workspace, self.store.target_goal)
        planned = self.store.create(dict(self.fixture.create, source_run='run-b', parent_id=self.root['id']))
        video = workspace / 'new.mp4'
        video.write_bytes(b'synthetic')
        different = jobs.register(dict(video=str(video), goal='another goal', audience='viewers', prior_knowledge='none'))
        with self.assertRaisesRegex(ValueError, '目标与创作链不一致'):
            jobs.plan(dict(different, cycle_id=planned['id'], max_model_calls=2, max_repair_rounds=0))
        self.assertEqual(list(jobs.folder.glob('job-*')), [])
        with self.assertRaisesRegex(ValueError, '目标须与创作链一致'):
            self.store.implement(dict(different, id=planned['id'], revision=planned['revision']))
        same = jobs.register(dict(video=str(video), cycle_id=planned['id'], audience='viewers', prior_knowledge='none'))
        self.assertEqual(jobs.samples()['samples'][-1]['goal'], self.root['source']['goal'])
        self.assertEqual(self.store.target_goal(planned['id'], same['sample_id']), self.root['source']['goal'])

    def test_read_only_http_chain_endpoint_and_input_validation(self):
        server=ReviewServer(workspace=self.fixture.fixture.workspace)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base='http://127.0.0.1:%s'%server.server_port
        with opener.open(base+'/api/cycles/chain?id='+self.root['id']) as response:
            self.assertEqual(len(json.load(response)['nodes']),2)
        with self.assertRaises(urllib.error.HTTPError):
            opener.open(base+'/api/cycles/chain?id='+self.root['id']+'&hours=nan')
