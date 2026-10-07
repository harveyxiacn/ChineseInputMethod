import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from ime.evaluation import aggregate_scores, contains_term, edit_distance, score_transcript
from ime.live_speech import RATE, wav_bytes
from scripts.evaluate_input import evaluate_audio, evaluate_candidates, live_timings, main, read_manifest


class ScoringTests(unittest.TestCase):
    def test_edit_distance_concrete_insert_delete_replace(self):
        self.assertEqual(edit_distance('kitten', 'sitting'), 3)
        self.assertEqual(edit_distance('', '42'), 2)
        self.assertEqual(edit_distance('42', ''), 2)

    def test_cantonese_negation_is_an_error(self):
        score = score_transcript('我唔去', '我去', ['唔'])
        self.assertEqual(score['character_errors'], 1)
        self.assertEqual(score['cer'], 1 / 3)
        self.assertEqual(score['required_term_accuracy'], 0)

    def test_english_negation_and_number_changes_are_counted(self):
        score = score_transcript('John did not pay 42', 'John did pay 24', ['John', 'not', '42'])
        self.assertEqual(score['reference_english_tokens'], 5)
        self.assertEqual(score['english_token_errors'], 2)
        self.assertEqual(score['english_token_wer'], .4)
        self.assertEqual(score['required_term_accuracy'], 1 / 3)

    def test_signs_and_fullwidth_digits_are_preserved(self):
        self.assertEqual(score_transcript('-42', '42')['english_token_wer'], 1)
        self.assertEqual(score_transcript('123', '１２３')['cer'], 1)
        self.assertEqual(score_transcript('42.5', '42.5')['english_token_wer'], 0)

    def test_nfc_whitespace_punctuation_and_script_policy(self):
        self.assertEqual(score_transcript('café', 'cafe\u0301')['cer'], 0)
        self.assertEqual(score_transcript('你 好', '你好')['cer'], 0)
        self.assertEqual(score_transcript('你好。', '你好')['cer'], 1 / 3)
        self.assertEqual(score_transcript('廣東話', '广东话')['character_errors'], 3)
        self.assertEqual(score_transcript('John', 'john')['english_token_wer'], 1)

    def test_english_spacing_is_not_hidden_by_cer_whitespace_policy(self):
        score = score_transcript('Hello John', 'HelloJohn')
        self.assertEqual(score['cer'], 0)
        self.assertEqual(score['english_token_wer'], 1)

    def test_empty_reference_never_divides_by_zero(self):
        score = score_transcript('', 'unexpected 42')
        self.assertIsNone(score['cer'])
        self.assertIsNone(score['english_token_wer'])
        self.assertEqual(score['character_errors'], 12)
        self.assertEqual(score['english_token_errors'], 2)
        self.assertEqual(score_transcript('', '')['character_errors'], 0)

    def test_required_terms_use_ascii_word_boundaries(self):
        self.assertTrue(contains_term('Call John at 42.', 'John'))
        self.assertFalse(contains_term('Johnson paid 420', 'John'))
        self.assertFalse(contains_term('Johnson paid 420', '42'))
        self.assertFalse(contains_term('paid -42', '42'))
        self.assertFalse(contains_term('paid 42.5', '42'))
        self.assertTrue(contains_term('张小明说不要改成42', '42'))
        self.assertTrue(contains_term('联系John明天见', 'John'))
        self.assertTrue(contains_term('我唔去', '唔'))
        with self.assertRaisesRegex(ValueError, 'occur in its reference'):
            score_transcript('Hello John', 'John', ['42'])

    def test_aggregate_is_weighted_by_reference_length_and_retains_insertions(self):
        scores = [score_transcript('你好', '你好'), score_transcript('', '字')]
        result = aggregate_scores(scores)
        self.assertEqual(result['cer'], .5)
        self.assertEqual(result['character_errors'], 1)
        self.assertEqual(result['reference_characters'], 2)
        self.assertIsNone(aggregate_scores([])['cer'])


