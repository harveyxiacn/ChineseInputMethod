"""Exercise a freshly built stable archive through the real staged updater.

By default the updater's GitHub transport uses local fixture streams.
Extraction, binary health checks, activation, startup acknowledgement and restore
use production code. User preferences and model caches remain outside this test.
The explicit --https-check flag also invokes the frozen application's real
GitHub update check; every HTTP, network or certificate failure fails this gate.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ime.updater import API_URL, REPOSITORY, TARGETS, UpdateError, Updater, archive_name, host_target, subprocess_environment, version_tuple


def archive_manifest(archive, target):
    """Read bounded metadata without extracting the trusted build artifact."""
    suffix = "/release-metadata/build-manifest.json"
    if target == "linux-x64":
        with tarfile.open(archive, "r:gz") as package:
            entries = [item for item in package if item.name.endswith(suffix)
                       and len(PurePosixPath(item.name).parts) == 3]
            if len(entries) != 1 or not entries[0].isfile() or not 0 < entries[0].size <= 1024 * 1024:
                raise ValueError("Archive must contain one bounded build manifest")
            name = entries[0].name
            with package.extractfile(entries[0]) as stream:
                data = stream.read(1024 * 1024 + 1)
    else:
        with zipfile.ZipFile(archive) as package:
            entries = [item for item in package.infolist() if item.filename.endswith(suffix)
                       and len(PurePosixPath(item.filename).parts) == 3]
            if len(entries) != 1 or entries[0].is_dir() or not 0 < entries[0].file_size <= 1024 * 1024:
                raise ValueError("Archive must contain one bounded build manifest")
            name = entries[0].filename
            with package.open(entries[0]) as stream:
                data = stream.read(1024 * 1024 + 1)
    parts = PurePosixPath(name).parts
    if len(parts) != 3 or name.startswith("/") or "\\" in name or any(part in {".", ".."} for part in parts):
        raise ValueError("Unexpected build manifest path")
    if len(data) > 1024 * 1024:
        raise ValueError("Build manifest exceeds its size limit")
    manifest = json.loads(data)
    if not isinstance(manifest, dict) or manifest.get("target") != target:
        raise ValueError("Build manifest target does not match the smoke target")
    version = manifest.get("version")
    version_tuple(version)
    expected_root = f"Shuangsheng-{version}-{target}"
    if parts[0] != expected_root or archive.name != archive_name(version, target):
        raise ValueError("Build manifest version does not match the archive name")
    return manifest


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_frozen_https(executable, directory, version):
    """Use the packaged updater's verified HTTPS transport, never our fixture.

    A report file supports windowed launchers without standard output. It is
    accepted only after successful process exit; stderr mentioning 404, an old
    success report or a malformed result cannot hide a transport failure.
    """
    report = directory / "frozen-https-check.json"
    report.unlink(missing_ok=True)
    result = subprocess.run([str(executable), "--update", "check", "--report", str(report)],
                            cwd=directory, env=subprocess_environment(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
    if result.returncode:
        detail = result.stdout.decode("utf-8", "replace")[-2000:]
        raise AssertionError(f"Frozen updater HTTPS check failed (exit {result.returncode}): {detail}")
    try:
        if not report.is_file() or not 0 < report.stat().st_size <= 1024 * 1024:
            raise ValueError("Missing or oversized report")
        checks = json.loads(report.read_text(encoding="utf-8"))
        if (not isinstance(checks, dict) or checks.get("url") != API_URL or
                version_tuple(checks.get("current")) != version_tuple(version) or
                "update" not in checks or
                (checks["update"] is not None and not isinstance(checks["update"], dict))):
            raise ValueError("Unexpected updater report")
    except (OSError, ValueError, UpdateError) as exc:
        raise AssertionError("Frozen updater exited without a valid HTTPS check report") from exc
    print(f"PASS: frozen {version} updater verified HTTPS check against {API_URL}")
    return checks


def verify_archive(archive, target, *, https_check=False):
    archive = Path(archive).resolve(strict=True)
    if target != host_target():
        raise ValueError(f"Run the OTA smoke on its matching platform: expected {target}, got {host_target()}")
    manifest = archive_manifest(archive, target)
    version = manifest["version"]
    if version_tuple(version) <= version_tuple("0.3.0"):
        raise ValueError("OTA smoke requires a stable archive newer than the 0.3.0 baseline")
    digest = digest_file(archive)
    size = archive.stat().st_size
    base = f"https://github.com/{REPOSITORY}/releases/download/{version}/"
    archive_url = base + archive.name
    checksum_url = base + "SHA256SUMS"
    payload = json.dumps({"tag_name": version, "draft": False, "prerelease": False,
        "assets": [{"name": archive.name, "size": size, "browser_download_url": archive_url},
                   {"name": "SHA256SUMS", "size": 256, "browser_download_url": checksum_url}]}).encode("utf-8")
    corrupt = True
    requested = []

    def fixture_transport(url):
        requested.append(url)
        if url == API_URL:
            return io.BytesIO(payload)
        if url == checksum_url:
            checksum = (("1" if digest[0] == "0" else "0") + digest[1:]) if corrupt else digest
            return io.BytesIO(f"{checksum}  {archive.name}\n".encode("ascii"))
        if url == archive_url:
            return archive.open("rb")
        raise AssertionError(f"Unexpected network request in OTA smoke: {url}")

    with tempfile.TemporaryDirectory(prefix="shuangsheng-ota-smoke-") as directory:
        temporary = Path(directory)
        settings = temporary / "config/settings.json"
        lexicon = temporary / "config/lexicon.json"
        settings.parent.mkdir()
        sentinels = {settings: b'{"theme":"dark","save_draft":false}\n',
                     lexicon: '{"entries":[],"learn":[],"sentinel":"保留原词库"}\n'.encode("utf-8")}
        for path, content in sentinels.items():
            path.write_bytes(content)
        environment = {"IME_SETTINGS_PATH": str(settings), "IME_LEXICON_PATH": str(lexicon),
            "SHUANGSHENG_UPDATE_ROOT": str(temporary / "updates"),
            "HF_HOME": str(temporary / "model-cache"), "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1", "PYTHONUTF8": "1"}
        with patch.dict(os.environ, environment), patch("ime.updater.open_url", side_effect=fixture_transport):
            updater = Updater(current_version="0.3.0", target=target, root=temporary / "updates")
            initial = updater.state()
            offer = updater.check()
            if not offer or offer["version"] != version:
                raise AssertionError("The freshly built archive was not offered as an update")
            try:
                updater.download(offer)
            except UpdateError as exc:
                if "SHA-256" not in str(exc):
                    raise AssertionError("Corrupt checksum failed for an unrelated reason") from exc
            else:
                raise AssertionError("Corrupt checksum was accepted")
            if updater.state() != initial or list((updater.root / "versions").iterdir()):
                raise AssertionError("Checksum rejection left an activated or staged version")
            corrupt = False
            progress = []
            record = updater.download(offer, progress=lambda done, total: progress.append((done, total)))
            if record["sha256"] != digest or not progress or progress[-1] != (size, size):
                raise AssertionError("Download checksum or progress does not match the archive")
            if any(total != size or done > size for done, total in progress):
                raise AssertionError("Download progress exceeded the archive size")
            if https_check:
                _, package = updater._package(record)
                verify_frozen_https(updater._verify_package(package, record), temporary, version)
            previous = updater.activate(record)
            if previous != initial or updater.state()["current"] != record:
                raise AssertionError("The prepared update was not activated")
            process = None
            try:
                report = temporary / "restarted-smoke.json"
                process = updater.launch(record, args=["--smoke-test", str(report)])
                if process.wait(timeout=180) != 0:
                    raise AssertionError("The activated application's smoke child failed")
                checks = json.loads(report.read_text(encoding="utf-8"))
                if checks.get("vad") != "passed" or checks.get("audio_samples") != 16000:
                    raise AssertionError("The restarted application failed its runtime checks")
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                updater.restore(previous)
            if updater.state() != initial or updater.state()["current"] is not None:
                raise AssertionError("Rollback did not restore the original activation state")
            for path, content in sentinels.items():
                if path.read_bytes() != content:
                    raise AssertionError(f"OTA smoke changed the preserved configuration: {path.name}")
            if set(requested) != {API_URL, checksum_url, archive_url}:
                raise AssertionError("The fixture did not exercise all expected GitHub requests")
    print(f"PASS: {target} {version}: checksum rejection, staged runtime, activation, acknowledged restart, rollback, configuration preserved")


class FrozenHTTPSCheckTests(unittest.TestCase):
    """The live gate must reject failures even when success-shaped data exists."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.executable = self.root / "packaged app 测试"
        self.report = self.root / "frozen-https-check.json"
        self.success = {"url": API_URL, "current": "0.4.1", "update": None}

    def tearDown(self):
        self.directory.cleanup()

    def run_check(self):
        return verify_frozen_https(self.executable, self.root, "v0.4.1")

    def child(self, report, code=0, output=b""):
        def run(command, **kwargs):
            self.assertEqual(command, [str(self.executable), "--update", "check", "--report", str(self.report)])
            self.assertEqual(kwargs["cwd"], self.root)
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
            if report is not None:
                self.report.write_text(json.dumps(report), encoding="utf-8")
            return subprocess.CompletedProcess(command, code, output)
        return run

    def test_success_uses_frozen_cli_and_file_without_stdout(self):
        with patch("subprocess.run", side_effect=self.child(self.success)):
            self.assertEqual(self.run_check(), self.success)

    def test_nonzero_rejects_even_new_success_report(self):
        with patch("subprocess.run", side_effect=self.child(self.success, 1, b"CERTIFICATE_VERIFY_FAILED")):
            with self.assertRaisesRegex(AssertionError, "HTTPS check failed.*CERTIFICATE_VERIFY_FAILED"):
                self.run_check()

    def test_http404_does_not_count_as_success(self):
        with patch("subprocess.run", side_effect=self.child(None, 1, b"HTTP Error 404: Not Found")):
            with self.assertRaisesRegex(AssertionError, "HTTPS check failed.*404"):
                self.run_check()

    def test_stale_report_is_removed_before_child(self):
        self.report.write_text(json.dumps(self.success), encoding="utf-8")
        def child(command, **kwargs):
            self.assertFalse(self.report.exists(), "Previous success report was not removed")
            return subprocess.CompletedProcess(command, 0, b"")
        with patch("subprocess.run", side_effect=child):
            with self.assertRaisesRegex(AssertionError, "valid HTTPS check report"):
                self.run_check()

    def test_malformed_report_rejected(self):
        malformed = [[], {}, {**self.success, "url": "http://api.github.com/"},
                     {**self.success, "current": "0.4.0"}, {**self.success, "update": False},
                     {"url": API_URL, "current": "0.4.1"}]
        for report in malformed:
            with self.subTest(report=report), patch("subprocess.run", side_effect=self.child(report)):
                with self.assertRaisesRegex(AssertionError, "valid HTTPS check report"):
                    self.run_check()

    def test_invalid_json_report_rejected(self):
        def child(command, **kwargs):
            self.report.write_text("{broken", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, b"")
        with patch("subprocess.run", side_effect=child):
            with self.assertRaisesRegex(AssertionError, "valid HTTPS check report"):
                self.run_check()

    def test_timeout_is_a_gate_failure(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("frozen updater", 90)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_check()

    def test_network_gate_is_explicit_opt_in(self):
        for flag, enabled in [([], False), (["--https-check"], True)]:
            with self.subTest(enabled=enabled), patch(__name__ + ".verify_archive") as verify:
                self.assertEqual(main(["artifact.tar.gz", "linux-x64", *flag]), 0)
                verify.assert_called_once_with(Path("artifact.tar.gz"), "linux-x64", https_check=enabled)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("target", choices=sorted(TARGETS))
    parser.add_argument("--https-check", action="store_true", help="Require the frozen updater's real verified GitHub HTTPS check (network required; every error fails)")
    args = parser.parse_args(argv)
    verify_archive(args.archive, args.target, https_check=args.https_check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
