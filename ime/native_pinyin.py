"""Optional libime decoder with a lazy shared process and bounded requests."""
from __future__ import annotations
import atexit
import os
from pathlib import Path
from queue import Queue, Empty, Full
import shutil
import subprocess
import threading
import time


def bridge_path():
    override = os.environ.get('IME_PINYIN_BRIDGE')
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    installed = shutil.which('shuangsheng-pinyin-bridge')
    if installed:
        return Path(installed)
    for path in (Path(__file__).resolve().parents[1] / '.cache/pinyin-bridge/shuangsheng-pinyin-bridge',
                 Path.home() / '.local/bin/shuangsheng-pinyin-bridge'):
        if path.is_file(): return path
    return None


class _Bridge:
    def __init__(self, path, timeout=3):
        self.path = Path(path)
        self.timeout = timeout
        self.process = None
        self.lines = None
        self.fingerprint = None
        self.lock = threading.RLock()

    def _start(self):
        self.fingerprint = self.path.stat().st_mtime_ns
        self.process = subprocess.Popen([str(self.path), '--serve'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding='ascii', bufsize=1)
        process, queue = self.process, Queue(maxsize=128)
        self.lines = queue
        def receive():
            try:
                for line in process.stdout:
                    if len(line) > 8192: break
                    queue.put(line.rstrip('\n'), timeout=self.timeout)
            except (OSError, UnicodeError, ValueError, Full):
                pass
            finally:
                try: queue.put(None, timeout=0.1)
                except Full: pass
        threading.Thread(target=receive, daemon=True, name='pinyin-bridge-reader').start()

    def close(self):
        with self.lock:
            process, self.process = self.process, None
            self.lines = None
            if not process: return
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=0.2)
            if process.stdin: process.stdin.close()
            if process.stdout: process.stdout.close()

    def decode(self, query, scheme, context, limit):
        if not self.lock.acquire(timeout=self.timeout):
            return []
        try:
            try:
                if not self.process or self.process.poll() is not None or self.fingerprint != self.path.stat().st_mtime_ns:
                    self.close(); self._start()
                query = query.replace(' ', "'")
                request = '\t'.join((query.encode().hex(), scheme, context[-120:].encode().hex(), str(limit)))
                self.process.stdin.write(request + '\n'); self.process.stdin.flush()
                result, deadline = [], time.monotonic() + self.timeout
                while True:
                    line = self.lines.get(timeout=max(0.001, deadline - time.monotonic()))
                    if line == '.': return result
                    if line is None or line == '!': raise ValueError('bridge stopped or rejected request')
                    if len(result) >= 50 or '\t' not in line: raise ValueError('invalid bridge response')
                    text, pinyin = (bytes.fromhex(part).decode('utf-8') for part in line.split('\t', 1))
                    if not text: raise ValueError('empty bridge candidate')
                    result.append({'text': text, 'pinyin': pinyin.replace("'", ' ')})
            except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, Empty):
                self.close()
                return []
        finally:
            self.lock.release()


_shared = None
_shared_lock = threading.RLock()


def close():
    global _shared
    with _shared_lock:
        if _shared: _shared.close()
        _shared = None


atexit.register(close)


def decode(query, *, scheme='pinyin', context='', limit=9):
    global _shared
    executable = bridge_path()
    if not executable: return []
    # Synchronize lifecycle/path changes and requests across web/desktop threads.
    with _shared_lock:
        if _shared is None or _shared.path != executable:
            close(); _shared = _Bridge(executable)
        bridge = _shared
    return bridge.decode(query, scheme, context, min(limit, 50))


def predict(context, limit=5):
    if not isinstance(context, str) or type(limit) is not int or not 0 <= limit <= 50:
        raise ValueError('context must be text and limit an integer from 0 to 50')
    if not context or limit == 0:
        return []
    return decode('', scheme='predict', context=context, limit=limit)
