"""Release integrity and native archive content contracts."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts import build_release


class ReleaseTests(unittest.TestCase):
    def test_notices_include_license_text_but_not_copying_test_binaries(self):
        for name in ['pkg.dist-info/licenses/LICENSE', 'COPYING.LESSER', 'COPYING.LIB', 'NOTICE.txt', 'libavcodec.COPYRIGHT']:
            self.assertTrue(build_release.is_notice_path(Path(name)), name)
        for name in ['PyObjCTest/_copying.cpython-312-darwin.so', 'tests/test_copying.py',
                     '_copying.so.dSYM/Contents/Resources/DWARF/_copying.so', 'random/data.txt']:
            self.assertFalse(build_release.is_notice_path(Path(name)), name)

    def test_incomplete_or_mixed_release_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for target in build_release.TARGETS[:-1]:
                (directory / build_release.archive_name('v0.3.0', target)).write_bytes(b'archive')
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                build_release.write_checksums(directory, 'v0.3.0')
            (directory / build_release.archive_name('v0.3.0', build_release.TARGETS[-1])).write_bytes(b'archive')
            checksums = build_release.write_checksums(directory, 'v0.3.0').read_text()
            self.assertEqual(len(checksums.splitlines()), 4)
            self.assertTrue(all(line.startswith(build_release.digest(directory / line.split()[1]))
                                for line in checksums.splitlines()))
            (directory / 'Shuangsheng-v0.2.0-windows-x64.zip').write_bytes(b'old')
            with self.assertRaisesRegex(ValueError, 'unexpected'):
                build_release.write_checksums(directory, 'v0.3.0')

    def test_rejects_empty_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for target in build_release.TARGETS:
                (directory / build_release.archive_name('v0.3.0', target)).touch()
            with self.assertRaisesRegex(ValueError, 'Empty'):
                build_release.write_checksums(directory, 'v0.3.0')

    def test_version_cannot_escape_output_directory(self):
        for version in ('../../other', 'v1', 'v1.2.3/other', '1.2.3\n', 'x;rm -rf'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                build_release.validate_version(version)
        self.assertEqual(build_release.validate_version('v0.3.0-rc.1'), 'v0.3.0-rc.1')

    def test_archive_preserves_nested_distribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'Shuangsheng-test'
            (package / 'Shuangsheng' / '_internal').mkdir(parents=True)
            (package / 'Shuangsheng' / 'Shuangsheng.exe').write_bytes(b'executable')
            (package / 'Shuangsheng' / '_internal' / 'dictionary.tsv').write_text('你好', encoding='utf-8')
            archive = root / 'test.zip'
            build_release.make_archive(package, archive, 'windows-x64')
            with zipfile.ZipFile(archive) as output:
                self.assertIn('Shuangsheng-test/Shuangsheng/_internal/dictionary.tsv', output.namelist())
            build_release.unpack_archive(archive, root / 'relocated', 'windows-x64')
            self.assertEqual((root / 'relocated' / package.name / 'Shuangsheng' / '_internal' / 'dictionary.tsv').read_text(encoding='utf-8'), '你好')


if __name__ == '__main__':
    unittest.main()
