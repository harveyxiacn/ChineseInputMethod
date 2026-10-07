"""Per-user Linux speech worker. Audio travels over a private Unix socket.

The worker owns one reusable model; capture processes can exit independently.
No request may select an executable, model path, or filesystem destination.
"""
from __future__ import annotations

import argparse
import contextlib
try:
    import fcntl
except ImportError:  # This optional worker is Linux-only.
    fcntl = None
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

from .speech import MAX_AUDIO_BYTES, SpeechError, SpeechService
from . import __version__

MAX_HEADER = 16384
MAX_RESPONSE = 1024 * 1024


def private_directory(path=None):
    if fcntl is None or not hasattr(socket, "SO_PEERCRED"):
        raise SpeechError("Reusable native speech requires Linux Unix sockets.", 503)
    path = Path(path or os.environ.get("IME_SPEECH_RUNTIME") or
                (Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"shuangsheng-{os.getuid()}-{__version__}"))
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SpeechError("Speech runtime directory must be private and owned by this user.", 503)
    if len(os.fsencode(path / "speech.sock")) > 100:
        raise SpeechError("Speech runtime directory path is too long.", 503)
    return path


@contextlib.contextmanager
def file_lock(path, *, blocking=True):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SpeechError("Invalid speech worker lock permissions.", 503)
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def receive_exact(connection, size):
    result = bytearray()
    while len(result) < size:
        block = connection.recv(min(size - len(result), 65536))
        if not block:
            raise ValueError("Incomplete speech request")
        result.extend(block)
    return bytes(result)


def receive_json(connection, limit):
    size = struct.unpack("!I", receive_exact(connection, 4))[0]
    if not 0 < size <= limit:
        raise ValueError("Speech message exceeds its limit")
    data = json.loads(receive_exact(connection, size))
    if not isinstance(data, dict):
        raise ValueError("Speech message must be an object")
    return data


def send_json(connection, value, limit=MAX_RESPONSE):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > limit:
        raise ValueError("Speech response exceeds its limit")
    connection.sendall(struct.pack("!I", len(data)) + data)


def check_peer(connection):
    if not hasattr(socket, "SO_PEERCRED"):
        raise SpeechError("The native speech worker requires Linux peer credentials.", 503)
    _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != os.getuid():
        raise SpeechError("Speech peer belongs to another user.", 403)


class SpeechDaemonClient:
    def __init__(self, directory=None, timeout=180):
        self.directory = directory
        self.timeout = timeout

    def _connect(self, directory):
        target = directory / "speech.sock"
        info = target.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise SpeechError("Invalid speech worker socket.", 503)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self.timeout)
            connection.connect(str(target))
            check_peer(connection)
            return connection
        except BaseException:
            connection.close()
            raise

    def _connection(self, start):
        directory = private_directory(self.directory)
        try:
            return self._connect(directory)
        except (FileNotFoundError, ConnectionRefusedError):
            if not start:
                return None
        with file_lock(directory / "start.lock"):
            try:
                return self._connect(directory)
            except (FileNotFoundError, ConnectionRefusedError):
                command = ([sys.executable, "--speech-daemon"] if getattr(sys, "frozen", False)
                           else [sys.executable, "-m", "ime.speech_daemon"])
                process = subprocess.Popen(
                    [*command, "--directory", str(directory)],
                    cwd=str(Path(__file__).resolve().parents[1]), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                    close_fds=True, env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                )
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        return self._connect(directory)
                    except (FileNotFoundError, ConnectionRefusedError):
                        if process.poll() is not None:
                            break
                        time.sleep(0.05)
        raise SpeechError("Could not start the reusable local speech worker.", 503)

    def _request(self, op, audio=b"", **options):
        if not isinstance(audio, bytes) or len(audio) > MAX_AUDIO_BYTES:
            raise SpeechError("Invalid or oversized speech request.", 413)
        connection = self._connection(op in {"transcribe", "warmup"})
        if connection is None:
            if op == "status":
                result = SpeechService().status()
                return {**result, "worker_running": False}
            return {"loaded": False, "worker_running": False}
        try:
            with connection:
                send_json(connection, {"op": op, "audio_bytes": len(audio), **options}, MAX_HEADER)
                if audio:
                    connection.sendall(audio)
                response = receive_json(connection, MAX_RESPONSE)
            if not response.get("ok"):
                raise SpeechError(response.get("error", "Speech worker failed."), response.get("status_code", 500))
            return response["result"]
        except (OSError, ValueError, KeyError) as exc:
            raise SpeechError("Local speech worker disconnected. Please retry.", 503) from exc

    def transcribe(self, audio, language="auto", script="simplified", *, fast=False,
                   hotwords=None, initial_prompt=None):
        if isinstance(hotwords, (list, tuple)):
            if len(hotwords) > 100 or not all(isinstance(word, str) for word in hotwords):
                raise SpeechError("Invalid hotwords.", 400)
            hotwords = ", ".join(hotwords)
        return self._request("transcribe", audio, language=language, script=script,
                             fast=fast, hotwords=hotwords, initial_prompt=initial_prompt)

    def status(self):
        return self._request("status")

    def warmup(self):
        return self._request("warmup")

    def unload(self):
        return self._request("unload")


