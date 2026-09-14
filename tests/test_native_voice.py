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
