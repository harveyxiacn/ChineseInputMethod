"""Bounded local dictation windows; confirmed audio is never decoded again."""
import io
import wave
from array import array
import sys

RATE = 16000
MIN_PREVIEW = 3 * RATE
WINDOW = 12 * RATE
MIN_BOUNDARY = 5 * RATE


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
    def __init__(self, service, language, script, emit):
        self.service = service
        self.language = language
        self.script = script
        self.emit = emit
        self.offset = 0  # PCM sample offset; advances only after successful inference.
        self.confirmed = ""
        self.draft = ""
        self.draft_end = 0

    @property
    def text(self):
        return self.confirmed + self.draft

    def recognize(self, pcm):
        return self.service.transcribe(
            wav_bytes(pcm), self.language, self.script, fast=True)["text"].strip()

    def update(self, audio, final=False):
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (RATE, 1, 2):
                raise ValueError("Live dictation needs 16 kHz mono PCM.")
            pcm = wav.readframes(wav.getnframes())
        total = len(pcm) // 2
        while self.offset < total:
            remaining = pcm[self.offset * 2:]
            boundary = pause_boundary(remaining)
            if boundary is not None:
                # A complete window is finalized once, even if capture runs ahead.
                text = self.recognize(remaining[:boundary * 2])
                if not text and self.draft:
                    # Do not erase an already displayed draft on an empty decode.
                    text = self.draft
                self.confirmed += text
                self.offset += boundary
                self.draft = ""
                self.draft_end = self.offset
                self.emit(self.text)
                continue
            if total == self.draft_end:
                # Stop after the latest preview: no second identical inference.
                return self.text
            if not final and total - self.offset < MIN_PREVIEW:
                return self.text
            text = self.recognize(remaining)
            # Keep a nonempty draft if a later decode unexpectedly returns empty.
            if text:
                self.draft = text
            self.draft_end = total
            self.emit(self.text)
            break
        return self.text
