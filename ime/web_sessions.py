"""Bounded incremental browser dictation sessions; memory only."""
import base64
import hashlib
import math
import re
import secrets
import threading
import time

from .live_speech import LiveTranscript, RATE, wav_bytes
from .speech import SpeechError, SpeechService


class LiveSessions:
    def __init__(self, speech, *, ttl=180, limit=4):
        if type(limit) is not int or not 1 <= limit <= 4 or type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("Live sessions need a positive finite TTL and a limit between 1 and 4")
        self.speech = speech
        self.ttl = ttl
        self.limit = limit
        self.sessions = {}
        self.lock = threading.Lock()

    @staticmethod
    def _token(token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
            raise ValueError("Invalid recording session id")

    def _prune(self, now):
        self.sessions = {key: item for key, item in self.sessions.items()
                         if now - item["used"] < self.ttl or item["lock"].locked()}
        finished = sorted((key for key, item in self.sessions.items() if item["finished"]),
                          key=lambda key: self.sessions[key]["used"], reverse=True)
        for key in finished[self.limit:]:
            if not self.sessions[key]["lock"].locked():
                self.sessions.pop(key)

    def start(self, language, script, hotwords=None, initial_prompt=None):
        if not isinstance(language, str) or language not in {"zh", "yue", "en", "auto"} or not isinstance(script, str) or script not in {"simplified", "traditional", "original"}:
            raise ValueError("Invalid language or script")
        hotwords = SpeechService._hint("hotwords", hotwords)
        initial_prompt = SpeechService._hint("initial_prompt", initial_prompt)
        with self.lock:
            now = time.monotonic()
            self._prune(now)
            if sum(not item["finished"] for item in self.sessions.values()) >= self.limit:
                raise SpeechError("Too many recording sessions. Cancel another recording first.", 429)
            token = secrets.token_urlsafe(24)
            self.sessions[token] = {"live": LiveTranscript(self.speech, language, script, lambda _: None,
                hotwords=hotwords, initial_prompt=initial_prompt), "pcm": bytearray(), "sequence": 0, "used": now,
                "lock": threading.Lock(), "last": None, "fingerprint": None, "finished": False, "cancelled": False}
            return {"id": token, "sample_rate": RATE, "max_seconds": 115}

    def cancel(self, token):
        self._token(token)
        with self.lock:
            session = self.sessions.pop(token, None)
            if session:
                session["cancelled"] = True
        return {"cancelled": True}

    def feed(self, token, sequence, encoded, final=False):
        self._token(token)
        if type(sequence) is not int or sequence < 0 or type(final) is not bool:
            raise ValueError("Invalid recording sequence")
        if not isinstance(encoded, str) or len(encoded) > 5 * 1024 * 1024:
            raise ValueError("Invalid recording chunk")
        pcm = base64.b64decode(encoded, validate=True)
        if len(pcm) % 2:
            raise ValueError("Recording chunks must contain PCM16 samples")
        fingerprint = (hashlib.sha256(pcm).digest(), len(pcm), final)
        with self.lock:
            session = self.sessions.get(token)
            if not session:
                raise SpeechError("Recording session expired. Start another recording.", 410)
            if time.monotonic() - session["used"] >= self.ttl and not session["lock"].locked():
                self.sessions.pop(token)
                raise SpeechError("Recording session expired. Start another recording.", 410)
            if not session["lock"].acquire(blocking=False):
                raise SpeechError("Recording chunk is still being processed.", 429)
        try:
            session["used"] = time.monotonic()
            if session["cancelled"]:
                raise SpeechError("Recording was cancelled.", 410)
            if sequence == session["sequence"] - 1 and session["last"] is not None:
                with self.lock:
                    if session["cancelled"] or self.sessions.get(token) is not session:
                        raise SpeechError("Recording was cancelled.", 410)
                    if fingerprint != session["fingerprint"]:
                        raise ValueError("A repeated sequence must contain the same PCM and final flag")
                    return dict(session["last"])
            if sequence != session["sequence"] or session["finished"]:
                raise ValueError("Out-of-order recording chunk")
            if len(session["pcm"]) + len(pcm) > 115 * RATE * 2:
                raise SpeechError("Recording exceeds 115 seconds.", 413)
            # Accept each audio chunk once, even when inference fails. Return its sequence
            # so retries never append duplicate audio; the next chunk retries decoding.
            session["pcm"].extend(pcm)
            session["sequence"] += 1
            error = None
            try:
                session["live"].update(wav_bytes(bytes(session["pcm"])), final=final)
            except Exception as exc:
                error = str(exc)
            if session["cancelled"]:
                raise SpeechError("Recording was cancelled.", 410)
            result = {**session["live"].snapshot, "sequence": sequence, "final": final}
            if final and not result["text"] and not error:
                error = "No speech recognized. Check the microphone and try again."
            if error:
                result["warning"] = error
                result["incomplete"] = True
            # Publish atomically with cancellation: a cancelled in-flight decode
            # never gets a successful result or a cached final acknowledgement.
            with self.lock:
                if session["cancelled"] or self.sessions.get(token) is not session:
                    raise SpeechError("Recording was cancelled.", 410)
                session["last"] = result
                session["fingerprint"] = fingerprint
                session["finished"] = final
                if final:
                    session["pcm"].clear()
                self._prune(time.monotonic())
                return dict(result)
        finally:
            session["used"] = time.monotonic()
            session["lock"].release()