class SpeechWorker:
    def __init__(self, service=None):
        self.service = service or SpeechService()
        self.last_used = time.monotonic()

    def unload_if_idle(self, idle_seconds):
        if time.monotonic() - self.last_used > idle_seconds:
            try:
                self.service.unload()
                self.last_used = time.monotonic()
            except SpeechError:
                pass  # Active inference holds the model lock; retry later.

    def handle(self, connection):
        active_operation = False
        try:
            connection.settimeout(180)
            check_peer(connection)
            header = receive_json(connection, MAX_HEADER)
            if set(header) - {"op", "audio_bytes", "language", "script", "fast", "hotwords", "initial_prompt"}:
                raise ValueError("Unexpected speech request field")
            size = header.get("audio_bytes", 0)
            if type(size) is not int or not 0 <= size <= MAX_AUDIO_BYTES:
                raise ValueError("Invalid speech audio size")
            op = header.get("op")
            if op not in {"transcribe", "status", "warmup", "unload"}:
                raise ValueError("Unknown speech operation")
            if op != "transcribe" and size:
                raise ValueError("This operation does not accept audio")
            active_operation = op in {"transcribe", "warmup"}
            if active_operation:
                self.last_used = time.monotonic()
            if op == "transcribe":
                if not size or type(header.get("fast", False)) is not bool:
                    raise ValueError("Invalid transcription request")
                for key in ("hotwords", "initial_prompt"):
                    value = header.get(key)
                    if value is not None and (not isinstance(value, str) or len(value) > 2000):
                        raise ValueError("Invalid speech vocabulary prompt")
                result = self.service.transcribe(receive_exact(connection, size),
                    language=header.get("language", "auto"), script=header.get("script", "simplified"),
                    fast=header.get("fast", False), hotwords=header.get("hotwords"),
                    initial_prompt=header.get("initial_prompt"))
            elif op == "status":
                result = {**self.service.status(), "worker_running": True}
            else:
                getattr(self.service, op)()
                result = {**self.service.status(), "worker_running": True}
            send_json(connection, {"ok": True, "result": result})
        except Exception as exc:
            try:
                send_json(connection, {"ok": False, "error": str(exc),
                                      "status_code": getattr(exc, "status_code", 400)})
            except (OSError, ValueError):
                pass
        finally:
            if active_operation:
                self.last_used = time.monotonic()
            connection.close()


def serve(directory=None, idle_seconds=300):
    directory = private_directory(directory)
    worker = SpeechWorker()
    slots = threading.BoundedSemaphore(4)
    with file_lock(directory / "server.lock", blocking=False):
        target = directory / "speech.sock"
        if target.exists() or target.is_symlink():
            info = target.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise SpeechError("Refusing to replace an invalid speech socket.", 503)
            target.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(target))
            os.chmod(target, 0o600)
            listener.listen(4)
            listener.settimeout(1)
            def run(connection):
                try:
                    worker.handle(connection)
                finally:
                    slots.release()
            try:
                while True:
                    worker.unload_if_idle(idle_seconds)
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    if slots.acquire(blocking=False):
                        threading.Thread(target=run, args=(connection,), daemon=True).start()
                    else:
                        connection.close()
            finally:
                target.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory")
    parser.add_argument("--idle-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.idle_seconds < 1:
        parser.error("idle-seconds must be positive")
    serve(args.directory, args.idle_seconds)


if __name__ == "__main__":
    main()
