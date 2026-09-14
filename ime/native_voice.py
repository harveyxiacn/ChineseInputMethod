"""One-shot local dictation worker for the Fcitx5 addon.

SIGUSR1 stops recording and starts transcription. SIGTERM cancels everything.
Only the final transcript is written to stdout; diagnostics go to stderr.
"""
import argparse
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

from .speech import SpeechService


class Cancelled(BaseException):
    pass


def stop_recorder(process):
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def record(path, stop, seconds=115):
    executable = shutil.which("pw-record")
    if not executable:
        raise RuntimeError("Install PipeWire's pw-record to use microphone dictation.")
    command = [executable, "--rate", "16000", "--channels", "1", "--format", "s16",
               "--sample-count", str(seconds * 16000), str(path)]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=errors)
        try:
            import time
            deadline = time.monotonic() + seconds + 2
            while process.poll() is None and not stop.wait(0.1):
                if time.monotonic() >= deadline:
                    break
        finally:
            stop_recorder(process)
        if not path.exists() or path.stat().st_size <= 44:
            raise RuntimeError("No microphone audio was captured. Check the default PipeWire input device.")
        if process.returncode not in (0, -signal.SIGINT, 128 + signal.SIGINT):
            raise RuntimeError("Microphone recording failed. Check PipeWire and microphone permissions.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["zh", "yue"], default="zh")
    parser.add_argument("--script", choices=["simplified", "traditional"], default="simplified")
    args = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGUSR1, lambda *_: stop.set())

    def cancel(*_):
        raise Cancelled()

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    try:
        with tempfile.TemporaryDirectory(prefix="shuangsheng-voice-") as directory:
            path = Path(directory) / "recording.wav"
            record(path, stop)
            if stop.is_set():
                stop.clear()
            result = SpeechService().transcribe(path.read_bytes(), args.language, args.script)
            if result["text"].strip():
                print(result["text"].strip(), flush=True)
            else:
                raise RuntimeError("No speech recognized. Try again with a clearer recording.")
    except Cancelled:
        return 130
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
