"""Release source archives must match reviewed upstream checksums."""
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import runtime_provenance


class SourceArchiveTests(unittest.TestCase):
    def test_verified_cache_avoids_network(self):
        content = b"exact upstream source"
        record = {"name": "example", "url": "https://example.invalid/source.tar.gz",
                  "sha256": hashlib.sha256(content).hexdigest()}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "example-source.tar.gz"
            path.write_bytes(content)
            with patch.object(runtime_provenance.urllib.request, "urlopen") as request:
                self.assertEqual(runtime_provenance.download_verified(record, Path(temporary)), path)
            request.assert_not_called()

    def test_corrupt_cache_redownloads_and_checks_response(self):
        content = b"exact upstream source"
        record = {"name": "example", "url": "https://example.invalid/source.tar.gz",
                  "sha256": hashlib.sha256(content).hexdigest()}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "example-source.tar.gz"
            path.write_bytes(b"wrong cache")
            with patch.object(runtime_provenance.urllib.request, "urlopen", return_value=io.BytesIO(content)):
                runtime_provenance.download_verified(record, Path(temporary))
            self.assertEqual(path.read_bytes(), content)

    def test_bad_source_checksum_is_rejected(self):
        record = {"name": "example", "url": "https://example.invalid/source.tar.gz", "sha256": "0" * 64}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(runtime_provenance.urllib.request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(b"wrong")), \
                    patch.object(runtime_provenance.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    runtime_provenance.download_verified(record, Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_dav1d_official_download_variant_requires_exact_bytes(self):
        content = b'exact pinned archive'
        record = {'name': 'dav1d', 'url': runtime_provenance.DAV1D_ARCHIVE_URL,
                  'sha256': hashlib.sha256(content).hexdigest()}
        responses = [b'<!doctype html><title>Making sure you are not a bot!</title>',
                     b'wrong source archive', content]
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(runtime_provenance.urllib.request, 'urlopen',
                              side_effect=lambda *a, **k: io.BytesIO(responses.pop(0))) as request, \
                    patch.object(runtime_provenance.time, 'sleep'):
                path = runtime_provenance.download_verified(record, Path(temporary))
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(runtime_provenance.sha256(path), record['sha256'])
            self.assertEqual([call.args[0].full_url for call in request.call_args_list],
                             [record['url'], record['url'] + '?ref_type=tags', record['url'] + '?download=1'])
            self.assertEqual(list(Path(temporary).iterdir()), [path])

    def test_dav1d_html_on_all_variants_is_rejected_without_cache(self):
        record = {'name': 'dav1d', 'url': runtime_provenance.DAV1D_ARCHIVE_URL, 'sha256': '0' * 64}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(runtime_provenance.urllib.request, 'urlopen',
                              side_effect=lambda *a, **k: io.BytesIO(b'<html>not an archive</html>')) as request, \
                    patch.object(runtime_provenance.time, 'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'VideoLAN returned HTML'):
                    runtime_provenance.download_verified(record, Path(temporary))
            self.assertEqual(request.call_count, 3)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_unreviewed_pyav_version_fails_before_network_or_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "source"
            with patch.dict("sys.modules", {"av": SimpleNamespace(__version__="99.0", ffmpeg_version_info="99.0")}), \
                    patch.object(runtime_provenance.urllib.request, "urlopen") as request:
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    runtime_provenance.collect_native_sources(target)
            request.assert_not_called()
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
