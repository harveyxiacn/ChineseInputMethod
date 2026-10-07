"""Exercise a freshly built stable archive through the real staged updater.

Only the updater's GitHub transport is replaced with local fixture streams.
Extraction, binary health checks, activation, startup acknowledgement and restore
use production code. User preferences and model caches remain outside this test.
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
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ime.updater import API_URL, REPOSITORY, TARGETS, UpdateError, Updater, archive_name, host_target, version_tuple


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


def verify_archive(archive, target):
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("target", choices=sorted(TARGETS))
    args = parser.parse_args(argv)
    verify_archive(args.archive, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
