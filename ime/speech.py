"""Optional offline Mandarin/Cantonese transcription using faster-whisper."""
import importlib.util
import io
import logging
import os
from pathlib import Path
import threading
import time
import gc

from .conversion import ConversionError, convert_text
from .paths import configure_model_cache

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_AUDIO_SECONDS = 120
SAMPLE_RATE = 16000
MAX_HINT_CHARS = 2000
MODEL_REPOS = {
    "large": "Systran/faster-whisper-large-v3",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
}

configure_model_cache()


class SpeechError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class SpeechService:
    def __init__(self, settings=None, lexicon=None):
        self._settings = settings
        self._lexicon = lexicon
        self._model = None
        self._lock = threading.Lock()
        self._loading = False
        self._last_error = None
        self._last_used = time.monotonic()
        self._loaded_device = None
        self._refresh_configuration()

    def _refresh_configuration(self):
        from .settings import SettingsStore
        config = (self._settings or SettingsStore()).snapshot()
        backend = os.environ.get("IME_SPEECH_BACKEND", config.get("backend", "whisper"))
        backend = "faster-whisper" if backend == "whisper" else backend
        model = (os.environ.get("IME_SENSEVOICE_MODEL", config.get("sensevoice_model", ""))
                 if backend == "sensevoice" else os.environ.get("IME_WHISPER_MODEL", config.get("model", "large-v3")))
        device = os.environ.get("IME_WHISPER_DEVICE", config.get("device", "cpu"))
        previous = (getattr(self, "backend", None), getattr(self, "model_name", None), getattr(self, "device", None))
        if previous != (backend, model, device):
            self._model = None
            self._loaded_device = None
            self._last_error = None
        self.backend, self.model_name, self.device = backend, model, device
        self._configured_hotwords = config.get("hotwords", [])
        self._configured_prompt = config.get("initial_prompt") or None

    def _default_hotwords(self):
        terms = list(self._configured_hotwords)
        try:
            from .lexicon import LexiconStore
            terms.extend(entry["text"] for entry in (self._lexicon or LexiconStore()).entries())
        except ImportError:
            pass
        # Stable bounded selection; only explicitly saved terminology is used.
        selected = []
        length = 0
        for term in dict.fromkeys(term for term in terms if isinstance(term, str) and term.strip()):
            if len(selected) == 100:
                break
            extra = len(term) + (2 if selected else 0)
            if length + extra <= MAX_HINT_CHARS:
                selected.append(term)
                length += extra
        return selected or None

    @staticmethod
    def _hint(label, value):
        if label == "hotwords" and isinstance(value, (list, tuple)):
            if len(value) > 100 or any(not isinstance(term, str) for term in value):
                raise SpeechError("hotwords must contain at most 100 text terms")
            value = ", ".join(value)
        if value is not None and (not isinstance(value, str) or len(value) > MAX_HINT_CHARS):
            raise SpeechError(f"{label} must be text of at most {MAX_HINT_CHARS} characters")
        return value

    def _cached_model(self):
        """Check complete local weights without loading a model or networking."""
        if self.backend == "sensevoice":
            from .sensevoice import SenseVoiceAdapter
            return bool(self.model_name) and SenseVoiceAdapter.cached(self.model_name)
        direct = Path(self.model_name).expanduser()
        if self._complete_whisper_path(direct):
            return True
        repo = self.model_name if "/" in self.model_name else MODEL_REPOS.get(self.model_name)
        if repo is None:
            repo = ("Systran/faster-distil-whisper-" + self.model_name[7:]
                    if self.model_name.startswith("distil-") else "Systran/faster-whisper-" + self.model_name)
        root = Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE") or configure_model_cache() / "hub")
        for snapshot in (root / ("models--" + repo.replace("/", "--")) / "snapshots").glob("*"):
            if self._complete_whisper_path(snapshot):
                return True
        return False

    @staticmethod
    def _complete_whisper_path(path):
        return all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))

    def _resolve_model_path(self):
        path = Path(self.model_name).expanduser()
        if not path.is_dir():
            from faster_whisper.utils import download_model
            path = Path(download_model(self.model_name, local_files_only=True))
        if not self._complete_whisper_path(path):
            raise ValueError("Cached Whisper model must include model.bin, config.json and tokenizer.json")
        return str(path)

    def status(self) -> dict:
        if self._lock.acquire(blocking=False):
            try:
                self._refresh_configuration()
            finally:
                self._lock.release()
        installed = importlib.util.find_spec("faster_whisper") is not None
        if self.backend == "sensevoice":
            installed = installed and importlib.util.find_spec("funasr") is not None
        valid_backend = self.backend in {"faster-whisper", "sensevoice"}
        installed = installed and valid_backend
        cached = self._cached_model()
        loaded = self._model is not None
        error = self._last_error or (None if valid_backend else "IME_SPEECH_BACKEND must be faster-whisper or sensevoice")
        missing = ("Install base speech requirements and optional FunASR dependencies; select a complete local SenseVoiceSmall directory."
                   if self.backend == "sensevoice" else "Install speech requirements and download a Whisper large-v3 model to enable dictation.")
        return {
            "available": installed,
            "installed": installed,
            "cached": cached,
            "ready": bool((loaded or (installed and cached)) and not error),
            "backend": self.backend,
            "device": self._loaded_device or self.device,
            "configured_device": self.device,
            "loading": self._loading,
            "busy": self._lock.locked(),
            "error": error,
            "model": self.model_name,
            "loaded": loaded,
            "detail": (
                error if error else
                "Local model loaded; audio stays on this server."
                if self._model is not None else
                "Cached weights found; device readiness is checked during warmup."
                if installed and cached else
                "Speech dependencies installed, but complete cached weights were not found. Download the model first."
                if installed else
                missing
            ),
        }

    def _load_model(self):
        if self._model is not None:
            return self._model
        if self.backend not in {"faster-whisper", "sensevoice"}:
            raise SpeechError("IME_SPEECH_BACKEND must be faster-whisper or sensevoice", 503)
        if self.backend == "sensevoice":
            self._loading = True
            try:
                from .sensevoice import SenseVoiceAdapter
                if not self.model_name:
                    raise ValueError("Set IME_SENSEVOICE_MODEL to a complete local SenseVoiceSmall directory")
                self._model = SenseVoiceAdapter(self.model_name, self.device)
                self._loaded_device = self._model.device
                self._last_error = None
                return self._model
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {str(exc)[:250]}"
                raise SpeechError("Could not load local SenseVoice. " + self._last_error, 503) from exc
            finally:
                self._loading = False
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SpeechError("Install the speech requirements to enable local dictation.", 503) from exc
        try:
            self._loading = True
            self._model = WhisperModel(
                self._resolve_model_path(),
                device=self.device,
                compute_type="float16" if self.device == "cuda" else "int8",
                local_files_only=True,
            )
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {str(exc)[:250]}"
            raise SpeechError(
                f"Could not load cached Whisper model {self.model_name!r}. "
                "Download the model using the project setup command first, and check IME_WHISPER_DEVICE. "
                + self._last_error,
                503,
            ) from exc
        finally:
            self._loading = False
        self._last_error = None
        actual_device = getattr(getattr(self._model, "model", None), "device", None)
        self._loaded_device = actual_device if isinstance(actual_device, str) else self.device
        return self._model

    def warmup(self):
        if not self._lock.acquire(blocking=False):
            raise SpeechError("Speech model is busy.", 429)
        try:
            self._refresh_configuration()
            self._load_model()
            self._last_used = time.monotonic()
        finally:
            self._lock.release()
        return self.status()

    def unload(self):
        if not self._lock.acquire(blocking=False):
            raise SpeechError("Speech model is busy.", 429)
        try:
            self._model = None
            self._loaded_device = None
            gc.collect()
        finally:
            self._lock.release()
        return self.status()

    @staticmethod
    def _decode(audio: bytes):
        # Decode a frame at a time first: compressed uploads can expand to hours
        # of audio even when their upload size is small. Do not accumulate frames.
        try:
            import av
            from faster_whisper.audio import decode_audio
            duration = 0.0
            with av.open(io.BytesIO(audio)) as container:
                if not container.streams.audio:
                    raise SpeechError("The upload contains no audio track.")
                for frame in container.decode(audio=0):
                    if not frame.sample_rate:
                        raise SpeechError("Audio has an invalid sample rate.")
                    duration += frame.samples / frame.sample_rate
                    if duration > MAX_AUDIO_SECONDS:
                        raise SpeechError("Audio must be at most 120 seconds long.", 413)
            waveform = decode_audio(io.BytesIO(audio), sampling_rate=SAMPLE_RATE)
            if len(waveform) == 0:
                raise SpeechError("The upload contains no decodable audio.")
            if len(waveform) > MAX_AUDIO_SECONDS * SAMPLE_RATE:
                raise SpeechError("Audio must be at most 120 seconds long.", 413)
            return waveform
        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError("Cannot decode this audio. Upload a valid WAV, MP3, M4A, Ogg, or WebM file.") from exc

    def transcribe(self, audio: bytes, language: str = "auto", script: str = "simplified", *, fast: bool = False,
                   hotwords=None, initial_prompt=None) -> dict:
        if not isinstance(language, str) or language not in {"auto", "zh", "yue", "en"}:
            raise SpeechError("language must be auto, zh (Mandarin), yue (Cantonese), or en (English)")
        if not isinstance(script, str) or script not in {"simplified", "traditional", "original"}:
            raise SpeechError("script must be simplified, traditional, or original")
        if not isinstance(audio, bytes) or not audio:
            raise SpeechError("Provide a nonempty audio file.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise SpeechError("Audio uploads must be at most 20 MiB.", 413)
        if type(fast) is not bool:
            raise SpeechError("fast must be a boolean")
        hotwords = self._hint("hotwords", hotwords)
        initial_prompt = self._hint("initial_prompt", initial_prompt)
        if not self._lock.acquire(blocking=False):
            raise SpeechError("Another recording is being transcribed. Try again shortly.", 429)
        try:
            self._refresh_configuration()
            if hotwords is None and self.backend == "faster-whisper":
                hotwords = self._hint("hotwords", self._default_hotwords())
            if initial_prompt is None and self.backend == "faster-whisper":
                initial_prompt = self._hint("initial_prompt", self._configured_prompt)
            if self.backend == "sensevoice" and (hotwords or initial_prompt):
                raise SpeechError("SenseVoiceSmall does not support terminology hints or initial prompts. Use faster-whisper for hints.", 422)
            # Check conversion before spending time on model inference.
            convert_text("中文", script)
            model = self._load_model()
            if language != "auto" and language not in model.supported_languages:
                raise SpeechError(
                    f"Model {self.model_name!r} does not support {language}. "
                    "Use large-v3 for Mandarin and Cantonese and download it first.", 422,
                )
            waveform = self._decode(audio)
            segments, info = model.transcribe(
                waveform,
                language=None if language == "auto" else language,
                task="transcribe",
                beam_size=1 if fast else 5,
                best_of=1 if fast else 5,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=initial_prompt,
                hotwords=hotwords,
            )
            text = "".join(segment.text for segment in segments).strip()
            self._last_error = None
            return {"text": convert_text(text, script), "language": info.language}
        except ConversionError as exc:
            raise SpeechError(str(exc), 503) from exc
        except SpeechError:
            raise
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {str(exc)[:250]}"
            # Backend exception text can contain supplied terminology or text.
            # Return diagnostics to the requesting UI, but never put it in logs.
            logging.error("Local transcription inference failed (%s)", type(exc).__name__)
            raise SpeechError("Local transcription failed. Check the model and available memory, then retry. "
                              + self._last_error, 500) from exc
        finally:
            self._last_used = time.monotonic()
            self._lock.release()
