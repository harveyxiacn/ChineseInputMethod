import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from ime.conversion import ConversionError, convert_text
from ime.speech import MAX_AUDIO_BYTES, SpeechError, SpeechService


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.service = SpeechService()
        self.model = MagicMock()
        self.model.supported_languages = ["zh", "yue", "en"]
        self.model.transcribe.return_value = (
            iter([types.SimpleNamespace(text=" 廣東話 ")]),
            types.SimpleNamespace(language="yue"),
        )
        self.service._model = self.model

    def assert_error(self, expected, callback):
        with self.assertRaises(SpeechError) as caught:
            callback()
        self.assertEqual(caught.exception.status_code, expected)
        return str(caught.exception)

    def test_cantonese_forwarded_and_conversion_applied(self):
        with patch.object(self.service, "_decode", return_value=[0.1]), patch(
            "ime.speech.convert_text", side_effect=lambda text, script: text.replace("廣東話", "广东话")
        ) as convert:
            result = self.service.transcribe(b"audio", "yue", "simplified")
        self.assertEqual(result, {"text": "广东话", "language": "yue"})
        self.assertEqual(self.model.transcribe.call_args.kwargs["language"], "yue")
        self.assertEqual(convert.call_args.args, ("廣東話", "simplified"))

    def test_mandarin_and_auto_forwarded(self):
        for requested, forwarded in [("zh", "zh"), ("auto", None)]:
            with self.subTest(requested=requested), patch.object(self.service, "_decode", return_value=[0.1]):
                self.service.transcribe(b"audio", requested, "original")
                self.assertEqual(self.model.transcribe.call_args.kwargs["language"], forwarded)

    def test_fast_live_decode_does_not_change_default_upload_beam(self):
        with patch.object(self.service, "_decode", return_value=[0.1]):
            self.service.transcribe(b"audio", "yue", "original", fast=True)
            self.assertEqual(self.model.transcribe.call_args.kwargs["beam_size"], 1)
            self.service.transcribe(b"audio", "yue", "original")
            self.assertEqual(self.model.transcribe.call_args.kwargs["beam_size"], 5)

    def test_unsupported_cantonese_is_actionable(self):
        self.model.supported_languages = ["zh"]
        message = self.assert_error(422, lambda: self.service.transcribe(b"audio", "yue", "original"))
        self.assertIn("large-v3", message)
        self.model.transcribe.assert_not_called()

    def test_invalid_request_and_size(self):
        self.assert_error(400, lambda: self.service.transcribe(b""))
        self.assert_error(400, lambda: self.service.transcribe(b"audio", "xx"))
        self.assert_error(400, lambda: self.service.transcribe(b"audio", script="unknown"))
        self.assert_error(413, lambda: self.service.transcribe(b"a" * (MAX_AUDIO_BYTES + 1)))

    def test_busy_rejected(self):
        self.service._lock.acquire()
        try:
            self.assert_error(429, lambda: self.service.transcribe(b"audio"))
        finally:
            self.service._lock.release()

    def test_generator_failure_releases_lock(self):
        def broken():
            raise RuntimeError("out of memory")
            yield
        self.model.transcribe.return_value = (broken(), types.SimpleNamespace(language="zh"))
        with patch.object(self.service, "_decode", return_value=[0.1]), self.assertLogs(level="ERROR") as logs:
            self.assert_error(500, lambda: self.service.transcribe(b"audio", script="original"))
        self.assertIn("RuntimeError", "\n".join(logs.output))
        self.assertFalse(self.service._lock.locked())

    def test_missing_conversion_is_not_silent(self):
        with patch("ime.speech.convert_text", side_effect=ConversionError("Install OpenCC")):
            self.assert_error(503, lambda: self.service.transcribe(b"audio"))
        self.model.transcribe.assert_not_called()

    def test_model_only_loads_from_cache(self):
        service = SpeechService()
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = MagicMock(return_value=self.model)
        with patch.dict(sys.modules, {"faster_whisper": module}), patch.object(service, '_resolve_model_path', return_value='cached/local'):
            self.assertIs(service._load_model(), self.model)
            self.assertIs(service._load_model(), self.model)
        module.WhisperModel.assert_called_once()
        self.assertTrue(module.WhisperModel.call_args.kwargs["local_files_only"])

    def test_unavailable_model_is_actionable(self):
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = MagicMock(side_effect=FileNotFoundError())
        with patch.dict(sys.modules, {"faster_whisper": module}), patch('ime.speech.SpeechService._resolve_model_path', return_value='cached/local'):
            message = self.assert_error(503, lambda: SpeechService()._load_model())
        self.assertIn("Download", message)

    def test_bounded_decode_rejects_long_audio_before_full_decode(self):
        av = types.ModuleType("av")
        container = MagicMock()
        container.streams.audio = [object()]
        container.decode.return_value = iter([
            types.SimpleNamespace(samples=16000 * 121, sample_rate=16000)
        ])
        av.open = MagicMock()
        av.open.return_value.__enter__.return_value = container
        decoder = types.ModuleType("faster_whisper.audio")
        decoder.decode_audio = MagicMock()
        with patch.dict(sys.modules, {"av": av, "faster_whisper.audio": decoder}):
            self.assert_error(413, lambda: self.service._decode(b"compressed audio"))
        decoder.decode_audio.assert_not_called()

    def test_invalid_audio_is_reported(self):
        av = types.ModuleType("av")
        av.open = MagicMock(side_effect=ValueError("invalid data"))
        decoder = types.ModuleType("faster_whisper.audio")
        decoder.decode_audio = MagicMock()
        with patch.dict(sys.modules, {"av": av, "faster_whisper.audio": decoder}):
            self.assert_error(400, lambda: self.service._decode(b"garbage"))

    def test_english_names_numbers_and_negation_are_not_rewritten(self):
        self.model.transcribe.return_value = (iter([types.SimpleNamespace(text='John did not pay 42.5')]), types.SimpleNamespace(language='en'))
        with patch.object(self.service, '_decode', return_value=[.1]):
            self.assertEqual(self.service.transcribe(b'audio', 'en', 'original'),
                             {'text': 'John did not pay 42.5', 'language': 'en'})
        self.assertEqual(self.model.transcribe.call_args.kwargs['task'], 'transcribe')

    def test_terms_and_prompt_pass_without_rewrite(self):
        with patch.object(self.service, '_decode', return_value=[.1]):
            self.service.transcribe(b'audio', 'auto', 'original', hotwords=['張小明', 'OpenAI'], initial_prompt='Meet John at 42')
        options = self.model.transcribe.call_args.kwargs
        self.assertEqual(options['hotwords'], '張小明, OpenAI')
        self.assertEqual(options['initial_prompt'], 'Meet John at 42')
        self.assertFalse(options['condition_on_previous_text'])

    def test_hint_bounds_are_checked_before_model(self):
        for options in ({'hotwords': ['valid', 3]}, {'hotwords': ['x'] * 101},
                        {'hotwords': 'x' * 2001}, {'initial_prompt': True}):
            with self.subTest(options=options):
                self.assert_error(400, lambda: self.service.transcribe(b'audio', **options))
        self.model.transcribe.assert_not_called()

    def test_warmup_and_unload_reject_concurrent_inference(self):
        self.service._lock.acquire()
        try:
            self.assert_error(429, self.service.warmup)
            self.assert_error(429, self.service.unload)
            self.assertIs(self.service._model, self.model)
        finally:
            self.service._lock.release()

    def test_warmup_reuses_loaded_model_and_unload_releases_it(self):
        with patch('ime.speech.importlib.util.find_spec', return_value=object()):
            self.assertTrue(self.service.warmup()['loaded'])
            self.assertFalse(self.service.unload()['loaded'])
        self.assertIsNone(self.service._model)
        self.assertFalse(self.service._lock.locked())

    def test_status_distinguishes_dependencies_weights_and_loaded_device(self):
        service = SpeechService()
        with patch('ime.speech.importlib.util.find_spec', return_value=object()), patch.object(service, '_cached_model', return_value=False):
            state = service.status()
            self.assertTrue(state['installed'])
            self.assertFalse(state['cached'])
            self.assertFalse(state['ready'])
            self.assertFalse(state['loaded'])
            self.assertEqual(state['device'], service.device)
        with patch('ime.speech.importlib.util.find_spec', return_value=object()), patch.object(service, '_cached_model', return_value=True):
            self.assertTrue(service.status()['ready'])

    def test_cache_probe_requires_weights_and_config(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            self.service.model_name = directory
            self.assertFalse(self.service._cached_model())
            Path(directory, 'config.json').write_text('{}')
            self.assertFalse(self.service._cached_model())
            Path(directory, 'model.bin').write_bytes(b'weights')
            self.assertFalse(self.service._cached_model())
            Path(directory, 'tokenizer.json').write_text('{}')
            self.assertTrue(self.service._cached_model())

    def test_incomplete_local_model_never_reaches_tokenizer_download_fallback(self):
        import tempfile
        from pathlib import Path
        module = types.ModuleType('faster_whisper')
        module.WhisperModel = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'model.bin').write_bytes(b'weights')
            Path(directory, 'config.json').write_text('{}')
            self.service._model = None
            self.service.model_name = directory
            with patch.dict(sys.modules, {'faster_whisper': module}):
                message = self.assert_error(503, self.service._load_model)
            self.assertIn('tokenizer.json', message)
            module.WhisperModel.assert_not_called()

    def test_settings_and_explicit_lexicon_terms_are_shared_without_learning_text(self):
        config = {'model': 'large-v3', 'device': 'cpu', 'backend': 'whisper',
                  'hotwords': ['張小明'], 'initial_prompt': 'Meeting at 42'}
        settings = types.SimpleNamespace(snapshot=lambda: dict(config))
        lexicon = types.SimpleNamespace(entries=lambda: [{'text': 'OpenAI'}, {'text': '張小明'}, {'text': 123}])
        with patch.dict('os.environ', {}, clear=True):
            service = SpeechService(settings, lexicon)
            service._model = self.model
            with patch.object(service, '_decode', return_value=[.1]):
                service.transcribe(b'audio', 'en', 'original')
            self.assertEqual(self.model.transcribe.call_args.kwargs['hotwords'], '張小明, OpenAI')
            self.assertEqual(self.model.transcribe.call_args.kwargs['initial_prompt'], 'Meeting at 42')

    def test_settings_refresh_unloads_old_model_while_environment_takes_precedence(self):
        config = {'model': 'large-v3', 'device': 'cpu', 'backend': 'whisper'}
        with patch.dict('os.environ', {}, clear=True):
            service = SpeechService(types.SimpleNamespace(snapshot=lambda: dict(config)))
            service._model = self.model
            config['model'] = 'small'
            with patch('ime.speech.importlib.util.find_spec', return_value=object()):
                self.assertEqual(service.status()['model'], 'small')
                self.assertIsNone(service._model)
            with patch.dict('os.environ', {'IME_WHISPER_MODEL': 'large-v3', 'IME_WHISPER_DEVICE': 'cuda'}):
                service._refresh_configuration()
            self.assertEqual(service.model_name, 'large-v3')
            self.assertEqual(service.device, 'cuda')

    def test_sensevoice_hints_are_explicitly_rejected_before_decode(self):
        settings = types.SimpleNamespace(snapshot=lambda: {'backend': 'sensevoice', 'sensevoice_model': '/local/model'})
        with patch.dict('os.environ', {}, clear=True):
            service = SpeechService(settings)
            with patch.object(service, '_decode') as decode:
                self.assert_error(422, lambda: service.transcribe(b'audio', 'en', 'original', hotwords='John'))
            decode.assert_not_called()

    def test_ill_typed_language_script_and_fast_are_request_errors(self):
        for options in ({'language': []}, {'script': {}}, {'fast': 'true'}):
            with self.subTest(options=options):
                self.assert_error(400, lambda: self.service.transcribe(b'audio', **options))

    def test_backend_exception_text_is_not_logged(self):
        self.model.transcribe.side_effect = RuntimeError('private transcript John did not pay 42')
        with patch.object(self.service, '_decode', return_value=[.1]), self.assertLogs(level='ERROR') as logs:
            self.assert_error(500, lambda: self.service.transcribe(b'audio', 'en', 'original'))
        self.assertIn('RuntimeError', '\n'.join(logs.output))
        self.assertNotIn('private transcript', '\n'.join(logs.output))

    def test_unknown_backend_is_not_reported_ready(self):
        with patch.dict('os.environ', {'IME_SPEECH_BACKEND': 'unknown'}), patch('ime.speech.importlib.util.find_spec', return_value=object()):
            service = SpeechService()
            service._model = self.model
            state = service.status()
        self.assertFalse(state['ready'])
        self.assertIn('IME_SPEECH_BACKEND', state['error'])


class ConversionTests(unittest.TestCase):
    def test_original_needs_no_dependency(self):
        self.assertEqual(convert_text("廣東話", "original"), "廣東話")

    def test_conversion_selects_requested_script(self):
        with patch("ime.conversion._converter") as factory:
            factory.return_value.convert.return_value = "广东话"
            self.assertEqual(convert_text("廣東話", "simplified"), "广东话")
        factory.assert_called_once_with("simplified")


if __name__ == "__main__":
    unittest.main()
