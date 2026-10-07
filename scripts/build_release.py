#!/usr/bin/env python3
"""Build native desktop archives and require a complete matrix before publishing."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
TARGETS = ("linux-x64", "windows-x64", "macos-arm64", "macos-x64")
SOURCE_DIRS = ("ime", "static", "native", "scripts", "packaging", "docs", "tests", "benchmarks", ".github")
SOURCE_FILES = ("README.md", "LICENSE", "CONTRIBUTING.md", "requirements.txt",
                "requirements-desktop.txt", "requirements-rime.txt", "requirements-sensevoice.txt")


def validate_version(value):
    if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?", value):
        raise ValueError("Version must be a semantic version, such as v0.3.0 or 0.3.0-rc.1")
    return value


def host_target():
    operating_system = {"linux": "linux", "win32": "windows", "darwin": "macos"}.get(sys.platform)
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    target = f"{operating_system}-{arch}"
    if target not in TARGETS:
        raise ValueError(f"Unsupported build host: {sys.platform}/{machine}")
    return target


def archive_name(version, target):
    extension = ".tar.gz" if target.startswith("linux-") else ".zip"
    return f"Shuangsheng-{validate_version(version)}-{target}{extension}"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_checksums(directory, version):
    """Do not publish a successful subset or archives from another version."""
    expected = {archive_name(version, target) for target in TARGETS}
    actual = {path.name for path in directory.iterdir() if path.name.endswith((".zip", ".tar.gz"))}
    if actual != expected:
        raise ValueError(f"Incomplete or mixed release: missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}")
    for name in expected:
        if not (directory / name).stat().st_size:
            raise ValueError(f"Empty release archive: {name}")
    output = directory / "SHA256SUMS"
    output.write_text("".join(f"{digest(directory / name)}  {name}\n" for name in sorted(expected)), encoding="utf-8")
    return output


def write_metadata(directory, version, target):
    """Record resolved versions and retain wheel-provided license/notice texts."""
    from scripts.runtime_provenance import collect_runtime_provenance

    directory.mkdir(parents=True)
    distributions = sorted(importlib.metadata.distributions(), key=lambda item: item.metadata['Name'].lower())
    installed = {}
    notices = directory / "dependency-licenses"
    python_notices = notices / "Python"
    python_notices.mkdir(parents=True)
    python_license = next((path for path in (
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
    ) if path.is_file()), None)
    if python_license is None:
        raise RuntimeError("Cannot find the Python runtime license for redistribution")
    shutil.copy2(python_license, python_notices / "LICENSE.txt")
    for distribution in distributions:
        name = distribution.metadata['Name']
        installed[name] = distribution.version
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        destination = notices / safe_name
        for relative in distribution.files or ():
            # Includes .dist-info/licenses and bundled FFmpeg/OpenSSL notices.
            if any(re.search(r"(^|[._-])(licenses?|copying|notice|copyright)([._-]|$)", part, re.I)
                   for part in relative.parts):
                original = Path(distribution.locate_file(relative))
                if original.is_file():
                    destination.mkdir(parents=True, exist_ok=True)
                    flattened = "__".join(part for part in relative.parts if part not in ("..", "."))
                    shutil.copy2(original, destination / flattened)
        destination.mkdir(parents=True, exist_ok=True)
        metadata = distribution.read_text("METADATA") or distribution.read_text("PKG-INFO")
        (destination / "METADATA.txt").write_text(metadata or f"Name: {name}\nVersion: {distribution.version}\n", encoding="utf-8")
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
    worktree = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True)
    manifest = {"version": version, "target": target, "python": sys.version,
                "commit": result.stdout.strip() if result.returncode == 0 else "unknown",
                "source_worktree_dirty": bool(worktree.stdout.strip()) if worktree.returncode == 0 else None,
                "dependencies": installed, "model_weights_included": False,
                "native_runtimes": collect_runtime_provenance(),
                "dependency_sources": "third-party-sources/source-manifest.json",
                "signing": "ad-hoc macOS signature; no Developer ID / Windows publisher certificate"}
    (directory / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def executable_in(package, target):
    if target.startswith("macos-"):
        return package / "Shuangsheng.app" / "Contents" / "MacOS" / "Shuangsheng"
    return package / "Shuangsheng" / ("Shuangsheng.exe" if target.startswith("windows-") else "Shuangsheng")


def smoke_test(package, target, working_directory, *, gui=False):
    executable = executable_in(package, target)
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    for variable in ('TCL_LIBRARY', 'TK_LIBRARY', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
        environment.pop(variable, None)
    report = working_directory / "smoke-report.json"
    subprocess.run([str(executable), "--smoke-test", str(report)], cwd=working_directory,
                   env=environment, check=True, timeout=120)
    if not report.is_file():
        raise RuntimeError("Frozen application exited without producing a smoke report")
    checks = json.loads(report.read_text(encoding="utf-8"))
    if checks.get("vad") != "passed" or checks.get("audio_samples") != 16000:
        raise RuntimeError(f"Frozen runtime smoke test failed: {checks}")
    # Verify command dispatch and bundled installer assets without changing a
    # runner's real input method profile.
    subprocess.run([str(executable), "--install-rime", "--user-dir", str(working_directory / "rime"), "--dry-run"],
                   cwd=working_directory, env=environment, check=True, timeout=30)
    if gui:
        subprocess.run([str(executable), "--gui-smoke-test"], cwd=working_directory,
                       env=environment, check=True, timeout=30)
    return checks


def make_archive(package, output, target):
    if target.startswith("macos-"):
        # ditto preserves app symlinks, executable modes and extended attributes.
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(package), str(output)], check=True)
    elif target.startswith("windows-"):
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for item in sorted(package.rglob("*")):
                if item.is_file():
                    archive.write(item, item.relative_to(package.parent))
    else:
        with tarfile.open(output, "w:gz", compresslevel=6) as archive:
            archive.add(package, arcname=package.name)


def unpack_archive(archive, directory, target):
    if target.startswith("macos-"):
        subprocess.run(["ditto", "-x", "-k", str(archive), str(directory)], check=True)
    elif target.startswith("windows-"):
        with zipfile.ZipFile(archive) as package:
            package.extractall(directory)
    else:
        # Only extract the archive just created by this process. data filter
        # preserves safe in-tree shared-library links, rejecting unsafe paths.
        with tarfile.open(archive) as package:
            package.extractall(directory, filter="data")


def build(version, target, *, gui_smoke=False):
    from scripts.runtime_provenance import collect_native_sources

    if host_target() != target:
        raise ValueError(f"Must build {target} on a matching host; detected {host_target()}")
    build_root = ROOT / ".cache" / "release" / target
    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)
    metadata = build_root / "metadata"
    write_metadata(metadata, version, target)
    environment = dict(os.environ, SHUANGSHENG_BUILD_METADATA=str(metadata), SHUANGSHENG_VERSION=version)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(build_root / "frozen"), "--workpath", str(build_root / "work"),
                    str(ROOT / "packaging" / "shuangsheng.spec")], cwd=ROOT, env=environment, check=True)
    package = build_root / f"Shuangsheng-{version}-{target}"
    package.mkdir()
    application = "Shuangsheng.app" if target.startswith("macos-") else "Shuangsheng"
    shutil.copytree(build_root / "frozen" / application, package / application, symlinks=True)
    shutil.copytree(metadata, package / "release-metadata")
    # Publish corresponding application/native/dictionary sources next to the
    # executable; omit caches, private audio and downloaded model weights.
    source = package / "source"
    source.mkdir()
    for name in SOURCE_DIRS:
        shutil.copytree(ROOT / name, source / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    for name in SOURCE_FILES:
        shutil.copy2(ROOT / name, source / name)
    for name in ("README.md", "LICENSE"):
        shutil.copy2(ROOT / name, package / name)
    shutil.copy2(ROOT / "packaging" / "QUICKSTART.md", package / "QUICKSTART.md")
    shutil.copy2(ROOT / "packaging" / "THIRD_PARTY.md", package / "THIRD_PARTY.md")
    collect_native_sources(package / "third-party-sources")
    destination = ROOT / "dist" / "release"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / archive_name(version, target)
    make_archive(package, archive, target)
    # Smoke test the extracted distributable outside the checkout, so missing
    # relative resources cannot accidentally be satisfied from source files.
    with tempfile.TemporaryDirectory(prefix="shuangsheng-release-") as temporary:
        relocated = Path(temporary)
        unpack_archive(archive, relocated, target)
        smoke_test(relocated / package.name, target, relocated, gui=gui_smoke)
    print(f"Built and verified {archive} ({digest(archive)})")
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", choices=TARGETS)
    parser.add_argument("--checksums-only", action="store_true")
    parser.add_argument("--gui-smoke-test", action="store_true", help="Also create and exercise the real Tk window; needs a display")
    args = parser.parse_args(argv)
    try:
        version = validate_version(args.version)
        if args.checksums_only:
            print(write_checksums(ROOT / "dist" / "release", version))
        else:
            build(version, args.target or host_target(), gui_smoke=args.gui_smoke_test)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
