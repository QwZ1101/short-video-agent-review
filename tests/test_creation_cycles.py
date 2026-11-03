import base64
import copy
from datetime import datetime, timedelta, timezone
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import test_human_review as fixtures
from creation_cycles import CycleStore, compare
from create_sample import create_sample
from human_review import atomic_json, digest
from review_server import ReviewServer
from screenshot_metrics import FIELDS


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HumanReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.reviews = self.fixture.store
        self.reviews.save('run-test', self.fixture.approve(), True)
        self.store = CycleStore(self.reviews)
        for name in ('test', 'test-b'):
            video = self.fixture.workspace / (name + '.mp4')
            video.write_bytes(b'synthetic video')
            create_sample(str(video), '运行', '观众', '无', sample_id=name, root=self.fixture.workspace)
        self.create = dict(source_run='run-test', title='下一条合成验收',
            items=[dict(finding_id='f1', planned_change='前 2 秒展示成品')], window_hours=24,
            tolerance_minutes=30, primary_metric='likes_per_100', expected_direction='increase')

    def screenshot(self, sample, observed='2026-11-01T12:00:00+08:00', scope='cumulative', likes='40'):
        r = self.store.metrics.upload({'image_base64': base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'synthetic fixture').decode()})
        metrics = {k: None for k in FIELDS}
        metrics.update(plays='1000', likes=likes, completion_rate='2.39%', bounce_2s_rate='42.16%')
        return self.store.metrics.confirm(r['id'], dict(reviewed=True, title='合成截图', sample_id=sample,
            observed_at=observed, scope=scope, metrics=metrics, note='合成验收数据'))

    def applied(self):
        r = self.store.create(self.create)
        return self.store.implement(dict(id=r['id'], revision=r['revision'], sample_id='test-b',
            executions=[dict(finding_id='f1', status='applied', note='先展示成品', evidence='B 00:00～00:02')]))

    def comparison_payload(self, r):
        a = self.screenshot('test')
        b = self.screenshot('test-b', likes='60')
        return dict(id=r['id'], revision=r['revision'], record_a=a['id'], record_b=b['id'],
            published_a='2026-10-31T12:00:00+08:00', published_b='2026-10-31T12:00:00+08:00',
            screenshots_match=True, same_goal_confirmed=True, confounders='题材不同，流量来源未知')

    def test_select_only_one_or_two_and_freeze_source_without_mutating_review(self):
        before = digest(self.fixture.root / 'human-feedback.json')
        for items in ([], self.create['items'] * 3, self.create['items'] * 2,
                      [dict(finding_id='unknown', planned_change='x')], [dict(finding_id='f1', planned_change='')]):
            with self.assertRaises(ValueError): self.store.create(dict(self.create, items=items))
        r = self.store.create(self.create)
        self.assertEqual(r['items'][0]['suggestion'], '改善')
        self.assertEqual(digest(self.fixture.root / 'human-feedback.json'), before)
        self.assertFalse((self.fixture.root / 'improvement-tasks.json').exists())
        self.assertEqual(r['status'], 'planned')

    def test_partial_submitted_feedback_allows_only_explicitly_accepted_findings(self):
        fixture = fixtures.HumanReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        store = CycleStore(fixture.store)
        self.assertEqual(store.sources()['runs'], [])
        with self.assertRaisesRegex(ValueError, '草稿'):
            store.create(self.create)
        form = fixture.approve()
        form['summary'].update(decision='unsure', note='总结还需补证')
        feedback = fixture.store.save('run-test', form, True)
        self.assertEqual(feedback['status'], 'needs_evidence')
        self.assertEqual(len(store.sources()['runs']), 1)
        record = store.create(self.create)
        self.assertEqual(record['items'][0]['finding_id'], 'f1')
        feedback['findings'][0].update(decision='unsure', note='该建议也尚未确定')
        fixture.store.save('run-test', feedback, True)
        self.assertEqual(store.sources()['runs'], [])
        with self.assertRaisesRegex(ValueError, '至少明确认可'):
            store.create(self.create)

    def test_full_cycle_computes_changes_but_decision_stays_human(self):
        r = self.applied()
        r = self.store.compare(self.comparison_payload(r))
        result = r['comparison']['result']
        self.assertEqual(result['status'], 'ready')
        row = next(x for x in result['rows'] if x['metric'] == 'likes_per_100')
        self.assertEqual((row['a'], row['b'], row['delta'], row['relative_percent']), (4, 6, 2, 50))
        self.assertTrue(result['retrospective'])
        self.assertEqual(r['decisions'], [])
        r = self.store.decide(dict(id=r['id'], revision=r['revision'],
            decisions=[dict(finding_id='f1', decision='retry', note='再试一条相似题材，观察是否重复出现')]))
        self.assertEqual(r['status'], 'decided')
        self.assertIn('再试一条', self.store.report(r['id']))
        with self.assertRaisesRegex(ValueError, '冻结'):
            self.store.defer(dict(id=r['id'], revision=r['revision'], reason='x'))

    def test_window_mismatch_unknown_scope_zero_and_missing_block_comparison(self):
        r = self.applied()
        payload = self.comparison_payload(r)
        a = self.store.metrics.for_workflow(payload['record_a'], 'test', True)
        b = self.store.metrics.for_workflow(payload['record_b'], 'test-b', True)
        for change in ({'observed_at': '2026-11-02T12:00:00+08:00'}, {'scope': 'unknown'},
                       {'metrics': dict(b['metrics'], plays=0)}, {'metrics': dict(b['metrics'], likes=None)}):
            result = compare(r, a, dict(b, **change), payload['published_a'], payload['published_b'])
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['rows'], [])
        # Both near 24h but 50 minutes apart still fails a 30-minute pair tolerance.
        result = compare(r, dict(a, observed_at='2026-11-01T11:35:00+08:00'),
            dict(b, observed_at='2026-11-01T12:25:00+08:00'), payload['published_a'], payload['published_b'])
        self.assertEqual(result['status'], 'blocked')
        # Platform rates use percentage-point difference, zero baseline has no relative percent.
        result = compare(r, dict(a, metrics=dict(a['metrics'], likes=0, completion_rate=2)),
            dict(b, metrics=dict(b['metrics'], completion_rate=3)), payload['published_a'], payload['published_b'])
        self.assertIsNone(result['rows'][0]['relative_percent'])
        rate = next(x for x in result['rows'] if x['metric'] == 'completion_rate')
        self.assertEqual((rate['delta'], rate['delta_unit']), (1, '个百分点'))

    def test_missing_baseline_can_end_inconclusive_and_link_next_round(self):
        r = self.applied()
        r = self.store.defer(dict(id=r['id'], revision=r['revision'], reason='A 没有 24 小时截图，下次按时采集'))
        with self.assertRaisesRegex(ValueError, '只能'):
            self.store.decide(dict(id=r['id'], revision=r['revision'], decisions=[dict(finding_id='f1', decision='keep', note='有效')]))
        r = self.store.decide(dict(id=r['id'], revision=r['revision'], decisions=[dict(finding_id='f1', decision='inconclusive', note='下次从 B 继续采样')]))
        with self.assertRaisesRegex(ValueError, '视频 B'):
            self.store.create(dict(self.create, parent_id=r['id']))
        original_source = self.store.source
        def source(run):
            if run == 'run-b':
                return dict(original_source('run-test'), run='run-b', sample_id='test-b')
            return original_source(run)
        with patch.object(self.store, 'source', side_effect=source):
            child = self.store.create(dict(self.create, parent_id=r['id'], source_run='run-b'))
        self.assertEqual(child['parent_id'], r['id'])
        self.assertEqual(child['source']['sample_id'], 'test-b')

    def test_editing_implementation_invalidates_current_comparison_and_versions_are_checked(self):
        r = self.applied()
        compared = self.store.compare(self.comparison_payload(r))
        with self.assertRaisesRegex(ValueError, '更新'):
            self.store.defer(dict(id=r['id'], revision=r['revision'], reason='stale'))
        updated = self.store.implement(dict(id=compared['id'], revision=compared['revision'], sample_id='test-b',
            executions=[dict(finding_id='f1', status='partial', note='只展示一秒', evidence='B-v2 00:00')]))
        self.assertIsNone(updated['comparison'])
        self.assertIsNotNone(json.loads((self.store.root(r['id']) / 'history' / compared['history_file']).read_text(encoding='utf-8'))['comparison'])
        with self.assertRaisesRegex(ValueError, '新视频 B'):
            self.store.implement(dict(id=updated['id'], revision=updated['revision'], sample_id='test'))

    def test_identity_confirmation_source_and_history_tampering_are_rejected(self):
        r = self.applied()
        payload = self.comparison_payload(r)
        for change in ({'screenshots_match': False}, {'same_goal_confirmed': False},
                       {'record_b': payload['record_a']}):
            with self.assertRaises(ValueError): self.store.compare(dict(payload, **change))
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.store.compare(dict(payload, record_a=payload['record_b'], record_b=payload['record_a']))
        r = self.store.compare(payload)
        image, _ = self.store.metrics.image(payload['record_a'])
        image.write_bytes(b'changed image')
        with self.assertRaisesRegex(ValueError, '原始截图已改变'):
            self.store.decide(dict(id=r['id'], revision=r['revision'], decisions=[dict(finding_id='f1', decision='retry', note='x')]))
        r['title'] = 'tampered'
        atomic_json(self.store.root(r['id']) / 'record.json', r)
        with self.assertRaisesRegex(ValueError, '历史版本'):
            self.store.load(r['id'])

    def test_http_gate_and_real_create_route(self):
        server = ReviewServer(workspace=self.fixture.workspace)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:%s' % server.server_port
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for headers in ({'Content-Type': 'application/json'}, {'Content-Type': 'application/json', 'X-Review-Token': server.token, 'Origin': 'https://other.test'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                op.open(urllib.request.Request(base+'/api/cycles/create', data=json.dumps(self.create).encode(), headers=headers))
            self.assertEqual(error.exception.code, 403)
        with op.open(urllib.request.Request(base+'/api/cycles/create', data=json.dumps(self.create).encode(), headers={'Content-Type': 'application/json', 'X-Review-Token': server.token})) as response:
            created = json.load(response)
        self.assertEqual(created['status'], 'planned')
        with op.open(urllib.request.Request(base+'/api/cycles/close', data=json.dumps({'id':created['id'],'revision':created['revision']}).encode(), headers={'Content-Type':'application/json','X-Review-Token':server.token})) as response:
            closed = json.load(response)
        self.assertEqual(closed['status'], 'closed')
        self.assertEqual(closed['items'], created['items'])
        self.assertTrue((self.store.root(created['id'])/'history'/created['history_file']).exists())
        with self.assertRaisesRegex(ValueError, '冻结'):
            self.store.implement(dict(id=closed['id'], revision=closed['revision'], sample_id='test-b'))
        fresh = self.store.create(self.create)
        self.assertNotEqual(fresh['id'],closed['id'])
        self.assertEqual(op.open(base+'/cycles').status, 200)
        with op.open(base+'/api/cycles/detail?id='+created['id']) as response:
            self.assertEqual(json.load(response)['id'], created['id'])


if __name__ == '__main__':
    unittest.main()
