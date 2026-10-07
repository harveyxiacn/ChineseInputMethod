import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ime.sensevoice import SenseVoiceAdapter


class SenseVoiceTests(unittest.TestCase):
    def test_missing_local_files_fail_before_optional_import(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'local config'):
                SenseVoiceAdapter(directory, 'cpu')

    def test_local_adapter_bypasses_hub_and_preserves_surface_text(self):
        config = {'model': 'SenseVoiceSmall', 'model_conf': {}, 'encoder': 'SenseVoiceEncoderSmall',
                  'tokenizer': 'SentencepiecesTokenizer', 'tokenizer_conf': {},
                  'frontend': 'WavFrontend', 'frontend_conf': {},
                  'remote_code': 'https://example.com/forbidden.py', 'vad_model': 'online-model'}
        model = Mock()
        model.kwargs = {'device': 'cpu'}
        model.generate.return_value = [{'text': '<|en|><|NEUTRAL|><|Speech|>John did not pay 42.5'}]
        factory = Mock(return_value=model)
        omega = SimpleNamespace(load=Mock(return_value=config), to_container=lambda value, **_: value)
        with tempfile.TemporaryDirectory() as directory:
            for name in ['config.yaml', 'model.pt', 'tokens.bpe.model']:
                Path(directory, name).write_bytes(b'local')
            with patch.dict('sys.modules', {'omegaconf': SimpleNamespace(OmegaConf=omega),
                                            'funasr': SimpleNamespace(AutoModel=factory)}):
                adapter = SenseVoiceAdapter(directory, 'cpu')
            options = factory.call_args.kwargs
            self.assertIn('model_conf', options)
            self.assertFalse(options['trust_remote_code'])
            self.assertTrue(options['disable_update'])
            self.assertNotIn('vad_model', options)
            self.assertNotIn('remote_code', options)
            self.assertEqual(options['init_param'], str(Path(directory, 'model.pt').resolve()))
            segments, info = adapter.transcribe([.1], language='en')
            self.assertEqual(next(segments).text, 'John did not pay 42.5')
            self.assertEqual(info.language, 'en')
            self.assertFalse(model.generate.call_args.kwargs['use_itn'])
            with self.assertRaisesRegex(ValueError, 'does not support hotwords'):
                adapter.transcribe([.1], hotwords='John')
