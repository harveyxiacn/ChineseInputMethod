"""Lexicon store tests: persistence, validation, learning, prediction."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from ime.lexicon import LexiconStore


class BasicEntryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = LexiconStore(path=Path(self.directory.name) / 'lexicon.json')

    def tearDown(self):
        self.directory.cleanup()

    def test_upsert_creates_entry(self):
        entry = self.store.upsert('你好世界', 'ni hao shi jie')
        self.assertIn('id', entry)
        self.assertEqual(entry['text'], '你好世界')
        self.assertEqual(entry['pinyin'], 'ni hao shi jie')
        self.assertFalse(entry['pinned'])

    def test_upsert_updates_existing_text(self):
        e1 = self.store.upsert('你好', 'ni hao')
        e2 = self.store.upsert('你好', 'ni3 hao3', shortcut='nh')
        self.assertEqual(e1['id'], e2['id'])
        self.assertEqual(len(self.store.entries()), 1)
        self.assertEqual(e2['pinyin'], 'ni3 hao3')
        self.assertEqual(e2['shortcut'], 'nh')

    def test_delete_entry(self):
        entry = self.store.upsert('测试', 'ce shi')
        self.assertTrue(self.store.delete(entry['id']))
        self.assertEqual(len(self.store.entries()), 0)

    def test_delete_unknown_id_returns_false(self):
        self.assertFalse(self.store.delete('nonexistent-id'))

    def test_entries_returns_copies(self):
        self.store.upsert('测试', 'ce shi')
        entries = self.store.entries()
        entries[0]['text'] = 'modified'
        self.assertEqual(self.store.entries()[0]['text'], '测试')


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = LexiconStore(path=Path(self.directory.name) / 'lexicon.json')

    def tearDown(self):
        self.directory.cleanup()

    def test_text_validation(self):
        self.store.upsert('a' * 512)
        with self.assertRaises(ValueError):
            self.store.upsert('')
        with self.assertRaises(ValueError):
            self.store.upsert('a' * 513)
        # Control chars not allowed
        with self.assertRaises(ValueError):
            self.store.upsert('test\x00')

    def test_template_newline_tab_allowed(self):
        # Templates may contain newline and tab
        self.store.upsert('line1\nline2', 'template')
        self.store.upsert('col1\tcol2', 'template2')

    def test_pinyin_validation(self):
        self.store.upsert('test', 'a' * 256)
        with self.assertRaises(ValueError):
            self.store.upsert('test', 'a' * 257)

    def test_shortcut_validation(self):
        self.store.upsert('test', shortcut='abc123')
        self.store.upsert('test', shortcut='')
        with self.assertRaises(ValueError):
            self.store.upsert('test', shortcut='a b')
        with self.assertRaises(ValueError):
            self.store.upsert('test', shortcut='a\x00b')
        with self.assertRaises(ValueError):
            self.store.upsert('test', shortcut='a' * 65)

    def test_pinned_validation(self):
        self.store.upsert('test', pinned=True)
        self.store.upsert('test2', pinned=False)
        with self.assertRaises(ValueError):
            self.store.upsert('test3', pinned=1)


class LearnAndPredictTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = LexiconStore(path=Path(self.directory.name) / 'lexicon.json')

    def tearDown(self):
        self.directory.cleanup()

    def test_learn_records_choice(self):
        self.store.learn('xihuan', '喜欢', '我不')
        learned = self.store.learned('xihuan', '我不')
        self.assertEqual(len(learned), 1)
        self.assertEqual(learned[0]['text'], '喜欢')

    def test_learn_increments_count(self):
        self.store.learn('xihuan', '喜欢', '我不')
        self.store.learn('xihuan', '喜欢', '我不')
        learned = self.store.learned('xihuan', '我不')
        self.assertEqual(learned[0]['count'], 2)

    def test_exact_context_matching(self):
        # Different contexts should not interfere
        self.store.learn('qu', '去', '我想')
        self.store.learn('qu', '去', '我不想去')
        # Query with '我想' context
        results = self.store.learned('qu', '我想')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['context_count'], 1)
        # Query with '我不想去' context
        results = self.store.learned('qu', '我不想去')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['context_count'], 1)

    def test_predict_uses_learned_transitions(self):
        self.store.upsert('喜欢', 'xihuan')
        self.store.learn('xihuan', '喜欢', '我不')
        predictions = self.store.predict('我不')
        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0]['text'], '喜欢')

    def test_predict_no_substring_matching(self):
        # Context '我不想去' must not match record learned from '我想去'
        self.store.upsert('去', 'qu')
        self.store.learn('qu', '去', '我想去')
        predictions = self.store.predict('我不想去')
        self.assertEqual(len(predictions), 0)

    def test_predict_empty_context_fallback(self):
        # Pinned entries should appear as fallback with empty context
        self.store.upsert('常用词', 'chang yong ci', pinned=True)
        predictions = self.store.predict('')
        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0]['text'], '常用词')

    def test_predict_limit_validation(self):
        for i in range(10):
            self.store.upsert(f'词{i}', f'ci{i}', pinned=True)
        results = self.store.predict('', limit=3)
        self.assertEqual(len(results), 3)
        self.assertEqual(self.store.predict('', limit=0), [])
        with self.assertRaises(ValueError):
            self.store.predict('', limit=51)
        with self.assertRaises(ValueError):
            self.store.predict('', limit=True)

    def test_learned_ranking_by_count(self):
        self.store.upsert('喜欢', 'xihuan')
        self.store.upsert('想', 'xiang')
        self.store.learn('xihuan', '喜欢', '我不')
        self.store.learn('xihuan', '喜欢', '我不')
        self.store.learn('xiang', '想', '我不')
        learned = self.store.learned('xihuan', '我不')
        self.assertEqual(learned[0]['text'], '喜欢')
        self.assertEqual(learned[0]['count'], 2)


class PersistenceTests(unittest.TestCase):
    def test_entries_persist_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lexicon.json'
            store = LexiconStore(path=path)
            store.upsert('你好', 'ni hao')
            store2 = LexiconStore(path=path)
            self.assertEqual(len(store2.entries()), 1)
            self.assertEqual(store2.entries()[0]['text'], '你好')

    def test_learned_data_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lexicon.json'
            store = LexiconStore(path=path)
            store.upsert('喜欢', 'xihuan')
            store.learn('xihuan', '喜欢', '我不')
            store2 = LexiconStore(path=path)
            learned = store2.learned('xihuan', '我不')
            self.assertEqual(len(learned), 1)

    def test_malformed_json_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lexicon.json'
            path.write_text('not json', encoding='utf-8')
            store = LexiconStore(path=path)
            self.assertEqual(len(store.entries()), 0)

    def test_bounded_learn_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lexicon.json'
            store = LexiconStore(path=path)
            records = [{'query': f'ci{i}', 'text': f'词{i}', 'context': f'context{i}', 'count': 1}
                       for i in range(2000)]
            path.write_text(json.dumps({'entries': [], 'learn': records}), encoding='utf-8')
            store.learn('xin', '新', 'context-new')
            reloaded = LexiconStore(path=path)
            self.assertEqual(reloaded.learned('ci0', 'context0'), [])
            self.assertEqual(reloaded.learned('xin', 'context-new')[0]['text'], '新')
            self.assertEqual(len(json.loads(path.read_text())['learn']), 2000)



class EnvironmentOverrideTests(unittest.TestCase):
    def test_ime_lexicon_path_env(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'env_lexicon.json'
            os.environ['IME_LEXICON_PATH'] = str(path)
            try:
                store = LexiconStore()
                store.upsert('测试', 'ce shi')
                self.assertTrue(path.exists())
            finally:
                del os.environ['IME_LEXICON_PATH']


if __name__ == '__main__':
    unittest.main()
