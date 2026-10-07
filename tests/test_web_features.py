import base64
import http.client
import io
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ime.assistant import LocalAssistant, AssistantError
from ime.lexicon import LexiconStore
from ime.pinyin import PinyinEngine
from ime.server import Server
from ime.settings import SettingsStore
from ime.web_sessions import LiveSessions
from ime.speech import SpeechError


class LiveSessionTests(unittest.TestCase):
    def setUp(self):
        self.speech = Mock()
        self.speech.transcribe.return_value = {'text': '不改 API 3.12'}
        self.sessions = LiveSessions(self.speech)
        self.token = self.sessions.start('auto', 'simplified')['id']
        self.pcm = base64.b64encode(b'\x00\x10' * 64000).decode()

    def test_final_once_and_retry_does_not_decode_or_append(self):
        first = self.sessions.feed(self.token, 0, self.pcm)
        repeated = self.sessions.feed(self.token, 0, self.pcm)
        self.assertEqual(first, repeated)
        self.assertEqual(len(self.sessions.sessions[self.token]['pcm']), 128000)
        final = self.sessions.feed(self.token, 1, '', True)
        self.assertEqual(final['text'], '不改 API 3.12')
        self.assertEqual(self.speech.transcribe.call_count, 1)
        self.assertEqual(self.sessions.sessions[self.token]['pcm'], b'')
        self.assertEqual(self.sessions.feed(self.token, 1, '', True), final)
        with self.assertRaises(ValueError):
            self.sessions.feed(self.token, 2, self.pcm)

    def test_failure_keeps_preview_and_marks_partial(self):
        self.sessions.feed(self.token, 0, self.pcm)
        self.speech.transcribe.side_effect = RuntimeError('inference failed')
        result = self.sessions.feed(self.token, 1, base64.b64encode(b'\x00\x10' * 16000).decode(), True)
        self.assertEqual(result['text'], '不改 API 3.12')
        self.assertTrue(result['incomplete'])
        self.assertIn('inference failed', result['warning'])

    def test_cancel_during_inference_drops_result(self):
        def infer(*args, **kwargs):
            self.sessions.cancel(self.token)
            return {'text': 'must not insert'}
        self.speech.transcribe.side_effect = infer
        with self.assertRaises(SpeechError) as caught:
            self.sessions.feed(self.token, 0, self.pcm, True)
        self.assertEqual(caught.exception.status_code, 410)
        self.assertNotIn(self.token, self.sessions.sessions)

    def test_limits_and_sequence(self):
        for sequence, encoded, final in [(True, '', False), (-1, '', False), (1, '', False), (0, 'not-base64', False), (0, 'AA==', False), (0, '', 'false')]:
            with self.assertRaises(ValueError):
                self.sessions.feed(self.token, sequence, encoded, final)
        self.sessions.cancel(self.token)
        with self.assertRaises(SpeechError): self.sessions.feed(self.token, 0, self.pcm)

    def test_session_cap_and_expiry(self):
        sessions = LiveSessions(self.speech, limit=1, ttl=5)
        token = sessions.start('zh', 'simplified')['id']
        with self.assertRaises(SpeechError): sessions.start('zh', 'simplified')
        sessions.sessions[token]['used'] -= 6
        replacement = sessions.start('yue', 'traditional')['id']
        self.assertNotEqual(token, replacement)

    def test_repeated_sequence_rejects_changed_audio_or_final_flag(self):
        first = self.sessions.feed(self.token, 0, self.pcm)
        altered = base64.b64encode(b'\x01\x10' * 64000).decode()
        for data, final in [(altered, False), (self.pcm, True)]:
            with self.assertRaisesRegex(ValueError, 'same PCM and final flag'):
                self.sessions.feed(self.token, 0, data, final)
        self.assertEqual(self.sessions.feed(self.token, 0, self.pcm), first)
        self.assertEqual(self.speech.transcribe.call_count, 1)
        self.assertEqual(len(self.sessions.sessions[self.token]['pcm']), 128000)
        self.assertFalse(self.sessions.sessions[self.token]['finished'])

    def test_cached_response_is_not_mutable_by_caller(self):
        response = self.sessions.feed(self.token, 0, self.pcm)
        response['text'] = 'corrupted'
        self.assertEqual(self.sessions.feed(self.token, 0, self.pcm)['text'], '不改 API 3.12')

    def test_completed_sessions_free_slots_and_keep_bounded_retry_cache(self):
        sessions = LiveSessions(self.speech, limit=1)
        # Windows can return equal monotonic values for several fast operations.
        with patch('ime.web_sessions.time.monotonic', return_value=100):
            for _ in range(10):
                token = sessions.start('en', 'original')['id']
                final = sessions.feed(token, 0, self.pcm, True)
                self.assertEqual(sessions.feed(token, 0, self.pcm, True), final)
                self.assertEqual(list(sessions.sessions), [token])

    def test_default_active_cap_is_four(self):
        for _ in range(3):
            self.sessions.start('zh', 'original')
        with self.assertRaises(SpeechError) as caught:
            self.sessions.start('zh', 'original')
        self.assertEqual(caught.exception.status_code, 429)

    def test_oversized_pcm_rejected_before_accepting_sequence(self):
        oversized = base64.b64encode(b'\x00\x10' * (115 * 16000 + 1)).decode()
        with self.assertRaises(SpeechError) as caught:
            self.sessions.feed(self.token, 0, oversized)
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(self.sessions.sessions[self.token]['sequence'], 0)
        self.assertEqual(self.sessions.sessions[self.token]['pcm'], b'')
        self.speech.transcribe.assert_not_called()

    def test_expiry_boundary_is_consistent_and_releases_idle_session(self):
        # Subtracting these float timestamps loses precision at the deadline.
        used = self.sessions.sessions[self.token]['used'] = 1000.1
        with patch('ime.web_sessions.time.monotonic', return_value=used + self.sessions.ttl):
            with self.assertRaises(SpeechError) as caught:
                self.sessions.feed(self.token, 0, self.pcm)
        self.assertEqual(caught.exception.status_code, 410)
        self.assertNotIn(self.token, self.sessions.sessions)

    def test_busy_session_is_not_expired_while_inference_owns_it(self):
        session = self.sessions.sessions[self.token]
        session['lock'].acquire()
        try:
            with patch('ime.web_sessions.time.monotonic', return_value=session['used'] + self.sessions.ttl + 1):
                with self.assertRaises(SpeechError) as caught:
                    self.sessions.feed(self.token, 0, self.pcm)
                self.assertEqual(caught.exception.status_code, 429)
                self.sessions.start('yue', 'original')
                self.assertIn(self.token, self.sessions.sessions)
        finally:
            session['lock'].release()

    def test_empty_final_has_warning_and_never_claims_complete_recognition(self):
        result = self.sessions.feed(self.token, 0, '', True)
        self.assertEqual(result['text'], '')
        self.assertTrue(result['incomplete'])
        self.assertIn('No speech recognized', result['warning'])
        self.speech.transcribe.assert_not_called()

    def test_invalid_start_cancel_and_hint_types(self):
        for language, script, words in [([], 'original', None), ('en', {}, None), ('en', 'original', True)]:
            with self.assertRaises((ValueError, SpeechError)):
                self.sessions.start(language, script, words)
        for token in [None, {}, '', 'a' * 100]:
            with self.assertRaises(ValueError):
                self.sessions.cancel(token)

    def test_cancel_before_publish_returns_no_successful_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []

        def infer(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {'text': 'must not insert'}

        def feed():
            try: results.append(self.sessions.feed(self.token, 0, self.pcm, True))
            except SpeechError as exc: errors.append(exc.status_code)

        self.speech.transcribe.side_effect = infer
        worker = threading.Thread(target=feed)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.sessions.cancel(self.token)
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(results, [])
        self.assertEqual(errors, [410])


class FeatureServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        base = Path(cls.directory.name)
        cls.server = Server(('127.0.0.1', 0), pinyin=PinyinEngine(lexicon=LexiconStore(base/'lexicon.json')),
                            speech=Mock(), settings=SettingsStore(base/'settings.json'))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.directory.cleanup()

    def request(self, method, path, body=None, content_type='application/json'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        conn.request(method, path, json.dumps(body) if body is not None else None, {'Content-Type':content_type})
        response = conn.getresponse(); data = json.loads(response.read()); conn.close()
        return response.status, data

    def setUp(self):
        self.original_speech = self.server.speech
        self.original_live = self.server.live
        self.server.speech = Mock()
        self.server.speech.status.return_value = {'backend': 'faster-whisper', 'ready': True}
        self.server.speech.transcribe.return_value = {'text': '不改 API 3.12'}
        self.server.live = LiveSessions(self.server.speech)

    def tearDown(self):
        self.server.speech = self.original_speech
        self.server.live = self.original_live

    def test_settings_roundtrip_and_validation(self):
        code, result = self.request('POST','/api/settings',{'language':'yue','compact':True,'hotwords':['双声']})
        self.assertEqual(code,200); self.assertEqual(result['language'],'yue')
        self.assertTrue(self.request('GET','/api/settings')[1]['compact'])
        for bad in [{'font_size':True},{'candidate_count':0},{'llm_url':'http://evil.test:80'},{'unknown':'x'}]:
            self.assertEqual(self.request('POST','/api/settings',bad)[0],400)
        self.assertEqual(self.request('POST','/api/settings',{'language':'zh'},'text/plain')[0],400)

    def test_personal_term_commit_and_prediction(self):
        code, term = self.request('POST','/api/lexicon',{'text':'双声测诗','pinyin':'shuangshengceshi','shortcut':'/ss','pinned':True})
        self.assertEqual(code,200)
        code, data = self.request('GET','/api/candidates?q=%2Fss')
        self.assertEqual(data['candidates'][0]['text'],'双声测诗')
        self.assertEqual(self.request('POST','/api/commit',{'query':'sscs','text':'双声测诗','context':'测试'})[0],200)
        from urllib.parse import urlencode
        code, predictions = self.request('GET','/api/predict?'+urlencode({'context':'测试'}))
        self.assertIn('双声测诗',[row['text'] for row in predictions['candidates']])
        self.assertTrue(self.request('POST','/api/lexicon',{'action':'delete','id':term['id']})[1]['deleted'])

    def test_mixed_input_and_disabled_assistant(self):
        for query in ['Python3.12','test@example.com','v1.2.3']:
            from urllib.parse import urlencode
            code,data=self.request('GET','/api/candidates?'+urlencode({'q':query}))
            self.assertEqual(code,200);self.assertIn(query,[row['text'] for row in data['candidates']])
        self.assertEqual(self.request('POST','/api/assist',{'text':'不要改 123','action':'polish'})[0],400)

    def test_injected_settings_and_lexicon_reach_default_service(self):
        with patch('ime.server.SpeechService') as factory:
            server = Server(('127.0.0.1', 0), pinyin=self.server.pinyin, settings=self.server.settings)
            server.server_close()
        factory.assert_called_once_with(settings=self.server.settings, lexicon=self.server.pinyin.lexicon)

    def test_live_api_default_hints_do_not_force_whisper_onto_sensevoice(self):
        import types
        from ime.speech import SpeechService
        settings = types.SimpleNamespace(snapshot=lambda: {'backend': 'sensevoice', 'sensevoice_model': '/local/sensevoice', 'device': 'cpu'})
        self.server.pinyin.lexicon.upsert('张小明', 'zhangxiaoming')
        service = SpeechService(settings, self.server.pinyin.lexicon)
        model = Mock()
        model.supported_languages = ['zh', 'en', 'yue']
        model.transcribe.return_value = (iter([types.SimpleNamespace(text='张小明说不要改成42')]), types.SimpleNamespace(language='zh'))
        service._model = model
        self.server.speech = service
        self.server.live = LiveSessions(service)
        with patch.object(service, '_decode', return_value=[.1]):
            code, start = self.request('POST', '/api/live/start', {'language': 'zh', 'script': 'original'})
            self.assertEqual(code, 200)
            code, result = self.request('POST', '/api/live/chunk', {'id': start['id'], 'sequence': 0,
                'pcm': base64.b64encode(b'\x00\x10' * 64000).decode(), 'final': True})
        self.assertEqual(code, 200)
        self.assertEqual(result['text'], '张小明说不要改成42')
        self.assertNotIn('incomplete', result)
        self.assertIsNone(model.transcribe.call_args.kwargs['hotwords'])
        self.assertIsNone(model.transcribe.call_args.kwargs['initial_prompt'])

    def test_api_does_not_log_query_or_document_context(self):
        from urllib.parse import urlencode
        errors = io.StringIO()
        with patch('sys.stderr', errors):
            code, _ = self.request('GET', '/api/candidates?' + urlencode({'q': 'private123', 'context': 'private-document-not-for-logs'}))
        self.assertEqual(code, 200)
        self.assertNotIn('private', errors.getvalue())
        self.assertNotIn('?', errors.getvalue())
        self.assertIn('/api/candidates', errors.getvalue())

    def test_backend_lifecycle_errors_keep_status_and_detail(self):
        self.server.speech.warmup.side_effect = SpeechError('device memory unavailable', 503)
        code, result = self.request('POST', '/api/speech/warmup', {})
        self.assertEqual(code, 503)
        self.assertEqual(result['detail'], 'device memory unavailable')
        self.server.speech.status.side_effect = SpeechError('worker busy', 429)
        self.assertEqual(self.request('GET', '/api/health')[0], 429)

    def test_candidate_types_and_extra_live_fields_are_rejected(self):
        for query in ['fuzzy=yes', 'fuzzy=', 'limit=0', 'limit=51', 'limit=%EF%BC%91', 'limit=-1', 'limit=9&limit=1']:
            self.assertEqual(self.request('GET', '/api/candidates?q=nihao&' + query)[0], 400)
        for path, body in [('/api/live/start', {'language': []}), ('/api/live/start', {'unexpected': 1}),
            ('/api/live/cancel', {}), ('/api/live/cancel', {'id': True}),
            ('/api/live/chunk', {'extra': 1}), ('/api/speech/unload', {'force': True})]:
            self.assertEqual(self.request('POST', path, body)[0], 400)
        self.server.speech.unload.assert_not_called()

    def test_fuzzy_configuration_refreshes_from_shared_settings(self):
        changed = self.request('POST', '/api/settings', {'fuzzy_pairs': [['n', 'l']]})
        self.assertEqual(changed[0], 200)
        self.assertEqual(self.server.pinyin.fuzzy_pairs, [['n', 'l']])

    def test_http_retry_cannot_change_audio_or_turn_preview_into_final(self):
        _, created = self.request('POST', '/api/live/start', {'language': 'en', 'script': 'original'})
        body = {'id': created['id'], 'sequence': 0, 'pcm': base64.b64encode(b'\x00\x10' * 64000).decode(), 'final': False}
        first_code, first = self.request('POST', '/api/live/chunk', body)
        self.assertEqual(first_code, 200)
        for changed in ({**body, 'final': True}, {**body, 'pcm': base64.b64encode(b'\x01\x10' * 64000).decode()}):
            self.assertEqual(self.request('POST', '/api/live/chunk', changed)[0], 400)
        repeated_code, repeated = self.request('POST', '/api/live/chunk', body)
        self.assertEqual(repeated_code, 200)
        self.assertEqual(repeated, first)
        self.assertEqual(self.server.speech.transcribe.call_count, 1)

    def test_multipart_accepts_english_and_original_script(self):
        from tests.test_server import multipart
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request('POST', '/api/transcribe', multipart(language='en', script='original'),
                           {'Content-Type': 'multipart/form-data; boundary=test-boundary'})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        connection.close()
        self.server.speech.transcribe.assert_called_once_with(b'audio', language='en', script='original')


class AssistantTests(unittest.TestCase):
    def config(self, **changes):
        settings = Mock();settings.snapshot.return_value = {'llm_enabled':True,'llm_url':'http://127.0.0.1:8080','llm_model':'local',**changes}
        return settings

    def test_disabled_or_remote_never_connects(self):
        with patch('ime.assistant.http.client.HTTPConnection') as connect:
            for settings in [self.config(llm_enabled=False),self.config(llm_url='http://example.com:8080'),self.config(llm_url='http://127.0.0.1:8080@evil.test')]:
                with self.assertRaises(AssistantError): LocalAssistant(settings).transform('不改 3.12')
            connect.assert_not_called()

    def test_preview_only_and_preserve_request_content(self):
        response=Mock(status=200);response.read.return_value=json.dumps({'choices':[{'message':{'content':'不要更改 API 3.12。'}}]}).encode()
        connection=Mock();connection.getresponse.return_value=response
        with patch('ime.assistant.http.client.HTTPConnection',return_value=connection):
            result=LocalAssistant(self.config()).transform('不要更改 API 3.12')
        self.assertEqual(result,'不要更改 API 3.12。')
        payload=json.loads(connection.request.call_args.args[2])
        self.assertEqual(payload['messages'][1]['content'],'不要更改 API 3.12')
        self.assertFalse(payload['stream']);connection.close.assert_called_once()
