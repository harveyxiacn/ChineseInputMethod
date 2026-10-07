"""Bounded local dictation windows with one second of boundary context."""
import io
import wave
from array import array
import sys

RATE = 16000
MIN_PREVIEW = 3 * RATE
WINDOW = 12 * RATE
MIN_BOUNDARY = 5 * RATE
OVERLAP = RATE


def join_text(prefix, tail, *, overlap=False):
    """Remove exact boundary repetition only when the audio windows overlap.

    Never collapse a single Chinese character or part of an English word:
    repeated words and negation in nonoverlapping speech must survive.
    """
    if overlap:
        for size in range(min(len(prefix), len(tail), 80), 1, -1):
            if prefix[-size:] != tail[:size]:
                continue
            fragment = tail[:size]
            if any("\u3400" <= char <= "\u9fff" for char in fragment):
                tail = tail[size:]
                break
            before = prefix[-size - 1] if len(prefix) > size else " "
            after = tail[size] if len(tail) > size else " "
            if not before.isalnum() and not after.isalnum():
                tail = tail[size:]
                break
    tail = tail.lstrip()
    if prefix and tail and prefix[-1].isascii() and prefix[-1].isalnum() and tail[0].isascii() and tail[0].isalnum():
        return prefix + " " + tail
    return prefix + tail


def wav_bytes(pcm):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm)
    return output.getvalue()


def pause_boundary(pcm):
    """Prefer a >= 300 ms quiet stretch between 5 and 12 seconds."""
    samples = array("h", pcm[:WINDOW * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    block = RATE // 10
    quiet = 0
    for offset in range(0, len(samples) - block + 1, block):
        energy = sum(value * value for value in samples[offset:offset + block]) / block
        quiet = quiet + 1 if energy < 200 ** 2 else 0
        end = offset + block
        if quiet >= 3 and end >= MIN_BOUNDARY:
            return end
    return WINDOW if len(samples) >= WINDOW else None


class LiveTranscript:
    def __init__(self, service, language, script, emit, *, hotwords=None, initial_prompt=None):
        self.service = service
        self.language = language
        self.script = script
        self.emit = emit
        self.offset = 0  # PCM sample offset; advances only after successful inference.
        self.confirmed = ""
        self.draft = ""
        self.draft_end = 0
        self._overlap = 0
        self.hotwords = hotwords
        self.initial_prompt = initial_prompt

    @property
    def text(self):
        return join_text(self.confirmed, self.draft, overlap=bool(self._overlap))

    @property
    def snapshot(self):
        return {"text": self.text, "stable_text": self.confirmed,
                "revisable_tail": self.text[len(self.confirmed):],
                "processed_samples": self.offset, "preview_samples": self.draft_end}

    def recognize(self, pcm):
        options = {"fast": True}
        if self.hotwords is not None:
            options["hotwords"] = self.hotwords
        if self.initial_prompt is not None:
            options["initial_prompt"] = self.initial_prompt
        return self.service.transcribe(wav_bytes(pcm), self.language, self.script, **options)["text"].strip()

    def _finalize(self, total):
        self.confirmed = self.text
        self.draft = ""
        self.offset = total
        self.draft_end = total
        self._overlap = 0

    def update(self, audio, final=False):
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (RATE, 1, 2):
                raise ValueError("Live dictation needs 16 kHz mono PCM.")
            pcm = wav.readframes(wav.getnframes())
        total = len(pcm) // 2
        if total < self.offset or total < self.draft_end:
            raise ValueError("Live audio must grow monotonically.")
        while self.offset < total:
            remaining = pcm[self.offset * 2:]
            boundary = pause_boundary(remaining)
            if boundary is not None:
                # A complete window is finalized once, even if capture runs ahead.
                start = max(0, self.offset - self._overlap)
                text = self.recognize(pcm[start * 2:(self.offset + boundary) * 2])
                if not text and self.draft:
                    # Do not erase an already displayed draft on an empty decode.
                    text = self.draft
                self.confirmed = join_text(self.confirmed, text, overlap=bool(self._overlap))
                self.offset += boundary
                self._overlap = OVERLAP if boundary == WINDOW else 0
                self.draft = ""
                self.draft_end = self.offset
                self.emit(self.text)
                continue
            if total == self.draft_end:
                # Stop after the latest preview: no second identical inference.
                if final:
                    self._finalize(total)
                return self.text
            if not final and total - self.offset < MIN_PREVIEW:
                return self.text
            text = self.recognize(pcm[max(0, self.offset - self._overlap) * 2:])
            # Keep a nonempty draft if a later decode unexpectedly returns empty.
            if text:
                self.draft = text
            self.draft_end = total
            self.emit(self.text)
            if final:
                self._finalize(total)
            break
        return self.text