class HarnessTests(unittest.TestCase):
    def manifest(self, directory, cases):
        path = Path(directory) / 'cases.json'
        path.write_text(json.dumps({'schema_version': 1, 'cases': cases}), encoding='utf-8')
        return path

    def test_manifest_rejects_duplicates_and_invalid_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.manifest(directory, [{'id': 'a'}, {'id': 'a'}])
            with self.assertRaisesRegex(ValueError, 'unique'):
                read_manifest(path)
            path.write_text('{"schema_version": 2, "cases": []}')
            with self.assertRaisesRegex(ValueError, 'schema_version'):
                read_manifest(path)

    def test_candidate_metrics_report_rank_and_hide_text(self):
        engine = Mock()
        engine.info = {'backend': 'test'}
        engine.candidates.return_value = [{'text': 'first'}, {'text': 'private-target'}]
        with tempfile.TemporaryDirectory() as directory:
            path = self.manifest(directory, [{'id': 'case', 'query': 'private-query', 'expected': 'private-target'}])
            with patch('ime.pinyin.PinyinEngine', return_value=engine) as factory:
                report = evaluate_candidates(path, 9)
            self.assertNotIn('private', json.dumps(report))
            row = report['cases'][0]
            self.assertEqual(row['rank'], 2)
            self.assertFalse(row['first_candidate_correct'])
            self.assertTrue(row['top_k_correct'])
            self.assertEqual(report['summary']['top_k_accuracy'], 1)
            self.assertEqual(engine.candidates.call_count, 2)
            self.assertNotEqual(factory.call_args.kwargs['lexicon'].path, Path.home() / '.config/shuangsheng/lexicon.json')

    def test_audio_runs_cold_warm_and_scores_without_report_text(self):
        service = Mock()
        service.transcribe.return_value = {'text': 'John did not pay 42', 'language': 'en'}
        service.status.return_value = {'backend': 'faster-whisper', 'model': 'test', 'device': 'cpu'}
        with tempfile.TemporaryDirectory() as directory:
            audio = wav_bytes(b'\x01\x20' * RATE)
            Path(directory, 'private.wav').write_bytes(audio)
            path = self.manifest(directory, [{'id': 'case', 'path': 'private.wav',
                'reference': 'John did not pay 42', 'language': 'en', 'terms': ['not', '42']}])
            with patch('ime.speech.SpeechService', return_value=service):
                report = evaluate_audio(path)
            self.assertEqual(service.transcribe.call_count, 2)
            self.assertEqual(service.unload.call_count, 2)
            self.assertEqual(service.transcribe.call_args.args, (audio, 'en', 'original'))
            self.assertEqual(report['summary']['cer'], 0)
            self.assertEqual(report['summary']['required_term_accuracy'], 1)
            self.assertNotIn('John', json.dumps(report))
            self.assertNotIn('private.wav', json.dumps(report))

    def test_invalid_reference_terms_do_not_run_inference(self):
        service = Mock()
        with tempfile.TemporaryDirectory() as directory:
            path = self.manifest(directory, [{'id': 'a', 'path': 'missing.wav', 'reference': 'John', 'terms': ['42']}])
            with patch('ime.speech.SpeechService', return_value=service), self.assertRaisesRegex(ValueError, 'reference'):
                evaluate_audio(path)
        service.transcribe.assert_not_called()

    def test_live_replay_is_bounded_and_does_not_claim_capture_latency(self):
        service = Mock()
        service.transcribe.side_effect = [{'text': 'draft'}, {'text': 'final'}]
        result = live_timings(service, wav_bytes(b'\x01\x20' * RATE * 4), 'en', 'original', {})
        self.assertTrue(result['available'])
        self.assertIn('without realtime', result['method'])
        self.assertEqual(service.transcribe.call_count, 2)
        self.assertGreaterEqual(result['first_preview_compute_seconds'], 0)
        self.assertGreaterEqual(result['stop_to_final_compute_seconds'], 0)

    def test_cli_does_no_speech_work_without_explicit_audio_manifest(self):
        output = io.StringIO()
        with patch('scripts.evaluate_input.evaluate_candidates', return_value={'summary': {'cases': 8}}), \
             patch('scripts.evaluate_input.evaluate_audio') as speech, patch('sys.stdout', output):
            self.assertEqual(main([]), 0)
        speech.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['speech']['available'])

    def test_cli_suppresses_private_python_backend_diagnostics(self):
        def recognize(*args, **kwargs):
            import logging
            import sys
            print('private reference')
            print('private hypothesis', file=sys.stderr)
            logging.error('private transcript')
            return {'summary': {'cases': 1}}

        output, errors = io.StringIO(), io.StringIO()
        with patch('scripts.evaluate_input.evaluate_candidates', return_value={}), \
             patch('scripts.evaluate_input.evaluate_audio', side_effect=recognize), \
             patch('sys.stdout', output), patch('sys.stderr', errors):
            self.assertEqual(main(['--audio', 'explicit.json']), 0)
        self.assertNotIn('private', output.getvalue())
        self.assertEqual(errors.getvalue(), '')
        self.assertEqual(json.loads(output.getvalue())['speech']['summary']['cases'], 1)
