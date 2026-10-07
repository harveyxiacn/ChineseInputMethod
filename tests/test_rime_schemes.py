"""Optional native scheme installation preserves existing frontend data."""
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import yaml
from scripts import install_rime, build_rime_schemes


class RimeSchemeTests(unittest.TestCase):
    def test_all_schemes_install_idempotently_with_licenses(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory)
            install_rime.install(user, schemes=['pinyin', 'shuangpin', 'jyutping'])
            patch_config = yaml.safe_load((user / 'default.custom.yaml').read_text(encoding='utf-8'))['patch']
            self.assertEqual(patch_config['schema_list/+'], [{'schema': 'shuangsheng'},
                             {'schema': 'shuangsheng_shuangpin'}, {'schema': 'shuangsheng_jyutping'}])
            dictionary = (user / 'shuangsheng_jyutping.dict.yaml').read_text(encoding='utf-8')
            self.assertIn('你\tnei5\t', dictionary)
            self.assertIn('好\thou2\t', dictionary)
            self.assertIn('香港\thoeng1 gong2\t', dictionary)
            self.assertIn('columns: [text, code, weight]', dictionary)
            self.assertIn('Creative Commons', (user / 'shuangsheng-LICENSE.cantonese').read_text(encoding='utf-8'))
            self.assertIn('CanCLID', (user / 'shuangsheng-NOTICE.cantonese').read_text(encoding='utf-8'))
            self.assertEqual(install_rime.install(user, schemes=['pinyin', 'shuangpin', 'jyutping'])['changed'], [])
            self.assertFalse(list(user.glob('*.before-shuangsheng-*')))
            # Shared Mandarin dictionary remains one installed file.
            self.assertFalse((user / 'shuangsheng_shuangpin.dict.yaml').exists())

    def test_appends_only_missing_schemas_preserving_existing_preferences(self):
        before = b'patch:\n  schema_list: [{schema: custom}, {schema: shuangsheng}]\n  schema_list/+: [{schema: another}]\n  menu/page_size: 5\n'
        after = yaml.safe_load(install_rime.merge_schema_config(before, ['shuangsheng', 'shuangsheng_jyutping']))['patch']
        self.assertEqual(after['schema_list'], [{'schema': 'custom'}, {'schema': 'shuangsheng'}])
        self.assertEqual(after['schema_list/+'], [{'schema': 'another'}, {'schema': 'shuangsheng_jyutping'}])
        self.assertEqual(after['menu/page_size'], 5)
        self.assertEqual(install_rime.merge_schema_config(before), before)

    def test_optional_files_are_backed_up(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory); schema = user / 'shuangsheng_shuangpin.schema.yaml'
            schema.write_bytes(b'custom old schema')
            result = install_rime.install(user, schemes=['shuangpin'])
            backup = next(Path(p) for p in result['backups'] if Path(p).name.startswith(schema.name))
            self.assertEqual(backup.read_bytes(), b'custom old schema')
            self.assertIn('双声小鹤双拼', schema.read_text(encoding='utf-8'))

    def test_invalid_schemes_and_dry_run_never_write(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory) / 'new'
            for schemes in [[], ['fake'], 'jyutping', [None]]:
                with self.assertRaises(ValueError): install_rime.install(user, schemes=schemes)
                self.assertFalse(user.exists())
            result = install_rime.install(user, schemes=['jyutping'], dry_run=True)
            self.assertIn('shuangsheng_jyutping.dict.yaml', result['changed'])
            self.assertFalse(user.exists())

    def test_missing_or_invalid_optional_data_prevents_partial_install(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory) / 'user'
            with patch.object(install_rime, 'jyutping_dictionary_bytes', side_effect=FileNotFoundError('missing')):
                with self.assertRaises(FileNotFoundError): install_rime.install(user, schemes=['jyutping'])
            self.assertFalse(user.exists())
            root = Path(directory) / 'root'; data = root / 'ime/data'; data.mkdir(parents=True)
            for row in ['字\tzi7\t1', '字\tzi6\tnan', '字\tzi6\t-1']:
                (data / 'jyutping.tsv').write_text(row, encoding='utf-8')
                with self.assertRaises(ValueError): install_rime.jyutping_dictionary_bytes(root)

    def test_optional_symlink_and_failure_preserve_original_config(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory); config = user / 'default.custom.yaml'; original = b'patch: {}\n'
            config.write_bytes(original)
            real_write = install_rime.atomic_write
            def fail(path, content):
                if path.name == 'shuangsheng_jyutping.dict.yaml': raise OSError('disk full')
                real_write(path, content)
            with patch.object(install_rime, 'atomic_write', side_effect=fail):
                with self.assertRaises(OSError): install_rime.install(user, schemes=['jyutping'])
            self.assertEqual(config.read_bytes(), original)
            self.assertFalse((user / 'shuangsheng_jyutping.schema.yaml').exists())
            target = user / 'preserved'; target.write_bytes(b'old')
            try: (user / 'shuangsheng_jyutping.schema.yaml').symlink_to(target)
            except OSError: self.skipTest('symlinks unavailable')
            with self.assertRaises(ValueError): install_rime.install(user, schemes=['jyutping'])
            self.assertEqual(target.read_bytes(), b'old')

    def test_xiaohe_algebra_matches_known_codes_without_double_remapping(self):
        schema = yaml.safe_load((install_rime.ROOT / 'native/rime/shuangsheng_shuangpin.schema.yaml').read_text(encoding='utf-8'))
        rules = schema['speller']['algebra']
        def encode(syllable):
            for rule in rules:
                operation, pattern, replacement, _ = rule.split('/')
                if operation != 'xform': continue
                replacement = re.sub(r'\$(\d+)', lambda m: '\\g<' + m[1] + '>', replacement)
                syllable = re.sub(pattern, replacement, syllable)
            return syllable
        self.assertEqual(encode('ni') + encode('hao'), 'nihc')
        self.assertEqual(encode('zhong') + encode('guo'), 'vsgo')
        for syllable, expected in [('a', 'aa'), ('ai', 'ai'), ('an', 'an'), ('ang', 'ah'), ('eng', 'eg'), ('er', 'er')]:
            self.assertEqual(encode(syllable), expected)
        self.assertEqual(schema['translator']['dictionary'], 'shuangsheng')
        self.assertEqual(schema['translator']['prism'], 'shuangsheng_shuangpin')

    def test_schema_generator_reproduces_bundled_bytes(self):
        generated = build_rime_schemes.schema_bytes()
        self.assertEqual(set(generated), {'shuangsheng_shuangpin.schema.yaml', 'shuangsheng_jyutping.schema.yaml'})
        for filename, content in generated.items():
            self.assertEqual(content, (install_rime.ROOT / 'native/rime' / filename).read_bytes())

    def test_jyutping_schema_retains_tones_and_toneless_forms(self):
        schema = yaml.safe_load((install_rime.ROOT / 'native/rime/shuangsheng_jyutping.schema.yaml').read_text(encoding='utf-8'))
        self.assertEqual(schema['translator']['dictionary'], 'shuangsheng_jyutping')
        self.assertTrue(set('123456') <= set(schema['speller']['alphabet']))
        self.assertIn('derive/[1-6]//', schema['speller']['algebra'])
        self.assertEqual(schema['schema']['schema_id'], 'shuangsheng_jyutping')
        for number in range(1, 10):
            self.assertIn({'when': 'has_menu', 'accept': f'Alt+{number}', 'send': f'Control+{number}'},
                          schema['key_binder']['bindings'])


if __name__ == '__main__':
    unittest.main()
