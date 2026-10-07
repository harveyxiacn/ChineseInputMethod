"""Settings persistence and validation tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ime.settings import SettingsStore


class DefaultsTests(unittest.TestCase):
    def test_default_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(path=Path(directory) / 'settings.json')
            self.assertEqual(store.get('language'), 'zh')
            self.assertEqual(store.get('script'), 'simplified')
            self.assertEqual(store.get('model'), 'large-v3')
            self.assertEqual(store.get('device'), 'cpu')
            self.assertEqual(store.get('microphone'), '')
            self.assertEqual(store.get('hotkey'), 'Ctrl+Alt+Space')
            self.assertFalse(store.get('compact'))
            self.assertEqual(store.get('theme'), 'system')
            self.assertEqual(store.get('font_size'), 16)
            self.assertEqual(store.get('candidate_count'), 9)
            self.assertEqual(store.get('input_scheme'), 'pinyin')
            self.assertFalse(store.get('fuzzy'))
            self.assertFalse(store.get('save_draft'))
            self.assertFalse(store.get('auto_insert'))

    def test_snapshot_is_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(path=Path(directory) / 'settings.json')
            snap = store.snapshot()
            snap['language'] = 'yue'
            self.assertEqual(store.get('language'), 'zh')

    def test_get_unknown_key(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(path=Path(directory) / 'settings.json')
            self.assertIsNone(store.get('nonexistent'))
            self.assertEqual(store.get('nonexistent', 'fallback'), 'fallback')


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SettingsStore(path=Path(self.directory.name) / 'settings.json')

    def tearDown(self):
        self.directory.cleanup()

    def test_valid_language_values(self):
        for lang in ['zh', 'yue', 'en', 'auto']:
            result = self.store.update({'language': lang})
            self.assertEqual(result['language'], lang)

    def test_invalid_language(self):
        with self.assertRaises(ValueError):
            self.store.update({'language': 'unsupported'})

    def test_valid_script_values(self):
        for script in ['simplified', 'traditional']:
            result = self.store.update({'script': script})
            self.assertEqual(result['script'], script)

    def test_invalid_script(self):
        with self.assertRaises(ValueError):
            self.store.update({'script': 'latin'})

    def test_valid_device_values(self):
        for device in ['auto', 'cpu', 'cuda']:
            result = self.store.update({'device': device})
            self.assertEqual(result['device'], device)

    def test_invalid_device(self):
        with self.assertRaises(ValueError):
            self.store.update({'device': 'gpu'})

    def test_valid_input_scheme_values(self):
        for scheme in ['pinyin', 'shuangpin', 'jyutping']:
            result = self.store.update({'input_scheme': scheme})
            self.assertEqual(result['input_scheme'], scheme)

    def test_invalid_input_scheme(self):
        with self.assertRaises(ValueError):
            self.store.update({'input_scheme': 'wubi'})

    def test_valid_theme_values(self):
        for theme in ['system', 'light', 'dark']:
            result = self.store.update({'theme': theme})
            self.assertEqual(result['theme'], theme)

    def test_invalid_theme(self):
        with self.assertRaises(ValueError):
            self.store.update({'theme': 'high-contrast'})

    def test_boolean_validation(self):
        result = self.store.update({'compact': True, 'fuzzy': False})
        self.assertTrue(result['compact'])
        self.assertFalse(result['fuzzy'])
        # Integers are not booleans
        with self.assertRaises(ValueError):
            self.store.update({'compact': 1})
        with self.assertRaises(ValueError):
            self.store.update({'fuzzy': 0})

    def test_font_size_validation(self):
        for size in [8, 16, 48]:
            result = self.store.update({'font_size': size})
            self.assertEqual(result['font_size'], size)
        for invalid in [7, 49, -1, 0]:
            with self.assertRaises(ValueError):
                self.store.update({'font_size': invalid})
        # Bool is not int
        with self.assertRaises(ValueError):
            self.store.update({'font_size': True})

    def test_candidate_count_validation(self):
        for count in [1, 9, 50]:
            result = self.store.update({'candidate_count': count})
            self.assertEqual(result['candidate_count'], count)
        for invalid in [0, 51, -1]:
            with self.assertRaises(ValueError):
                self.store.update({'candidate_count': invalid})
        with self.assertRaises(ValueError):
            self.store.update({'candidate_count': True})

    def test_model_string_validation(self):
        result = self.store.update({'model': 'small'})
        self.assertEqual(result['model'], 'small')
        result = self.store.update({'model': '/path/to/local/model'})
        self.assertEqual(result['model'], '/path/to/local/model')
        result = self.store.update({'model': 'a' * 4096})
        self.assertEqual(len(result['model']), 4096)
        with self.assertRaises(ValueError):
            self.store.update({'model': ''})
        with self.assertRaises(ValueError):
            self.store.update({'model': 'a' * 4097})

    def test_microphone_string_validation(self):
        result = self.store.update({'microphone': 'Built-in Mic'})
        self.assertEqual(result['microphone'], 'Built-in Mic')
        result = self.store.update({'microphone': 'a' * 256})
        self.assertEqual(len(result['microphone']), 256)
        with self.assertRaises(ValueError):
            self.store.update({'microphone': 'a' * 257})

    def test_hotkey_validation(self):
        result = self.store.update({'hotkey': 'Cmd+Space'})
        self.assertEqual(result['hotkey'], 'Cmd+Space')
        with self.assertRaises(ValueError):
            self.store.update({'hotkey': ''})

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({'unknown_key': 'value'})

    def test_update_rollback_on_error(self):
        self.store.update({'language': 'yue'})
        with self.assertRaises(ValueError):
            self.store.update({'language': 'bad', 'script': 'bad'})
        # Both should be unchanged
        self.assertEqual(self.store.get('language'), 'yue')
        self.assertEqual(self.store.get('script'), 'simplified')


class PersistenceTests(unittest.TestCase):
    def test_update_persists_and_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            store = SettingsStore(path=path)
            store.update({'language': 'yue', 'script': 'traditional'})
            # Reload from disk
            store2 = SettingsStore(path=path)
            self.assertEqual(store2.get('language'), 'yue')
            self.assertEqual(store2.get('script'), 'traditional')

    def test_malformed_json_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('not valid json', encoding='utf-8')
            store = SettingsStore(path=path)
            self.assertEqual(store.get('language'), 'zh')

    def test_non_object_json_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('[1, 2, 3]', encoding='utf-8')
            store = SettingsStore(path=path)
            self.assertEqual(store.get('language'), 'zh')

    def test_partial_valid_fields_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            data = {'language': 'yue', 'unknown': 'ignored', 'font_size': 20}
            path.write_text(json.dumps(data), encoding='utf-8')
            store = SettingsStore(path=path)
            self.assertEqual(store.get('language'), 'yue')
            self.assertEqual(store.get('font_size'), 20)
            self.assertEqual(store.get('script'), 'simplified')  # default

    def test_invalid_field_uses_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            data = {'language': 'invalid', 'script': 'simplified'}
            path.write_text(json.dumps(data), encoding='utf-8')
            store = SettingsStore(path=path)
            self.assertEqual(store.get('language'), 'zh')
            self.assertEqual(store.get('script'), 'simplified')

    def test_file_created_on_first_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sub' / 'settings.json'
            store = SettingsStore(path=path)
            self.assertFalse(path.exists())
            store.update({'language': 'yue'})
            self.assertTrue(path.exists())

    def test_atomic_write_preserves_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            store = SettingsStore(path=path)
            store.update({'language': 'yue'})
            original = path.read_bytes()
            with patch('ime.settings.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    store.update({'language': 'zh'})
            self.assertEqual(store.get('language'), 'yue')
            self.assertEqual(path.read_bytes(), original)


class EnvironmentOverrideTests(unittest.TestCase):
    def test_ime_settings_path_env(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'env_settings.json'
            os.environ['IME_SETTINGS_PATH'] = str(path)
            try:
                store = SettingsStore()
                store.update({'language': 'yue'})
                self.assertTrue(path.exists())
                self.assertEqual(store.get('language'), 'yue')
            finally:
                del os.environ['IME_SETTINGS_PATH']


if __name__ == '__main__':
    unittest.main()
