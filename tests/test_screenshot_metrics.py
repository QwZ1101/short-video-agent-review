import base64
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

from screenshot_metrics import ScreenshotStore, FIELDS, number, validate_candidate
from review_server import ReviewServer

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ScreenshotStore(self.temp.name)
        self.record = self.store.upload({'image_base64': base64.b64encode(PNG).decode()})
        self.id = self.record['id']
        self.candidate = dict(metrics={key: None for key in FIELDS}, published_text=None, title_text=None, warnings=[])
        self.candidate['metrics'].update(plays='2791', likes='53', comments='10', shares='17', favorites='20', danmaku='0', completion_rate='2.39%', bounce_2s_rate='42.16%')
        self.form = dict(reviewed=True, title='测试作品', sample_id='', observed_at='2026-11-01T16:42:00+08:00', scope='unknown', metrics=copy.deepcopy(self.candidate['metrics']), note='')

    def test_numbers_keep_unknown_separate_and_convert_units(self):
        self.assertIsNone(number(None))
        self.assertEqual(number('0'), 0)
        self.assertEqual(number('1.2万'), 12000)
        self.assertEqual(number('2.39%', True), 2.39)
        for value, rate in [('101%', True), ('1.5', False), ('1%', False), ('-1', False), ('NaN', False), ('1万', True)]:
            with self.assertRaises(ValueError): number(value, rate)

    def test_candidate_requires_all_fields_and_never_fills_missing_zero(self):
        self.candidate['metrics']['likes'] = None
        self.assertIsNone(validate_candidate(self.candidate)['metrics']['likes'])
        del self.candidate['metrics']['shares']
        with self.assertRaises(ValueError): validate_candidate(self.candidate)

    def test_upload_rejects_invalid_images_and_paths(self):
        with self.assertRaises(ValueError): self.store.upload({'image_base64': base64.b64encode(b'<script>bad</script>').decode()})
        with self.assertRaises(ValueError): self.store.read('../secret')
        with self.assertRaises(ValueError): self.store.upload({'image_base64': '%%%%'})
        path, _ = self.store.image(self.id)
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError): self.store.confirm(self.id, self.form)

    def test_confirmation_is_explicit_immutable_and_retains_raw_values(self):
        self.form['metrics']['plays'] = '1.2万'
        self.form['metrics']['likes'] = None
        state = self.store.confirm(self.id, self.form)
        self.assertEqual(state['confirmation']['normalized']['plays'], 12000)
        self.assertIsNone(state['confirmation']['normalized']['likes'])
        self.assertEqual(state['confirmation']['approximate_fields'], ['plays'])
        self.assertEqual(state['confirmation']['metrics']['plays'], '1.2万')
        with self.assertRaises(ValueError): self.store.confirm(self.id, self.form)

    def test_confirmation_requires_time_title_scope_and_real_sample(self):
        for key, value in [('reviewed', False), ('title', ''), ('observed_at', '16:42'), ('sample_id', 'no-such-video'), ('scope', 'period')]:
            form = dict(self.form, **{key: value})
            with self.assertRaises(ValueError): self.store.confirm(self.id, form)

    def test_one_attempt_and_candidate_requires_human_confirmation(self):
        release = threading.Event()
        def model(_):
            release.wait(2)
            return {'choices': [{'message': {'content': json.dumps(self.candidate)}}]}
        with patch.dict('os.environ', DASHSCOPE_API_KEY='test', DASHSCOPE_BASE_URL='https://test.aliyuncs.com'), patch('screenshot_metrics.call_dashscope', side_effect=model) as call:
            self.store.start(self.id)
            self.store.start(self.id)
            with self.assertRaises(ValueError): self.store.confirm(self.id, self.form)
            release.set()
            for _ in range(100):
                if self.store.read(self.id)['status'] != 'recognizing': break
                time.sleep(.02)
            self.assertEqual(self.store.read(self.id)['status'], 'needs_confirmation')
            self.assertIsNone(self.store.read(self.id)['confirmation'])
            self.store.start(self.id)
            self.assertEqual(call.call_count, 1)

    def test_failed_model_does_not_retry_or_invent_values(self):
        with patch.dict('os.environ', DASHSCOPE_API_KEY='test', DASHSCOPE_BASE_URL='https://test.aliyuncs.com'), patch('screenshot_metrics.call_dashscope', side_effect=ValueError('network failure')) as call:
            self.store.start(self.id)
            for _ in range(100):
                if self.store.read(self.id)['status'] != 'recognizing': break
                time.sleep(.02)
            state = self.store.start(self.id)
            self.assertEqual(state['status'], 'failed')
            self.assertIsNone(state['candidate'])
            self.assertEqual(call.call_count, 1)

    def test_http_requires_token_and_rejects_foreign_origin(self):
        server = ReviewServer(workspace=Path(self.temp.name))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:%s' % server.server_port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        body = json.dumps({'id': self.id, 'confirmation': self.form}).encode()
        for headers in ({'Content-Type':'application/json'}, {'Content-Type':'application/json','X-Review-Token':server.token,'Origin':'https://other.test'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(urllib.request.Request(base+'/api/metrics/confirm',data=body,headers=headers))
            self.assertEqual(error.exception.code,403)
        self.assertEqual(opener.open(base+'/metrics').status,200)
        result=json.load(opener.open(urllib.request.Request(base+'/api/metrics/confirm',data=body,headers={'Content-Type':'application/json','X-Review-Token':server.token})))
        self.assertEqual(result['status'],'confirmed')
