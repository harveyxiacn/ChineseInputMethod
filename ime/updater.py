"""Explicit GitHub release updates, staged beside the running application.

The trust anchor is this repository's HTTPS release, not an arbitrary feed URL.
SHA256SUMS detects damaged downloads; it is not a publisher signature.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid
import zipfile

from . import __version__
from .settings import file_lock

REPOSITORY = "harveyxiacn/ChineseInputMethod"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_ARCHIVE = 4 * 1024**3
MAX_EXPANDED = 12 * 1024**3
MAX_ENTRIES = 100000
VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z", re.ASCII)
TARGETS = {"linux-x64", "windows-x64", "macos-arm64", "macos-x64"}


class UpdateError(RuntimeError):
    pass


def version_tuple(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) and len(value) <= 64 else None
    if not match:
        raise UpdateError("Invalid stable release version.")
    return tuple(int(item) for item in match.groups())


def host_target():
    system = {"linux": "linux", "win32": "windows", "darwin": "macos"}.get(sys.platform)
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    target = f"{system}-{arch}"
    return target if target in TARGETS else None


def update_root():
    override = os.environ.get("SHUANGSHENG_UPDATE_ROOT")
    if override:
        return Path(override).expanduser().absolute()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "shuangsheng/updates"


def application_version():
    if getattr(sys, "frozen", False):
        manifest = Path(__file__).resolve().parents[1] / "release-metadata/build-manifest.json"
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))["version"]
            version_tuple(value)
            return value.removeprefix("v")
        except (OSError, ValueError, KeyError, UpdateError):
            pass
    return __version__


def archive_name(version, target):
    version_tuple(version)
    if target not in TARGETS:
        raise UpdateError("Unsupported update platform.")
    return f"Shuangsheng-{version}-{target}" + (".tar.gz" if target == "linux-x64" else ".zip")


def executable_in(package, target):
    if target.startswith("macos-"):
        return package / "Shuangsheng.app/Contents/MacOS/Shuangsheng"
    return package / "Shuangsheng" / ("Shuangsheng.exe" if target == "windows-x64" else "Shuangsheng")


def validate_url(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in {
                "api.github.com", "github.com", "release-assets.githubusercontent.com",
                "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
                or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
            raise ValueError()
    except (ValueError, TypeError):
        raise UpdateError("Update URL is outside the trusted HTTPS release hosts.") from None


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _tls_context():
    """Use the destination OS trust store, even when frozen on another distro."""
    context = ssl.create_default_context()
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        # An explicit trust-store override must not silently gain other roots.
        return context
    if sys.platform == "linux":
        # Bundled OpenSSL keeps its build host's default path (e.g. /usr/lib/ssl),
        # which need not exist on the installed machine (e.g. Arch /etc/ssl).
        for name in ("/etc/ssl/certs/ca-certificates.crt",
                     "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem"):
            if Path(name).is_file():
                context.load_verify_locations(cafile=name)
                break
    if not context.get_ca_certs():
        # macOS/Python distributions can also lack an OpenSSL default CA file.
        # PyInstaller's certifi hook bundles this public root certificate set.
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    return context


def open_url(url):
    validate_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Shuangsheng-Updater/0.4", "Accept": "application/json" if url == API_URL else "application/octet-stream"})
    https = urllib.request.HTTPSHandler(context=_tls_context())
    return urllib.request.build_opener(_Redirects(), https).open(request, timeout=45)


def _bounded_json(path):
    if path.stat().st_size > 1024 * 1024:
        raise UpdateError("Update metadata is too large.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise UpdateError("Invalid update metadata.")
    return value


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _member_path(name, expected_root):
    if (not isinstance(name, str) or len(name) > 1024 or "\\" in name or ":" in name
            or "\0" in name or name.startswith("/")):
        raise UpdateError("Unsafe archive path.")
    parts = PurePosixPath(name).parts
    if not parts or any(item in {"..", "."} for item in parts):
        raise UpdateError("Unsafe archive traversal.")
    if parts[0] not in {expected_root, "__MACOSX"}:
        raise UpdateError("Unexpected archive root.")
    return parts


def safe_extract(archive, destination, expected_root, target):
    """Validate all members before writing. Links are created after regular files."""
    destination = Path(destination)
    seen, records, expanded = set(), [], 0
    is_tar = target == "linux-x64"
    with tarfile.open(archive, "r:gz") if is_tar else zipfile.ZipFile(archive) as package:
        for member in package if is_tar else package.infolist():
            name = member.name if is_tar else member.filename
            parts = _member_path(name, expected_root)
            if parts[0] == "__MACOSX":
                continue  # ditto's optional resource-fork records aren't application files.
            key = "/".join(parts).casefold() if target != "linux-x64" else "/".join(parts)
            if key in seen or len(seen) >= MAX_ENTRIES:
                raise UpdateError("Duplicate or excessive archive entries.")
            seen.add(key)
            mode = member.mode if is_tar else member.external_attr >> 16
            if is_tar:
                kind = "directory" if member.isdir() else "file" if member.isfile() else "symlink" if member.issym() else "hardlink" if member.islnk() else "invalid"
                size = member.size
            else:
                kind = "directory" if member.is_dir() else "symlink" if stat.S_ISLNK(mode) else "file"
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                    kind = "invalid"
                size = member.file_size
            if kind == "invalid" or size < 0:
                raise UpdateError("Unsupported archive entry.")
            expanded += size
            if expanded > MAX_EXPANDED:
                raise UpdateError("Expanded update exceeds the size limit.")
            link = None
            if kind in {"symlink", "hardlink"}:
                if size > 4096:
                    raise UpdateError("Invalid archive link.")
                link = member.linkname if is_tar else package.read(member).decode("utf-8")
                if not link or "\\" in link or ":" in link or "\0" in link or link.startswith("/"):
                    raise UpdateError("Unsafe archive link.")
                resolved = posixpath.normpath(link if kind == "hardlink" else posixpath.join(posixpath.dirname(name), link))
                if not (resolved == expected_root or resolved.startswith(expected_root + "/")):
                    raise UpdateError("Archive link escapes its package.")
            records.append((member, parts, kind, mode, link))
        for member, parts, kind, mode, link in sorted(records, key=lambda item: item[2] in {"symlink", "hardlink"}):
            path = destination.joinpath(*parts)
            if not _inside(path.parent, destination):
                raise UpdateError("Archive parent escapes staging directory.")
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "directory":
                path.mkdir(exist_ok=True)
            elif kind == "file":
                source = package.extractfile(member) if is_tar else package.open(member)
                with source, path.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                if os.name != "nt":
                    path.chmod(0o755 if mode & 0o111 else 0o644)
            elif kind == "symlink":
                if target == "windows-x64":
                    raise UpdateError("Windows releases must not contain symlinks.")
                path.symlink_to(link)
            else:
                original = destination / link
                if not original.is_file() or original.is_symlink() or not _inside(original, destination):
                    raise UpdateError("Invalid archive hardlink target.")
                os.link(original, path)


def subprocess_environment():
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYINSTALLER_RESET_ENVIRONMENT="1")
    for name in ("TCL_LIBRARY", "TK_LIBRARY", "DYLD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH"):
        environment.pop(name, None)
    if environment.get("LD_LIBRARY_PATH_ORIG"):
        environment["LD_LIBRARY_PATH"] = environment["LD_LIBRARY_PATH_ORIG"]
    else:
        environment.pop("LD_LIBRARY_PATH", None)
    return environment


@contextmanager
def _system_dlls():
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            yield
        finally:
            ctypes.windll.kernel32.SetDllDirectoryW(str(sys._MEIPASS))
    else:
        yield


class Updater:
    def __init__(self, *, root=None, current_version=None, target=None):
        self.root = Path(root) if root is not None else update_root()
        self.current_version = current_version or application_version()
        self.target = target or host_target()

    def _prepare_root(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise UpdateError("The update root must be a real directory.")
        if os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise UpdateError("The update directory must be owned by this user and not writable by others.")

    def status(self):
        detail = "从 GitHub 检查正式版；下载校验后安装到独立目录，保留旧版。"
        if self.native_installed():
            detail += " 同时更新已安装的 Linux 原生插件，输入法将短暂重启。"
        return {"supported": self.target in TARGETS, "detail": detail if self.target else "当前平台暂无安装包。"}

    def native_installed(self):
        return self.target == "linux-x64" and (Path.home() / ".local/lib/fcitx5/shuangsheng.so").exists()

    def _bytes(self, url, limit):
        with open_url(url) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise UpdateError("Update response exceeds its size limit.")
        return data

    def check(self):
        if not self.status()["supported"]:
            raise UpdateError("Unsupported update platform.")
        try:
            release = json.loads(self._bytes(API_URL, 2 * 1024 * 1024))
            tag = release["tag_name"]
            if release.get("draft") or release.get("prerelease"):
                raise UpdateError("The latest release is not a stable public release.")
            if version_tuple(tag) <= version_tuple(self.current_version):
                return None
            name = archive_name(tag, self.target)
            assets = release["assets"]
            selected = [item for item in assets if item["name"] == name]
            checksums = [item for item in assets if item["name"] == "SHA256SUMS"]
            if len(selected) != 1 or len(checksums) != 1:
                raise UpdateError("The release is missing a unique platform archive or checksum file.")
            offer = {"version": tag, "target": self.target, "name": name,
                     "size": selected[0]["size"], "url": selected[0]["browser_download_url"],
                     "checksum_url": checksums[0]["browser_download_url"],
                     "page_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}"}
            self._validate_offer(offer)
            return offer
        except UpdateError:
            raise
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise UpdateError(f"无法检查 GitHub 更新：{exc}") from exc

    def _validate_offer(self, offer):
        name = archive_name(offer["version"], self.target)
        base = f"https://github.com/{REPOSITORY}/releases/download/{offer['version']}/"
        if (offer.get("target") != self.target or offer.get("name") != name
                or offer.get("url") != base + name or offer.get("checksum_url") != base + "SHA256SUMS"
                or type(offer.get("size")) is not int or not 0 < offer["size"] <= MAX_ARCHIVE):
            raise UpdateError("Invalid update offer or asset URL.")
        if version_tuple(offer["version"]) <= version_tuple(self.current_version):
            raise UpdateError("Refusing an update to the same or an older version.")

    def _package(self, record):
        version_tuple(record["version"])
        if record["target"] != self.target:
            raise UpdateError("Update platform mismatch.")
        directory = self.root / "versions" / f"{record['version']}-{self.target}"
        package = directory / f"Shuangsheng-{record['version']}-{self.target}"
        if not _inside(package, self.root / "versions"):
            raise UpdateError("Installed package escapes the update directory.")
        return directory, package

    def _verify_package(self, package, record):
        manifest = _bounded_json(package / "release-metadata/build-manifest.json")
        if manifest.get("version") != record["version"] or manifest.get("target") != record["target"]:
            raise UpdateError("The package manifest does not match the selected release.")
        executable = executable_in(package, self.target)
        if not executable.is_file() or not _inside(executable, package):
            raise UpdateError("The update executable is missing or escapes its package.")
        return executable

    def _smoke(self, package, record):
        executable = self._verify_package(package, record)
        with tempfile.TemporaryDirectory(prefix="shuangsheng-update-check-") as temporary:
            report = Path(temporary) / "smoke.json"
            environment = subprocess_environment()
            environment.pop("SHUANGSHENG_OTA_READY", None)
            environment["IME_SETTINGS_PATH"] = str(Path(temporary) / "settings.json")
            environment["IME_LEXICON_PATH"] = str(Path(temporary) / "lexicon.json")
            with _system_dlls():
                result = subprocess.run([str(executable), "--smoke-test", str(report)], cwd=temporary,
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
            if result.returncode or not report.is_file():
                raise UpdateError("新版运行检查失败，现有版本未变更。\n" + result.stdout.decode("utf-8", "replace")[-2000:])
            checks = _bounded_json(report)
            if checks.get("vad") != "passed" or checks.get("audio_samples") != 16000:
                raise UpdateError("The new version failed its runtime health check.")

    def download(self, offer, progress=None):
        try:
            self._validate_offer(offer)
            self._prepare_root()
            with file_lock(self.root / "update"):
                sums = self._bytes(offer["checksum_url"], 1024 * 1024).decode("utf-8")
                matches = [line.split() for line in sums.splitlines() if len(line.split()) == 2 and line.split()[1].lstrip("*") == offer["name"]]
                if len(matches) != 1 or not re.fullmatch(r"[0-9a-fA-F]{64}", matches[0][0]):
                    raise UpdateError("Missing or ambiguous SHA-256 for the selected archive.")
                digest = matches[0][0].lower()
                record = {"version": offer["version"], "target": self.target, "sha256": digest}
                directory, package = self._package(record)
                if directory.exists():
                    if _bounded_json(directory / "ready.json") != record:
                        raise UpdateError("An existing staged version has different metadata.")
                    self._smoke(package, record)
                    return record
                versions = self.root / "versions"
                versions.mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=".stage-", dir=versions) as temporary:
                    stage = Path(temporary)
                    archive = stage / "download"
                    count, hasher = 0, hashlib.sha256()
                    with open_url(offer["url"]) as response, archive.open("xb") as output:
                        while block := response.read(1024 * 1024):
                            count += len(block)
                            if count > offer["size"]:
                                raise UpdateError("Downloaded archive exceeds its declared size.")
                            hasher.update(block)
                            output.write(block)
                            if progress:
                                progress(count, offer["size"])
                    if count != offer["size"] or hasher.hexdigest() != digest:
                        raise UpdateError("下载大小或 SHA-256 校验失败，现有版本未变更。")
                    extracted = stage / "extracted"
                    extracted.mkdir()
                    safe_extract(archive, extracted, package.name, self.target)
                    self._smoke(extracted / package.name, record)
                    _atomic_json(extracted / "ready.json", record)
                    os.replace(extracted, directory)
                return record
        except UpdateError:
            raise
        except (OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
            raise UpdateError(f"更新准备失败，现有版本未变更：{exc}") from exc

    def _record(self, record):
        directory, package = self._package(record)
        ready = _bounded_json(directory / "ready.json")
        if ready != record:
            raise UpdateError("The prepared update record has changed.")
        self._verify_package(package, record)
        return package

    def state(self):
        path = self.root / "active.json"
        return _bounded_json(path) if path.exists() else {"current": None, "previous": None}

    def activate(self, record):
        self._record(record)
        with file_lock(self.root / "update"):
            old = self.state()
            if old.get("current") and version_tuple(record["version"]) < version_tuple(old["current"]["version"]):
                raise UpdateError("A newer update was already activated; use explicit rollback to downgrade.")
            if old.get("current") == record:
                return old
            _atomic_json(self.root / "active.json", {"current": record, "previous": old.get("current")})
            return old

    def restore(self, state):
        for key in ("current", "previous"):
            if state.get(key):
                self._record(state[key])
        with file_lock(self.root / "update"):
            _atomic_json(self.root / "active.json", state)

    def rollback(self):
        self._prepare_root()
        with file_lock(self.root / "installation"):
            return self._rollback()

    def _rollback(self):
        state = self.state()
        previous = state.get("previous")
        if not previous:
            raise UpdateError("没有已托管的上一版本；首次更新可从原安装目录启动旧版并使用 --no-update-redirect。")
        self._record(previous)
        native = None
        if self.native_installed() and (self.root / "native.json").exists():
            from .native_update import NativeUpgrade
            native = NativeUpgrade(self, previous)
            native.prepare()
        try:
            if native:
                native.activate()
            self.restore({"current": previous, "previous": state.get("current")})
        except Exception:
            if native:
                native.rollback()
            raise
        return previous

    def launch(self, record, args=()):
        package = self._record(record)
        executable = self._verify_package(package, record)
        token = uuid.uuid4().hex
        ready_dir = self.root / "launch"
        ready_dir.mkdir(exist_ok=True)
        ready = ready_dir / token
        environment = subprocess_environment()
        environment.update(SHUANGSHENG_OTA_READY=token, SHUANGSHENG_UPDATE_ROOT=str(self.root.resolve()))
        command = [str(executable), "--no-update-redirect", *args]
        if not args and self.native_installed():
            # The native addon owns the shared global dictation shortcut.
            command.append("--no-hotkey")
        log = self.root / "launch.log"
        with log.open("wb") as output, _system_dlls():
            process = subprocess.Popen(command, cwd=package, env=environment, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if ready.is_file():
                    return process
                code = process.poll()
                if code is not None:
                    break
                time.sleep(0.1)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            raise UpdateError("新版未能确认启动；已保留当前应用。详情：" + str(log))
        finally:
            ready.unlink(missing_ok=True)

    def install(self, record):
        self._prepare_root()
        with file_lock(self.root / "installation"):
            return self._install(record)

    def _install(self, record):
        self._record(record)
        native_transaction = None
        if self.native_installed():
            from .native_update import NativeUpgrade
            native_transaction = NativeUpgrade(self, record)
            native_transaction.prepare()
        old = self.activate(record)
        try:
            if native_transaction:
                native_transaction.activate()
            self.launch(record)
        except Exception:
            self.restore(old)
            if native_transaction:
                native_transaction.rollback()
            raise

    def redirect(self):
        record = self.state().get("current")
        if not record:
            return False
        package = self._record(record)
        if getattr(sys, "frozen", False) and self._verify_package(package, record).resolve() == Path(sys.executable).resolve():
            return False
        self.launch(record)
        return True


def acknowledge_startup():
    token = os.environ.pop("SHUANGSHENG_OTA_READY", "")
    if re.fullmatch(r"[0-9a-f]{32}", token):
        root = update_root()
        directory = root / "launch"
        if directory.is_dir():
            (directory / token).write_text("ready\n", encoding="ascii")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Check or install a verified stable Shuangsheng release.")
    parser.add_argument("action", choices=["check", "download", "install", "rollback", "status"])
    parser.add_argument("--bootstrap", action="store_true", help="First managed install from source; permit the same published version, never an older one")
    parser.add_argument("--report", type=Path, help="Write a successful check as JSON, including in windowed builds")
    args = parser.parse_args(argv)
    if args.bootstrap and args.action not in {"check", "download", "install"}:
        parser.error("--bootstrap applies only to check, download or install")
    if args.report and args.action != "check":
        parser.error("--report applies only to check")
    updater = Updater(current_version="0.0.0" if args.bootstrap else None)
    try:
        if args.report:
            args.report.unlink(missing_ok=True)
        if args.action == "status":
            print(json.dumps({"version": updater.current_version, "target": updater.target, **updater.state()}, ensure_ascii=False))
        elif args.action == "rollback":
            record = updater.rollback()
            updater.launch(record)
        else:
            offer = updater.check()
            if args.bootstrap and offer and version_tuple(offer["version"]) < version_tuple(application_version()):
                raise UpdateError("The latest published release is older than this updater; wait for its release before bootstrapping.")
            print(json.dumps(offer or {"current": updater.current_version, "available": False}, ensure_ascii=False))
            if args.report:
                _atomic_json(args.report, {"url": API_URL, "current": updater.current_version, "update": offer})
            if offer and args.action != "check":
                record = updater.download(offer)
                if args.action == "install":
                    updater.install(record)
        return 0
    except (UpdateError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
