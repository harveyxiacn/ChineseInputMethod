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
