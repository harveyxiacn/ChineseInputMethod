"""Bounded microphone capture and cancellable desktop dictation jobs.

This module has no GUI or audio-library imports at import time. All callbacks
crossing into a GUI must be consumed from ``DictationJob.events`` on its thread.
"""
from __future__ import annotations

from array import array
import io
import queue
import sys
import threading
import time
import wave

MAX_RECORDING_SECONDS = 115


class RecordingError(RuntimeError):
    pass


class RecordingCancelled(Exception):
    pass


def audio_backend():
    try:
        import sounddevice
        return sounddevice
    except (ImportError, OSError) as exc:
        raise RecordingError(
            "无法加载麦克风组件。源码安装请运行 pip install -r requirements-desktop.txt；"
            "Linux 还需安装 libportaudio2。"
        ) from exc


def input_devices(backend=None):
    backend = backend or audio_backend()
    try:
        return [(index, device["name"]) for index, device in enumerate(backend.query_devices())
                if device["max_input_channels"] > 0]
    except Exception as exc:
        raise RecordingError("无法枚举麦克风，请检查系统声音设置和麦克风权限。") from exc


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    if len(pcm) % 2 or not 8000 <= sample_rate <= 48000:
        raise RecordingError("麦克风返回的音频格式无效。")
    if len(pcm) < sample_rate * 2 // 5:
        raise RecordingError("录音太短，请说话至少 0.2 秒后再停止。")
    if len(pcm) > sample_rate * 2 * MAX_RECORDING_SECONDS:
        raise RecordingError("录音超过时长上限。")
    samples = array("h")
    samples.frombytes(pcm)
    if max((abs(value) for value in samples), default=0) < 16:
        raise RecordingError("麦克风只录到静音，请检查输入设备、音量和静音开关。")
    if sys.byteorder != "little":
        samples.byteswap()
        pcm = samples.tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def record_microphone(stop, cancel, device=None, *, backend=None,
                      seconds=MAX_RECORDING_SECONDS, on_started=None):
    """Capture mono native-endian int16 PCM in memory; never write audio to disk."""
    if not 0 < seconds <= MAX_RECORDING_SECONDS:
        raise ValueError("Invalid recording duration")
    if cancel.is_set():
        raise RecordingCancelled()
    backend = backend or audio_backend()
    chunks = []
    finished = threading.Event()
    overflow = threading.Event()
    captured = 0
    try:
        info = backend.query_devices(device, "input")
        sample_rate = min(48000, int(info["default_samplerate"]))
        if sample_rate < 8000:
            raise RecordingError("所选麦克风的采样率不受支持，请选择其他输入设备。")
        max_frames = int(sample_rate * seconds)

        def callback(data, frames, _time, status):
            nonlocal captured
            if cancel.is_set() or stop.is_set():
                raise backend.CallbackStop()
            if status:
                overflow.set()
            count = min(frames, max_frames - captured)
            if count > 0:
                chunks.append(bytes(data)[:count * 2])
                captured += count
            if captured >= max_frames:
                raise backend.CallbackStop()

        with backend.RawInputStream(
            samplerate=sample_rate, blocksize=max(1, sample_rate // 10),
            device=device, channels=1, dtype="int16", callback=callback,
            finished_callback=finished.set,
        ):
            if on_started:
                on_started()
            deadline = time.monotonic() + seconds + 2
            while not finished.wait(0.05):
                if stop.is_set() or cancel.is_set():
                    break
                if time.monotonic() >= deadline:
                    raise RecordingError("麦克风停止响应，请检查设备后重试。")
        if cancel.is_set():
            raise RecordingCancelled()
        if overflow.is_set():
            raise RecordingError("录音发生中断或丢帧，请关闭占用音频的程序后重试。")
        return pcm_to_wav(b"".join(chunks), sample_rate)
    except (RecordingError, RecordingCancelled):
        raise
    except Exception as exc:
        raise RecordingError(
            "无法录音，请检查麦克风连接和系统权限，或选择其他输入设备。"
        ) from exc


class DictationJob:
    """One worker at a time. Cancellation discards an in-flight inference result.

    Whisper's native inference cannot safely be interrupted within this process;
    a cancelled job stays busy until it returns, preventing overlapping models.
    """
    def __init__(self, service, recorder=record_microphone):
        self.service = service
        self.recorder = recorder
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, language="zh", script="simplified", device=None):
        if language not in {"zh", "yue", "auto"} or script not in {
                "simplified", "traditional", "original"}:
            raise ValueError("Invalid language or script")
        with self._lock:
            if self.busy:
                return False
            self.stop_event.clear()
            self.cancel_event.clear()
            self._thread = threading.Thread(
                target=self._run, args=(language, script, device), daemon=True,
                name="desktop-dictation",
            )
            self._thread.start()
            return True

    def stop(self):
        self.stop_event.set()

    def cancel(self):
        self.cancel_event.set()
        self.stop_event.set()

    def _run(self, language, script, device):
        try:
            audio = self.recorder(
                self.stop_event, self.cancel_event, device,
                on_started=lambda: self.events.put(("recording", None)),
            )
            if self.cancel_event.is_set():
                raise RecordingCancelled()
            self.events.put(("transcribing", None))
            result = self.service.transcribe(audio, language, script)
            if self.cancel_event.is_set():
                raise RecordingCancelled()
            text = result["text"].strip()
            if not text:
                raise RecordingError("没有识别出语音，请检查麦克风并重新录音。")
            self.events.put(("result", text))
        except RecordingCancelled:
            self.events.put(("cancelled", None))
        except Exception as exc:
            if self.cancel_event.is_set():
                self.events.put(("cancelled", None))
            else:
                self.events.put(("error", str(exc)))
        finally:
            self.events.put(("idle", None))


class GlobalShortcut:
    """Optional listener. No keystrokes are saved or simulated."""
    def __init__(self, callback):
        self.callback = callback
        self.listener = None

    def start(self):
        import os
        if sys.platform.startswith("linux") and (
                os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY")):
            return "Wayland 下请使用窗口中的录音按钮。"
        try:
            if sys.platform == "darwin":
                # pynput's class attribute stays False: it updates only the
                # running listener instance. Query macOS directly, without a
                # permission prompt or waiting for a listener on Tk's thread.
                from HIServices import AXIsProcessTrusted
                if not AXIsProcessTrusted():
                    return "全局快捷键需在系统设置中允许辅助功能/输入监控；授权后重启应用，也可使用录音按钮。"
            from pynput import keyboard
            self.listener = keyboard.GlobalHotKeys({"<ctrl>+<alt>+<space>": self.callback})
            self.listener.start()
            return "Ctrl+Alt+Space 开始/停止录音（macOS：Control+Option+Space）。"
        except Exception:
            self.stop()
            return "全局快捷键不可用，请使用窗口中的录音按钮。"

    def stop(self):
        if self.listener is not None:
            try:
                self.listener.stop()
            except Exception:
                # A platform backend may fail before its native hook exists.
                pass
            finally:
                self.listener = None
