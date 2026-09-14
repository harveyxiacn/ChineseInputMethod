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
