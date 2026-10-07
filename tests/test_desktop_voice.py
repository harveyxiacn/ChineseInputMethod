from array import array
import io
import os
import queue
import sys
import threading
import types
import unittest
from unittest.mock import MagicMock, patch
import wave

from ime.desktop_voice import (
    DictationJob, GlobalShortcut, RecordingCancelled, RecordingError,
    input_devices, pcm_to_wav, record_microphone,
)


class FakeBackend:
    class CallbackStop(Exception):
        pass

    def __init__(self, rate=16000, status=False):
        self.rate = rate
        self.status = status
        self.closed = False
        self.stream_options = None

    def query_devices(self, *args):
        if args:
            return {"default_samplerate": self.rate}
        return [{"name": "speaker", "max_input_channels": 0},
                {"name": "microphone", "max_input_channels": 1}]

    def RawInputStream(self, **options):
        self.stream_options = options
        backend = self

        class Stream:
            def __enter__(self):
                try:
                    # More samples than allowed exercise the duration bound.
                    for _ in range(100):
                        options["callback"](array("h", [500] * 1000).tobytes(),
                                            1000, None, backend.status)
                except backend.CallbackStop:
                    options["finished_callback"]()
                return self

            def __exit__(self, *args):
                backend.closed = True

        return Stream()


class RecordingTests(unittest.TestCase):
    def test_capture_is_bounded_and_preserves_device_sample_rate(self):
        backend = FakeBackend(rate=44100)
        started = MagicMock()
        wav = record_microphone(threading.Event(), threading.Event(), 7,
                                backend=backend, seconds=.25, on_started=started)
        with wave.open(io.BytesIO(wav), "rb") as audio:
            self.assertEqual(audio.getframerate(), 44100)
            self.assertEqual(audio.getnframes(), 11025)
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getsampwidth(), 2)
        self.assertEqual(backend.stream_options["device"], 7)
        self.assertTrue(backend.closed)
        started.assert_called_once()

    def test_unsupported_high_device_rate_is_bounded(self):
        backend = FakeBackend(rate=192000)
        record_microphone(threading.Event(), threading.Event(), backend=backend, seconds=.25)
        self.assertEqual(backend.stream_options["samplerate"], 48000)

    def test_input_overflow_reports_error_after_closing_device(self):
        backend = FakeBackend(status=True)
        with self.assertRaisesRegex(RecordingError, "丢帧"):
            record_microphone(threading.Event(), threading.Event(), backend=backend, seconds=.25)
        self.assertTrue(backend.closed)

    def test_cancel_before_opening_does_not_touch_device(self):
        cancel = threading.Event()
        cancel.set()
        backend = MagicMock()
        with self.assertRaises(RecordingCancelled):
            record_microphone(threading.Event(), cancel, backend=backend)
        backend.query_devices.assert_not_called()

    def test_devices_only_include_inputs(self):
        self.assertEqual(input_devices(FakeBackend()), [(1, "microphone")])

    def test_empty_short_and_silent_recordings_do_not_reach_model(self):
        for pcm in (b"", array("h", [100] * 100).tobytes(), b"\0" * 8000):
            with self.subTest(size=len(pcm)), self.assertRaises(RecordingError):
                pcm_to_wav(pcm, 16000)

    def test_permission_error_is_actionable(self):
        backend = MagicMock()
        backend.query_devices.side_effect = OSError("denied")
        with self.assertRaisesRegex(RecordingError, "权限"):
            record_microphone(threading.Event(), threading.Event(), backend=backend)


def event_list(job):
    result = []
    while True:
        try:
            result.append(job.events.get_nowait())
        except queue.Empty:
            return result


