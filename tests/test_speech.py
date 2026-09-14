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
        with patch.object(self.service, "_decode", return_value=[0.1]):
            self.assert_error(500, lambda: self.service.transcribe(b"audio", script="original"))
        self.assertFalse(self.service._lock.locked())

    def test_missing_conversion_is_not_silent(self):
        with patch("ime.speech.convert_text", side_effect=ConversionError("Install OpenCC")):
            self.assert_error(503, lambda: self.service.transcribe(b"audio"))
        self.model.transcribe.assert_not_called()

    def test_model_only_loads_from_cache(self):
        service = SpeechService()
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = MagicMock(return_value=self.model)
        with patch.dict(sys.modules, {"faster_whisper": module}):
            self.assertIs(service._load_model(), self.model)
            self.assertIs(service._load_model(), self.model)
        module.WhisperModel.assert_called_once()
        self.assertTrue(module.WhisperModel.call_args.kwargs["local_files_only"])

    def test_unavailable_model_is_actionable(self):
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = MagicMock(side_effect=FileNotFoundError())
        with patch.dict(sys.modules, {"faster_whisper": module}):
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
