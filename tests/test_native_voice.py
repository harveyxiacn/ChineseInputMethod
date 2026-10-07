import signal
import subprocess
import unittest
from unittest.mock import Mock

from ime.native_voice import stop_recorder


class NativeVoiceTests(unittest.TestCase):
    def test_stop_flushes_wav_with_interrupt(self):
        process = Mock()
        process.poll.return_value = None
        stop_recorder(process)
        process.send_signal.assert_called_once_with(signal.SIGINT)
        process.wait.assert_called_once_with(timeout=3)
        process.kill.assert_not_called()

    def test_unresponsive_recorder_is_killed_and_reaped(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('pw-record', 3), 0]
        stop_recorder(process)
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_exited_process_is_not_signaled(self):
        process = Mock()
        process.poll.return_value = 0
        stop_recorder(process)
        process.send_signal.assert_not_called()

    def test_native_launch_uses_reusable_client_and_keeps_output_framing(self):
        import io
        from unittest.mock import patch
        from ime.live_speech import RATE, wav_bytes
        from ime.native_voice import main
        audio = wav_bytes(b'\x01\x20' * RATE * 4)
        service = Mock()
        service.transcribe.return_value = {'text': 'John did not pay 42'}

        def recorder(path, stop, preview=None):
            path.write_bytes(audio)

        output = io.StringIO()
        with patch('sys.argv', ['native_voice', '--language', 'en', '--script', 'original', '--protocol']), \
             patch('ime.native_voice.signal.signal'), patch('ime.native_voice.record', side_effect=recorder), \
             patch('ime.speech_daemon.SpeechDaemonClient', return_value=service), patch('sys.stdout', output):
            self.assertEqual(main(), 0)
        self.assertEqual(output.getvalue(), 'SHUANGSHENG_PARTIAL\nJohn did not pay 42\nSHUANGSHENG_END\nSHUANGSHENG_OK\nJohn did not pay 42\nSHUANGSHENG_END\n')
        self.assertEqual(service.transcribe.call_args.args[1:], ('en', 'original'))

    def test_native_cancel_during_inference_delivers_no_transcript(self):
        import io
        from unittest.mock import patch
        from ime.live_speech import RATE, wav_bytes
        from ime.native_voice import main
        audio = wav_bytes(b'\x01\x20' * RATE * 4)
        handlers = {}

        def recorder(path, stop, preview=None):
            path.write_bytes(audio)

        def transcribe(*args, **kwargs):
            handlers[signal.SIGTERM]()
            return {'text': 'must not appear'}

        service = Mock()
        service.transcribe.side_effect = transcribe
        output = io.StringIO()
        with patch('sys.argv', ['native_voice', '--protocol']), \
             patch('ime.native_voice.signal.signal', side_effect=lambda kind, handler: handlers.update({kind: handler})), \
             patch('ime.native_voice.record', side_effect=recorder), \
             patch('ime.speech_daemon.SpeechDaemonClient', return_value=service), patch('sys.stdout', output):
            self.assertEqual(main(), 130)
        self.assertEqual(output.getvalue(), '')


class RecordingTests(unittest.TestCase):
    def run_recording(self, returncode=1, running=True, stderr=None, silent=False,
                      frames=8000, cancelled=False):
        from array import array
        from pathlib import Path
        import tempfile
        import threading
        import wave
        from unittest.mock import patch
        from ime.native_voice import Cancelled, record

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audio.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(array('h', [0 if silent else 1000] * frames).tobytes())
            process = Mock()
            process.poll.return_value = None if running else returncode
            process.returncode = returncode
            stop = Mock() if cancelled else threading.Event()
            if cancelled:
                stop.wait.side_effect = Cancelled()
            else:
                stop.set()

            def popen(command, **kwargs):
                kwargs['stderr'].write((str(path) + '\n' + (stderr or '')).encode())
                return process

            with patch('ime.native_voice.shutil.which', return_value='/usr/bin/pw-record'), \
                 patch('ime.native_voice.subprocess.Popen', side_effect=popen) as launch:
                record(path, stop)
                return launch.call_args.args[0], process

    def test_valid_audio_after_requested_stop_accepts_pipewire_exit_one(self):
        _, process = self.run_recording()
        process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_successful_natural_completion(self):
        self.run_recording(returncode=0, running=False)

    def test_unrequested_exit_one_fails_even_with_valid_audio(self):
        with self.assertRaisesRegex(RuntimeError, 'Microphone recording failed'):
            self.run_recording(running=False)

    def test_real_error_is_reported_even_if_valid_audio_exists(self):
        with self.assertRaisesRegex(RuntimeError, 'Permission denied'):
            self.run_recording(stderr='remote error: Permission denied')

    def test_silence_is_rejected_before_transcription(self):
        with self.assertRaisesRegex(RuntimeError, 'captured silence'):
            self.run_recording(silent=True)

    def test_too_short_recording_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'too short'):
            self.run_recording(frames=10)

    def test_cancel_stops_recorder_and_does_not_transcribe(self):
        from ime.native_voice import Cancelled
        with self.assertRaises(Cancelled):
            self.run_recording(cancelled=True)

    def test_target_can_be_selected_without_changing_default_device(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'IME_RECORD_TARGET': 'test_virtual'}):
            command, _ = self.run_recording()
        self.assertEqual(command[1:3], ['--target', 'test_virtual'])

    def test_invalid_wav_is_rejected(self):
        from pathlib import Path
        import tempfile
        from ime.native_voice import validate_recording
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.wav'
            path.write_bytes(b'not a wave file')
            with self.assertRaisesRegex(RuntimeError, 'No valid microphone audio'):
                validate_recording(path)


class SnapshotTests(unittest.TestCase):
    def test_unfinalized_header_and_partial_sample(self):
        import io
        import struct
        import tempfile
        import wave
        from pathlib import Path
        from ime.native_voice import recording_snapshot
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b'\x01\x02' * 16000)
        data = bytearray(output.getvalue())
        struct.pack_into('<I', data, 4, 0)
        struct.pack_into('<I', data, 40, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recording.wav'
            self.assertIsNone(recording_snapshot(path))
            path.write_bytes(data + b'\x01')
            with wave.open(io.BytesIO(recording_snapshot(path)), 'rb') as wav:
                self.assertEqual(wav.getnframes(), 16000)
                self.assertEqual(wav.readframes(16000), b'\x01\x02' * 16000)
            path.write_bytes(data[:30])
            self.assertIsNone(recording_snapshot(path))
