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
        self.assertEqual(self.durations(service), [12, 13, 13, 3])

    def test_stop_does_not_repeat_identical_preview(self):
        live, service, _ = self.session(['正在說話'])
        live.update(audio(4))
        self.assertEqual(live.update(audio(4), final=True), '正在說話')
        self.assertEqual(service.transcribe.call_count, 1)
        self.assertEqual(live.snapshot['stable_text'], '正在說話')
        self.assertEqual(live.snapshot['revisable_tail'], '')
        self.assertEqual(live.offset, 4 * RATE)

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

    def test_forced_boundary_recovers_word_with_exact_context_dedup(self):
        live, service, _ = self.session(['你好張小明', '張小明說不要改成42'])
        live.update(audio(12))
        self.assertEqual(live.update(audio(14), final=True), '你好張小明說不要改成42')
        self.assertEqual(self.durations(service), [12, 3])

    def test_without_overlap_repeated_negation_is_not_removed(self):
        from ime.live_speech import join_text
        self.assertEqual(join_text('不要', '不要'), '不要不要')
        self.assertEqual(join_text('不', '不可以', overlap=True), '不不可以')
        self.assertEqual(join_text('do not', 'not now'), 'do not not now')

    def test_overlap_does_not_remove_partial_english_word(self):
        from ime.live_speech import join_text
        self.assertEqual(join_text('something', 'thing 42', overlap=True), 'something thing 42')
        self.assertEqual(join_text('call John', 'John at 42', overlap=True), 'call John at 42')

    def test_snapshot_marks_window_and_revisable_tail(self):
        live, _, _ = self.session(['前文', '預覽', '新的預覽'])
        live.update(audio(12))
        live.update(audio(15))
        self.assertEqual(live.snapshot['stable_text'], '前文')
        self.assertEqual(live.snapshot['revisable_tail'], '預覽')
        live.update(audio(16))
        self.assertEqual(live.snapshot['revisable_tail'], '新的預覽')

    def test_shrinking_snapshot_is_rejected_without_losing_text(self):
        live, service, _ = self.session(['文字'])
        live.update(audio(4))
        with self.assertRaisesRegex(ValueError, 'monotonically'):
            live.update(audio(3))
        self.assertEqual(live.text, '文字')
        self.assertEqual(service.transcribe.call_count, 1)

    def test_hints_forward_to_each_window(self):
        service = Mock()
        service.transcribe.return_value = {'text': '王小明42'}
        live = LiveTranscript(service, 'en', 'original', lambda _: None,
                              hotwords=['王小明'], initial_prompt='42')
        live.update(audio(3))
        self.assertEqual(service.transcribe.call_args.kwargs,
                         {'fast': True, 'hotwords': ['王小明'], 'initial_prompt': '42'})
