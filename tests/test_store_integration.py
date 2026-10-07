"""Checks independent of the delegated scaffold tests."""
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ime.settings import SettingsStore
from ime.lexicon import LexiconStore


def increment_commits(path, count):
    store = LexiconStore(path)
    for _ in range(count):
        store.learn('nihao', '你好', 'message')


class StoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_multiple_instances_merge_and_see_edits(self):
        a = SettingsStore(self.root / 'settings.json'); b = SettingsStore(a.path)
        a.update({'language': 'yue'}); b.update({'theme': 'dark'})
        self.assertEqual(a.get('theme'), 'dark'); self.assertEqual(b.get('language'), 'yue')
        x = LexiconStore(self.root / 'lexicon.json'); y = LexiconStore(x.path)
        x.upsert('甲'); y.upsert('乙')
        self.assertEqual([e['text'] for e in x.entries()], ['甲', '乙'])

    def test_multiprocess_updates_do_not_lose_commits(self):
        path = str(self.root / 'lexicon.json')
        program = 'from tests.test_store_integration import increment_commits; import sys; increment_commits(sys.argv[1], 12)'
        processes = [subprocess.Popen([sys.executable, '-c', program, path],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(LexiconStore(path).learned('nihao', 'message')[0]['count'], 36)

    def test_extended_settings_and_deep_copy(self):
        store = SettingsStore(self.root / 'settings.json')
        settings = store.update({'push_to_talk': True, 'backend': 'sensevoice', 'hotwords': ['双声'],
                                 'app_profiles': {'editor': {'script': 'traditional'}}, 'locale': 'en'})
        settings['hotwords'].append('bad'); store.get('app_profiles')['editor']['script'] = 'bad'
        self.assertEqual(store.get('hotwords'), ['双声'])
        self.assertEqual(store.get('app_profiles')['editor']['script'], 'traditional')
        self.assertEqual(store.get('model'), 'large-v3')
        self.assertEqual(store.get('device'), 'cpu')

    def test_invalid_unicode_and_shapes_are_rejected(self):
        store = SettingsStore(self.root / 'settings.json')
        for data in [{'language': {}}, {'candidate_count': True}, {'font_size': 10**400},
                     {'model': '\ud800'}, {'microphone': '\x00'}, {'raw_audio': 'a'},
                     {'draft': 'private'}, {'hotwords': ['\x00']}, {'app_profiles': {'a': {'app_profiles': {}}}}]:
            with self.assertRaises(ValueError): store.update(data)
        self.assertFalse(store.path.exists())
        lexicon = LexiconStore(self.root / 'lexicon.json')
        for kwargs in [{'shortcut': False}, {'pinyin': '\ud800'}, {'text': '\udfff'}, {'pinned': 1}]:
            with self.assertRaises(ValueError): lexicon.upsert(**({'text': '词'} | kwargs))
        for query in [True, [], '\x00', '\ud800']:
            with self.assertRaises(ValueError): lexicon.learn(query, '词')

    def test_malformed_persisted_rows_are_filtered(self):
        path = self.root / 'settings.json'
        path.write_text(json.dumps({'language': {}, 'theme': 'dark', 'candidate_count': True}))
        store = SettingsStore(path)
        self.assertEqual(store.get('theme'), 'dark'); self.assertEqual(store.get('language'), 'zh')
        path = self.root / 'lexicon.json'
        path.write_text(json.dumps({'entries': [{'id': 'evil', 'text': []}],
                                    'learn': [None, {'query': 'a', 'text': [], 'count': 1}]}))
        lexicon = LexiconStore(path)
        self.assertEqual(lexicon.entries(), []); self.assertEqual(lexicon.predict('a'), [])

    def test_failed_write_does_not_change_live_lexicon(self):
        store = LexiconStore(self.root / 'lexicon.json'); original = store.upsert('旧词')
        old_bytes = store.path.read_bytes()
        with patch('ime.settings.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): store.upsert('新词')
        self.assertEqual(store.entries(), [original]); self.assertEqual(store.path.read_bytes(), old_bytes)
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_id_updates_and_missing_ids(self):
        store = LexiconStore(self.root / 'lexicon.json'); entry = store.upsert('旧词')
        updated = store.upsert('新词', id=entry['id'])
        self.assertEqual(updated['id'], entry['id']); self.assertEqual(store.entries()[0]['text'], '新词')
        with self.assertRaises(ValueError): store.upsert('别的词', id='missing')
        self.assertFalse(store.delete('missing'))

    def test_local_llm_urls(self):
        store = SettingsStore(self.root / 'settings.json')
        for url in ['http://127.0.0.1:8080', 'http://localhost:9000', 'http://[::1]:1234/']:
            self.assertEqual(store.update({'llm_url': url})['llm_url'], url)
        for url in ['http://example.com:8080', 'https://localhost:8080', 'http://localhost',
                    'http://127.0.0.1:8080@evil.test:80', 'http://localhost:80/path']:
            with self.assertRaises(ValueError): store.update({'llm_url': url})


if __name__ == '__main__':
    unittest.main()
