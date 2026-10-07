"""Adversarial checks for offline decoding and explicit commit personalization."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ime.lexicon import LexiconStore
from ime.pinyin import PinyinEngine, normalize, shuangpin_code
from ime.settings import SettingsStore
from ime.native_pinyin import decode, bridge_path


class InputEngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.lexicon = LexiconStore(Path(self.directory.name) / 'lexicon.json')
        self.settings = SettingsStore(Path(self.directory.name) / 'settings.json')
        self.engine = PinyinEngine(lexicon=self.lexicon, fuzzy_pairs=self.settings.get('fuzzy_pairs'))

    def tearDown(self):
        self.directory.cleanup()

    def texts(self, query, **kwargs):
        return [c['text'] for c in self.engine.candidates(query, **kwargs)]

    def test_fallback_sentence_contains_real_words(self):
        with patch('ime.pinyin.decode', return_value=[]):
            self.assertEqual(self.texts('wojintianxiangquchaoshimaidongxi')[0], '我今天想去超市买东西')
            self.assertEqual(self.texts('wojintianquchaoshimaipingguo')[0], '我今天去超市买苹果')

    def test_literal_identifiers_preserve_every_character(self):
        for query in ['HTTPServer2', 'v1.2.3', 'alice+test@example.org',
                      'https://Example.org/a?q=2', '/tmp/My_File.py', r'C:\Users\A\x.txt',
                      'Python版本3', 'foo_bar', 'foo123', 'version3', 'shi123', '123.45']:
            self.assertEqual(self.texts(query)[0], query)

    def test_unknown_plain_pinyin_remains_invalid(self):
        for query in ['zzzzzz', '<script>', '你好', '１２３', 'x\x00z']:
            self.assertEqual(self.texts(query), [])

    def test_tones_still_rank_chinese_first(self):
        for query in ['Nǐ hǎo', 'ni3hao3', 'Ni3Hao3', "ni'hao"]:
            self.assertEqual(self.texts(query)[0], '你好')

    def test_english_completions_preserve_case(self):
        self.assertIn('hello', self.texts('hel'))
        self.assertIn('Hello', self.texts('Hel'))
        self.assertIn('HELLO', self.texts('HEL'))

    def test_only_explicit_commits_learn(self):
        self.engine.candidates('shi')
        self.assertFalse((Path(self.directory.name) / 'lexicon.json').exists())
        self.engine.learn('shi', '诗', '写一首')
        self.assertEqual(self.texts('shi', context='写一首')[0], '诗')
        reloaded = PinyinEngine(lexicon=LexiconStore(Path(self.directory.name) / 'lexicon.json'))
        self.assertEqual(reloaded.candidates('shi', context='写一首')[0]['text'], '诗')
        self.engine.learn('HTTPServer2', 'HTTPServer2')
        self.assertEqual(self.texts('HTTPServer3')[0], 'HTTPServer3')

    def test_exact_context_rank_and_negation(self):
        self.engine.learn('shi', '诗', '写一首')
        self.engine.learn('shi', '事', '还有什么')
        self.assertEqual(self.texts('shi', context='写一首')[0], '诗')
        self.assertEqual(self.texts('shi', context='还有什么')[0], '事')
        self.engine.learn('qu', '超市', '我想去')
        self.assertEqual(self.engine.predict('我想去')[0]['text'], '超市')
        self.assertNotIn('超市', [c['text'] for c in self.engine.predict('我不想去')])

    def test_user_terms_and_multiline_shortcut(self):
        self.lexicon.upsert('双声测试', 'shuang sheng ce shi', pinned=True)
        self.assertEqual(self.texts('shuangshengceshi')[0], '双声测试')
        self.lexicon.upsert('您好，\n谢谢来信。', shortcut='/reply')
        self.assertEqual(self.texts('/reply')[0], '您好，\n谢谢来信。')

    def test_user_terms_preserve_explicit_boundaries(self):
        self.lexicon.upsert('先', 'xian', pinned=True)
        self.assertNotIn('先', self.texts("xi'an"))
        self.assertEqual(self.texts("xi'an")[0], '西安')
        self.assertNotIn('你好', self.texts('n ihc', scheme='shuangpin'))

    def test_prediction_script(self):
        self.engine.learn('ruanjian', '软件', '开发')
        self.assertEqual(self.engine.predict('开发', script='traditional')[0]['text'], '軟件')

    def test_explicit_useful_shortcuts(self):
        self.assertRegex(self.texts('/date')[0], r'^\d{4}-\d{2}-\d{2}$')
        self.assertRegex(self.texts('/time')[0], r'^\d{2}:\d{2}$')
        self.assertEqual(self.texts('/sqm')[0], 'm²')
        self.assertEqual(self.texts('/celsius')[0], '℃')
        self.assertNotIn('℃', self.texts('celsius'))

    def test_fuzzy_is_opt_in_and_preserves_boundaries(self):
        self.assertNotIn('中国', self.texts('zongguo'))
        self.assertIn('中国', self.texts('zongguo', fuzzy=True))
        self.assertIn('上海', self.texts('sang hai', fuzzy=True))
        self.assertNotIn('先', self.texts("xi'an", fuzzy=True))

    def test_configured_fuzzy_pairs(self):
        self.engine.fuzzy_pairs = []
        self.assertNotIn('中国', self.texts('zongguo', fuzzy=True))
        self.engine.fuzzy_pairs = [['zh', 'z']]
        self.assertIn('中国', self.texts('zongguo', fuzzy=True))

    def test_xiaohe_double_pinyin_without_native_helper(self):
        with patch('ime.pinyin.decode', return_value=[]):
            self.assertEqual(self.texts('nihc', scheme='shuangpin')[0], '你好')
            self.assertEqual(self.texts('vsgo', scheme='shuangpin')[0], '中国')
        self.assertEqual(shuangpin_code('zhong'), 'vs')
        self.assertEqual(shuangpin_code('ang'), 'ah')

    def test_real_jyutping_and_tone_discrimination(self):
        self.assertEqual(self.texts('nei5hou2', scheme='jyutping')[0], '你好')
        self.assertEqual(self.texts('nei hou', scheme='jyutping')[0], '你好')
        self.assertIn('香港', self.texts('hoeng1gong2', scheme='jyutping', script='traditional'))
        self.assertNotIn('你好', self.texts('nei4hou2', scheme='jyutping'))
        self.assertEqual(self.texts('nei５hou２', scheme='jyutping'), [])
        self.assertEqual(self.texts('ne i5hou2', scheme='jyutping'), [])
        self.assertEqual(self.texts(' ', scheme='jyutping'), [])

    def test_jyutping_unavailable_has_clear_error(self):
        self.engine._jyutping = []
        with self.assertRaisesRegex(ValueError, 'Jyutping dictionary is unavailable'):
            self.engine.candidates('nei5hou2', scheme='jyutping')

    @unittest.skipUnless(bridge_path(), 'optional locally compiled libime bridge unavailable')
    def test_statistical_predictions_merge_without_duplicates(self):
        predictions = self.engine.predict('中国', limit=5)
        self.assertIn('人民', [c['text'] for c in predictions])
        self.assertEqual(len({c['text'] for c in predictions}), len(predictions))
        self.assertNotIn('中国', [c['text'] for c in predictions])
        self.engine.learn('renmin', '人民', '中国')
        merged = self.engine.predict('中国', limit=5)
        self.assertEqual(merged[0]['text'], '人民')
        self.assertEqual(sum(c['text'] == '人民' for c in merged), 1)
        self.engine.learn('chuantong', '传统', '中国')
        self.assertIn('傳統', [c['text'] for c in self.engine.predict('中国', limit=9, script='traditional')])

    def test_limits_and_types(self):
        self.assertEqual(self.engine.candidates('hello', limit=0), [])
        for limit in [True, 1.5, '3', 10**400]:
            if type(limit) is int:
                self.assertLessEqual(len(self.engine.candidates('shi', limit=limit)), 50)
            else:
                with self.assertRaises(ValueError):
                    self.engine.candidates('shi', limit=limit)
        with self.assertRaises(ValueError):
            self.engine.candidates('shi', fuzzy=1)
        with self.assertRaises(ValueError):
            self.engine.candidates('shi', scheme='fake')

    @unittest.skipUnless(bridge_path(), 'optional locally compiled libime bridge unavailable')
    def test_native_bridge_real_sentences_and_boundaries(self):
        self.assertEqual(decode('wojintianxiangquchaoshimaidongxi')[0]['text'], '我今天想去超市买东西')
        self.assertEqual(decode('nihc', scheme='shuangpin')[0]['text'], '你好')
        candidates = decode("xi'an")
        self.assertIn('西安', [c['text'] for c in candidates])
        self.assertNotIn('先', [c['text'] for c in candidates])

    def test_bridge_falls_back_on_failure(self):
        with patch('ime.native_pinyin.bridge_path', return_value=Path('/definitely/missing')):
            self.assertEqual(decode('nihao'), [])
        with patch('ime.pinyin.decode', return_value=[]):
            self.assertEqual(self.texts('nihao')[0], '你好')


if __name__ == '__main__':
    unittest.main()
