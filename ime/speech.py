"""Optional offline Mandarin/Cantonese transcription using faster-whisper."""
import importlib.util
import io
import os
import threading
from pathlib import Path

from .conversion import ConversionError, convert_text

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_AUDIO_SECONDS = 120
SAMPLE_RATE = 16000

# Match download_model.py and keep large cached weights inside this workspace.
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[1] / ".cache" / "huggingface"))


class SpeechError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class SpeechService:
    def __init__(self):
        self.model_name = os.environ.get("IME_WHISPER_MODEL", "large-v3")
        self.device = os.environ.get("IME_WHISPER_DEVICE", "cpu")
        self._model = None
        self._lock = threading.Lock()

    def status(self) -> dict:
        installed = importlib.util.find_spec("faster_whisper") is not None
        return {
            "available": installed,
            "model": self.model_name,
            "loaded": self._model is not None,
            "detail": (
                "Local model loaded; audio stays on this server."
                if self._model is not None else
                "Speech dependencies installed. A cached model is required; readiness is checked on first use."
                if installed else
                "Install speech requirements and download a Whisper large-v3 model to enable dictation."
            ),
        }

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SpeechError("Install the speech requirements to enable local dictation.", 503) from exc
        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type="float16" if self.device == "cuda" else "int8",
                local_files_only=True,
            )
        except Exception as exc:
            raise SpeechError(
                f"Could not load cached Whisper model {self.model_name!r}. "
                "Download the model using the project setup command first, and check IME_WHISPER_DEVICE.",
                503,
            ) from exc
        return self._model

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

    def transcribe(self, audio: bytes, language: str = "auto", script: str = "simplified") -> dict:
        if language not in {"auto", "zh", "yue"}:
            raise SpeechError("language must be auto, zh (Mandarin), or yue (Cantonese)")
        if script not in {"simplified", "traditional", "original"}:
            raise SpeechError("script must be simplified, traditional, or original")
        if not isinstance(audio, bytes) or not audio:
            raise SpeechError("Provide a nonempty audio file.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise SpeechError("Audio uploads must be at most 20 MiB.", 413)
        if not self._lock.acquire(blocking=False):
            raise SpeechError("Another recording is being transcribed. Try again shortly.", 429)
        try:
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
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = "".join(segment.text for segment in segments).strip()
            return {"text": convert_text(text, script), "language": info.language}
        except ConversionError as exc:
            raise SpeechError(str(exc), 503) from exc
        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError("Local transcription failed. Check the model and available memory, then retry.", 500) from exc
        finally:
            self._lock.release()
