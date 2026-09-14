"""One-shot local dictation worker for the Fcitx5 addon.

SIGUSR1 stops recording and starts transcription. SIGTERM cancels everything.
Only the final transcript is written to stdout; diagnostics go to stderr.
"""
import argparse
from array import array
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import wave

from .speech import SpeechService


class Cancelled(BaseException):
    pass


def stop_recorder(process):
    if process.poll() is not None:
        return False
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return False
    return True


def validate_recording(path):
    try:
        with wave.open(str(path), "rb") as recording:
            if recording.getsampwidth() != 2 or recording.getnchannels() != 1:
                raise RuntimeError("Microphone returned an unsupported audio format.")
            samples = array("h", recording.readframes(recording.getnframes()))
            if sys.byteorder != "little":
                samples.byteswap()
            duration = len(samples) / recording.getframerate()
    except (OSError, EOFError, wave.Error) as exc:
        raise RuntimeError("No valid microphone audio was captured. Check the PipeWire input device.") from exc
    if duration < 0.2:
        raise RuntimeError("Recording was too short. Speak for at least a moment before stopping.")
    if not samples or max(abs(sample) for sample in samples) < 16:
        raise RuntimeError("Microphone captured silence. Check that your input device is selected and unmuted.")


def record(path, stop, seconds=115):
    executable = shutil.which("pw-record")
    if not executable:
        raise RuntimeError("Install PipeWire's pw-record to use microphone dictation.")
    command = [executable, "--rate", "16000", "--channels", "1", "--format", "s16",
               "--sample-count", str(seconds * 16000), str(path)]
    target = os.environ.get("IME_RECORD_TARGET")
    if target:
        command[1:1] = ["--target", target]
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
            interrupted = stop_recorder(process)
        errors.seek(0)
        # pw-record prints its output filename even on success. Keep real errors.
        detail = " ".join(line.strip() for line in errors.read(8192).decode(
            "utf-8", errors="replace").splitlines() if line.strip() != str(path))
        normal_exit = process.returncode == 0
        interrupted_exit = interrupted and process.returncode in (
            1, -signal.SIGINT, 128 + signal.SIGINT)
        if not normal_exit and (not interrupted_exit or detail):
            raise RuntimeError("Microphone recording failed: " + (detail[:400] or
                               "check PipeWire and microphone permissions."))
        validate_recording(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["zh", "yue"], default="zh")
    parser.add_argument("--script", choices=["simplified", "traditional"], default="simplified")
    parser.add_argument("--protocol", action="store_true", help="Frame successful output for the native addon")
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
                text = result["text"].strip()
                if args.protocol:
                    print("SHUANGSHENG_OK\n" + text + "\nSHUANGSHENG_END", flush=True)
                else:
                    print(text, flush=True)
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
