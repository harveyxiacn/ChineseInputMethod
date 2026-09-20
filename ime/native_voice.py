"""One-shot local dictation worker for the Fcitx5 addon.

SIGUSR1 stops recording and starts transcription. SIGTERM cancels everything.
Protocol mode emits live preview frames and a final transcript; diagnostics go to stderr.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import struct
import time
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
from .live_speech import LiveTranscript


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


def recording_snapshot(path):
    """Rebuild a WAV from complete PCM samples; pw-record finalizes sizes on exit."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    offset = 12
    fmt = None
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        offset += 8
        if kind == b"fmt " and size >= 16 and offset + size <= len(data):
            fmt = struct.unpack_from("<HHIIHH", data, offset)
        if kind == b"data":
            if not fmt or fmt[:2] != (1, 1) or fmt[2] != 16000 or fmt[5] != 16:
                return None
            pcm = data[offset:offset + 115 * 16000 * 2]
            pcm = pcm[:len(pcm) // 2 * 2]
            if len(pcm) < 16000:
                return None
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes(pcm)
            return output.getvalue()
        offset += size + size % 2
    return None


def record(path, stop, seconds=115, preview=None):
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
            deadline = time.monotonic() + seconds + 2
            next_preview = time.monotonic() + 3
            while process.poll() is None and not stop.wait(0.1):
                if preview and time.monotonic() >= next_preview:
                    preview(path)
                    next_preview = time.monotonic() + 3
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
            service = SpeechService()
            pending = None

            def emit(text):
                text = " ".join(text.splitlines()).strip()
                if text and args.protocol:
                    print("SHUANGSHENG_PARTIAL\n" + text + "\nSHUANGSHENG_END", flush=True)

            live = LiveTranscript(service, args.language, args.script, emit)

            def recognize_preview(audio):
                try:
                    live.update(audio)
                except Exception as exc:
                    print(f"Live preview failed: {exc}", file=sys.stderr, flush=True)

            def preview(path):
                nonlocal pending
                if pending is not None and not pending.done():
                    return
                audio = recording_snapshot(path)
                if audio:
                    pending = executor.submit(recognize_preview, audio)

            with ThreadPoolExecutor(max_workers=1) as executor:
                record(path, stop, preview=preview if args.protocol else None)
                if pending is not None:
                    pending.result()
                if args.protocol:
                    result = {"text": live.update(path.read_bytes(), final=True)}
                else:
                    result = service.transcribe(path.read_bytes(), args.language, args.script)
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
