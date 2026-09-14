import tempfile
import unittest
from pathlib import Path

from ime.pinyin import PinyinEngine


class PinyinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = PinyinEngine()

    def texts(self, query, **kwargs):
        return [c['text'] for c in self.engine.candidates(query, **kwargs)]

    def test_exact_normalization(self):
        for query in ['nihao', 'Nǐ hǎo', "ni'hao", 'ni3hao3']:
            self.assertEqual(self.texts(query)[0], '你好')

    def test_explicit_boundaries(self):
        self.assertEqual(self.texts("xi'an")[0], '西安')
        self.assertNotIn('先', self.texts("xi'an"))

    def test_umlaut(self):
        for query in ['nǚ', 'nv', 'nu:']:
            self.assertEqual(self.texts(query)[0], '女')

    def test_prefix_and_initials(self):
        self.assertIn('你好', self.texts('nh'))
        self.assertIn('中国', self.texts('zhongg'))

    def test_composed_sentence(self):
        self.assertEqual(self.texts('woaizhongguo')[0], '我爱中国')

    def test_traditional(self):
        self.assertEqual(self.texts('zhongguo', script='traditional')[0], '中國')

    def test_invalid_and_limits(self):
        for query in ['', '你好', '<script>', 'a' * 129, 'zzzzzz']:
            self.assertEqual(self.texts(query), [])
        self.assertEqual(self.texts('nihao', limit=0), [])
        self.assertLessEqual(len(self.texts('shi', limit=2)), 2)
        with self.assertRaises(ValueError):
            self.texts('shi', script='other')

    def test_imported_dictionary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.tsv'
            path.write_text('程序\t程序\tcheng xu\t5000\n', encoding='utf-8')
            engine = PinyinEngine(path)
            self.assertEqual(engine.candidates('chengxu')[0]['text'], '程序')
            self.assertEqual(engine.candidates('nihao')[0]['text'], '你好')


if __name__ == '__main__':
    unittest.main()
