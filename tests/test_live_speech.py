import io
import unittest
import wave
from unittest.mock import Mock

from ime.live_speech import LiveTranscript, RATE, wav_bytes


def audio(seconds, sample=b'\x00\x10'):
    return wav_bytes(sample * int(seconds * RATE))


class LiveSpeechTests(unittest.TestCase):
    def session(self, texts):
        service = Mock()
        service.transcribe.side_effect = [
            item if isinstance(item, Exception) else {"text": item} for item in texts]
        updates = []
        live = LiveTranscript(service, 'yue', 'traditional', updates.append)
        return live, service, updates

    def durations(self, service):
        durations = []
        for call in service.transcribe.call_args_list:
            with wave.open(io.BytesIO(call.args[0]), 'rb') as wav:
                durations.append(wav.getnframes() / RATE)
            self.assertEqual(call.args[1:], ('yue', 'traditional'))
            self.assertEqual(call.kwargs, {'fast': True})
        return durations

    def test_long_audio_is_bounded_and_stop_only_decodes_tail(self):
        live, service, _ = self.session(['第一段', '第二段', '第三段', '尾句'])
        self.assertEqual(live.update(audio(36)), '第一段第二段第三段')
        self.assertEqual(live.update(audio(38), final=True), '第一段第二段第三段尾句')
        self.assertEqual(self.durations(service), [12, 12, 12, 2])

    def test_stop_does_not_repeat_identical_preview(self):
        live, service, _ = self.session(['正在說話'])
        live.update(audio(4))
        self.assertEqual(live.update(audio(4), final=True), '正在說話')
        self.assertEqual(service.transcribe.call_count, 1)

    def test_final_error_keeps_preview_and_does_not_advance_offset(self):
        live, service, updates = self.session(['前文', '尾句預覽', RuntimeError('inference failed')])
        live.update(audio(12))
        live.update(audio(16))
        with self.assertRaisesRegex(RuntimeError, 'inference failed'):
            live.update(audio(17), final=True)
        self.assertEqual(live.text, '前文尾句預覽')
        self.assertEqual(live.offset, 12 * RATE)
        self.assertEqual(updates[-1], '前文尾句預覽')

    def test_retry_failed_window_has_no_duplicate_prefix(self):
        live, _, _ = self.session(['前文', RuntimeError('busy'), '後文'])
        live.update(audio(12))
        with self.assertRaises(RuntimeError):
            live.update(audio(24))
        self.assertEqual(live.update(audio(24), final=True), '前文後文')

    def test_split_at_pause_and_keep_short_final_tail(self):
        live, service, _ = self.session(['一句', '尾'])
        pcm = b'\x00\x10' * (5 * RATE) + b'\x00\x00' * (RATE // 2) + b'\x00\x10' * RATE
        self.assertEqual(live.update(wav_bytes(pcm), final=True), '一句尾')
        durations = self.durations(service)
        self.assertAlmostEqual(durations[0], 5.3)
        self.assertAlmostEqual(sum(durations), 6.5)

    def test_empty_final_result_preserves_nonempty_draft(self):
        live, _, _ = self.session(['文字', ''])
        live.update(audio(3))
        self.assertEqual(live.update(audio(4), final=True), '文字')