class DictationTests(unittest.TestCase):
    def fake_recorder(self, stop, cancel, device, *, on_started):
        on_started()
        return b"wav"

    def test_language_script_and_result_forwarded(self):
        service = MagicMock()
        service.transcribe.return_value = {"text": " 廣東話 "}
        job = DictationJob(service, self.fake_recorder)
        self.assertTrue(job.start("yue", "traditional", 2))
        job._thread.join(2)
        self.assertFalse(job.busy)
        service.transcribe.assert_called_once_with(b"wav", "yue", "traditional")
        self.assertEqual(event_list(job), [
            ("recording", None), ("transcribing", None), ("result", "廣東話"), ("idle", None),
        ])

    def test_cancel_inference_discards_result_and_prevents_overlap(self):
        entered, release = threading.Event(), threading.Event()

        def transcribe(*args):
            entered.set()
            release.wait(2)
            return {"text": "must not appear"}

        service = types.SimpleNamespace(transcribe=transcribe)
        job = DictationJob(service, self.fake_recorder)
        job.start()
        self.assertTrue(entered.wait(2))
        job.cancel()
        self.assertFalse(job.start())
        release.set()
        job._thread.join(2)
        events = event_list(job)
        self.assertNotIn("result", [kind for kind, _ in events])
        self.assertIn(("cancelled", None), events)
        self.assertEqual(events[-1], ("idle", None))

    def test_cancel_capture_does_not_transcribe(self):
        entered = threading.Event()

        def recorder(stop, cancel, device, *, on_started):
            entered.set()
            stop.wait(2)
            return b"wav"

        service = MagicMock()
        job = DictationJob(service, recorder)
        job.start()
        self.assertTrue(entered.wait(2))
        job.cancel()
        job._thread.join(2)
        service.transcribe.assert_not_called()
        self.assertEqual(event_list(job), [("cancelled", None), ("idle", None)])

    def test_speech_failure_restores_idle_and_preserves_actionable_error(self):
        service = MagicMock()
        service.transcribe.side_effect = RuntimeError("Download model first")
        job = DictationJob(service, self.fake_recorder)
        job.start()
        job._thread.join(2)
        self.assertIn(("error", "Download model first"), event_list(job))
        self.assertFalse(job.busy)

    def test_empty_transcript_is_not_delivered(self):
        service = MagicMock()
        service.transcribe.return_value = {"text": "  "}
        job = DictationJob(service, self.fake_recorder)
        job.start()
        job._thread.join(2)
        self.assertIn("error", [kind for kind, _ in event_list(job)])

    def test_wayland_does_not_claim_global_hotkey(self):
        shortcut = GlobalShortcut(lambda: None)
        with patch("sys.platform", "linux"), patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            self.assertIn("Wayland", shortcut.start())
        self.assertIsNone(shortcut.listener)

    def test_trusted_macos_starts_hotkey_even_when_class_default_is_false(self):
        keyboard = types.SimpleNamespace(
            Listener=types.SimpleNamespace(IS_TRUSTED=False), GlobalHotKeys=MagicMock(),
        )
        trust = MagicMock(return_value=True)
        callback = MagicMock()
        shortcut = GlobalShortcut(callback)
        with patch("sys.platform", "darwin"), patch.dict(sys.modules, {
            "HIServices": types.SimpleNamespace(AXIsProcessTrusted=trust),
            "pynput": types.SimpleNamespace(keyboard=keyboard),
        }):
            self.assertIn("Ctrl+Alt+Space", shortcut.start())
        trust.assert_called_once_with()
        keyboard.GlobalHotKeys.assert_called_once_with({"<ctrl>+<alt>+<space>": callback})
        keyboard.GlobalHotKeys.return_value.start.assert_called_once_with()
        self.assertIs(shortcut.listener, keyboard.GlobalHotKeys.return_value)
        shortcut.stop()

    def test_untrusted_macos_does_not_start_keyboard_monitor(self):
        keyboard = types.SimpleNamespace(GlobalHotKeys=MagicMock())
        trust = MagicMock(return_value=False)
        shortcut = GlobalShortcut(lambda: None)
        with patch("sys.platform", "darwin"), patch.dict(sys.modules, {
            "HIServices": types.SimpleNamespace(AXIsProcessTrusted=trust),
            "pynput": types.SimpleNamespace(keyboard=keyboard),
        }):
            self.assertIn("辅助功能", shortcut.start())
        trust.assert_called_once_with()
        keyboard.GlobalHotKeys.assert_not_called()
        self.assertIsNone(shortcut.listener)

    def test_macos_trust_query_failure_preserves_button_fallback(self):
        trust = MagicMock(side_effect=RuntimeError("framework unavailable"))
        shortcut = GlobalShortcut(lambda: None)
        with patch("sys.platform", "darwin"), patch.dict(sys.modules, {
            "HIServices": types.SimpleNamespace(AXIsProcessTrusted=trust),
        }):
            self.assertIn("录音按钮", shortcut.start())
        self.assertIsNone(shortcut.listener)

    def test_live_preview_level_and_final_preserve_names_and_numbers(self):
        from ime.live_speech import wav_bytes, RATE
        preview_audio = wav_bytes(b'\x01\x20' * RATE * 4)
        final_audio = wav_bytes(b'\x01\x20' * RATE * 5)

        def recorder(stop, cancel, device, *, on_started, on_preview, on_level):
            on_started()
            on_level(.25)
            on_preview(preview_audio)
            return final_audio

        service = MagicMock()
        service.transcribe.side_effect = [{'text': 'John'}, {'text': 'John did not pay 42'}]
        job = DictationJob(service, recorder)
        job.start('en', 'original', hotwords=['John'])
        job._thread.join(2)
        events = event_list(job)
        self.assertIn(('level', .25), events)
        self.assertIn(('partial', 'John'), events)
        self.assertIn(('result', 'John did not pay 42'), events)
        self.assertEqual(service.transcribe.call_count, 2)
        self.assertEqual(service.transcribe.call_args.kwargs, {'fast': True, 'hotwords': ['John']})

    def test_cancel_live_inference_emits_no_late_partial_or_result(self):
        from ime.live_speech import wav_bytes, RATE
        entered, release = threading.Event(), threading.Event()
        audio = wav_bytes(b'\x01\x20' * RATE * 4)

        def recorder(stop, cancel, device, *, on_started, on_preview, on_level):
            on_started()
            on_preview(audio)
            entered.wait(2)
            return audio

        def transcribe(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {'text': 'late result'}

        job = DictationJob(types.SimpleNamespace(transcribe=transcribe), recorder)
        job.start()
        self.assertTrue(entered.wait(2))
        job.cancel()
        release.set()
        job._thread.join(2)
        kinds = [kind for kind, _ in event_list(job)]
        self.assertNotIn('result', kinds)
        self.assertNotIn('partial', kinds)
        self.assertIn('cancelled', kinds)

    def test_hold_shortcut_starts_once_and_stops_on_chord_release(self):
        start, stop = MagicMock(), MagicMock()
        listener = MagicMock()
        listener.canonical.side_effect = lambda key: key
        keyboard = types.SimpleNamespace(Listener=MagicMock(return_value=listener),
                                         HotKey=types.SimpleNamespace(parse=lambda _: ['ctrl', 'alt', 'd']))
        shortcut = GlobalShortcut(start, hotkey='<ctrl>+<alt>+d', mode='hold', on_release=stop)
        with patch('sys.platform', 'win32'), patch.dict(sys.modules, {'pynput': types.SimpleNamespace(keyboard=keyboard)}):
            shortcut.start()
        handlers = keyboard.Listener.call_args.kwargs
        for key in ['ctrl', 'alt', 'd', 'd']:
            handlers['on_press'](key)
        start.assert_called_once()
        handlers['on_release']('unrelated')
        stop.assert_not_called()
        handlers['on_release']('d')
        handlers['on_release']('alt')
        stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
