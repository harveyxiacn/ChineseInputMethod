"""Dependency-free localhost HTTP service. Run with python -m ime.server."""
from __future__ import annotations

import argparse
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .pinyin import PinyinEngine
from .speech import SpeechError, SpeechService
from .settings import SettingsStore
from .web_sessions import LiveSessions
from .assistant import LocalAssistant, AssistantError

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_UPLOAD = 16 * 1024 * 1024


def parse_upload(content_type: str, body: bytes) -> tuple[bytes, str, str]:
    if not content_type.lower().startswith("multipart/form-data;"):
        raise ValueError("Upload audio as multipart/form-data.")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    if not message.is_multipart() or message.defects:
        raise ValueError("Malformed audio upload.")
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name not in {"file", "language", "script"} or name in fields:
            raise ValueError("Unexpected or duplicate upload field.")
        fields[name] = part.get_payload(decode=True) or b""
    if not fields.get("file"):
        raise ValueError("Choose a nonempty audio file.")
    language = fields.get("language", b"auto").decode("utf-8")
    script = fields.get("script", b"simplified").decode("utf-8")
    if language not in {"auto", "zh", "yue", "en"}:
        raise ValueError("Language must be auto, zh, yue, or en.")
    if script not in {"simplified", "traditional", "original"}:
        raise ValueError("Script must be simplified, traditional, or original.")
    return fields["file"], language, script


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, pinyin=None, speech=None, settings=None):
        self.settings = settings or SettingsStore()
        self.pinyin = pinyin or PinyinEngine(fuzzy_pairs=self.settings.get("fuzzy_pairs"))
        self.speech = speech or SpeechService(settings=self.settings, lexicon=getattr(self.pinyin, "lexicon", None))
        self.live = LiveSessions(self.speech)
        self.assistant = LocalAssistant(self.settings)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = f"Shuangsheng/{__version__}"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_request(self, code="-", size="-"):
        # Query strings can contain typed input or document context.
        self.log_message('"%s %s" %s %s', self.command, urlsplit(self.path).path, str(code), str(size))

    def respond(self, status, payload, content_type="application/json; charset=utf-8"):
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Permissions-Policy", "microphone=(self)")
        self.end_headers()
        self.wfile.write(payload)

    def error(self, status, detail):
        self.respond(status, {"error": detail, "detail": detail})

    def trusted_request(self):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in allowed or (origin and origin != f"http://{host}"):
            self.close_connection = True
            self.error(403, "This service accepts same-origin localhost requests only.")
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        url = urlsplit(self.path)
        if url.path == "/api/health":
            try:
                self.respond(200, {"speech": self.server.speech.status(),
                                   "dictionary": getattr(self.server.pinyin, "info", {"source": "bundled"})})
            except SpeechError as exc:
                self.error(exc.status_code, str(exc))
        elif url.path == "/api/settings":
            self.respond(200, self.server.settings.snapshot())
        elif url.path == "/api/lexicon":
            self.respond(200, {"entries": self.server.pinyin.lexicon.entries()})
        elif url.path == "/api/predict":
            params = parse_qs(url.query, keep_blank_values=True)
            try:
                self.respond(200, {"candidates": self.server.pinyin.predict(
                    params.get("context", [""])[0][-512:], script=params.get("script", ["simplified"])[0])})
            except ValueError as exc:
                self.error(400, str(exc))
            except RuntimeError as exc:
                self.error(503, str(exc))
        elif url.path == "/api/candidates":
            params = parse_qs(url.query, keep_blank_values=True)
            query = params.get("q", [""])[0]
            script = params.get("script", ["simplified"])[0]
            if len(query) > 128 or script not in {"simplified", "traditional"}:
                self.error(400, "Use at most 128 pinyin characters and a valid script.")
                return
            try:
                options = {"context": params.get("context", [""])[0][-512:],
                           "scheme": params.get("scheme", ["pinyin"])[0],
                           "fuzzy": params.get("fuzzy", ["false"])[0] == "true"}
                if any(len(values) != 1 for values in params.values()):
                    raise ValueError("Candidate parameters must not be repeated")
                if params.get("fuzzy", ["false"])[0] not in {"true", "false"}:
                    raise ValueError("fuzzy must be true or false")
                count_text = params.get("limit", ["9"])[0]
                if not re.fullmatch(r"[0-9]{1,2}", count_text) or not 1 <= int(count_text) <= 50:
                    raise ValueError("limit must be an ASCII integer between 1 and 50")
                count = int(count_text)
                self.server.pinyin.fuzzy_pairs = self.server.settings.get("fuzzy_pairs")
                self.respond(200, {"candidates": self.server.pinyin.candidates(query, limit=count, script=script, **options)})
            except ValueError as exc:
                self.error(400, str(exc))
            except RuntimeError as exc:
                self.error(503, str(exc))
        else:
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/index.html": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/capture.js": ("capture.js", "text/javascript; charset=utf-8"),
                     "/styles.css": ("styles.css", "text/css; charset=utf-8")}
            if url.path not in files:
                self.error(404, "Not found.")
                return
            filename, mime = files[url.path]
            self.respond(200, (STATIC / filename).read_bytes(), mime)

    def do_POST(self):
        if not self.trusted_request():
            return
        if self.path.startswith("/api/") and self.path != "/api/transcribe":
            self.json_action()
            return
        if self.path != "/api/transcribe":
            self.close_connection = True
            self.error(404, "Not found.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_UPLOAD:
            self.close_connection = True
            self.error(413, "Audio upload must be between 1 byte and 16 MiB.")
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Incomplete upload.")
            audio, language, script = parse_upload(self.headers.get("Content-Type", ""), body)
            result = self.server.speech.transcribe(audio, language=language, script=script)
            self.respond(200, result)
        except SpeechError as exc:
            self.error(exc.status_code, str(exc))
        except (ValueError, UnicodeError) as exc:
            self.error(400, str(exc))
        except TimeoutError:
            self.close_connection = True
            self.error(408, "Audio upload timed out.")
        except Exception:
            self.error(500, "Transcription failed. Check the server configuration and try again.")

    def json_action(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 5 * 1024 * 1024 if self.path == "/api/live/chunk" else 128 * 1024
            if self.headers.get("Transfer-Encoding") or not 0 < length <= limit:
                self.close_connection = True
                self.error(413, "Request exceeds its size limit.")
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("Send application/json.")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Send a JSON object.")
            engine = self.server.pinyin
            if self.path == "/api/settings":
                result = self.server.settings.update(body)
                self.server.pinyin.fuzzy_pairs = result["fuzzy_pairs"]
            elif self.path == "/api/commit":
                if set(body) - {"query", "text", "context"}:
                    raise ValueError("Unexpected commit field")
                for key in ("query", "text", "context"):
                    if not isinstance(body.get(key, ""), str) or len(body.get(key, "")) > 4096:
                        raise ValueError("Invalid commit text")
                engine.learn(body.get("query", ""), body.get("text", ""), body.get("context", "")[-512:])
                result = {"saved": True}
            elif self.path == "/api/lexicon":
                action = body.pop("action", "upsert")
                if action == "delete":
                    if set(body) != {"id"} or not isinstance(body["id"], str):
                        raise ValueError("Term deletion needs only a string id")
                    result = {"deleted": engine.lexicon.delete(body.get("id"))}
                elif action == "upsert":
                    if set(body) - {"id", "text", "pinyin", "shortcut", "pinned"}:
                        raise ValueError("Unexpected term field")
                    result = engine.lexicon.upsert(**body)
                else:
                    raise ValueError("Unknown vocabulary action")
            elif self.path == "/api/live/start":
                if set(body) - {"language", "script", "hotwords", "initial_prompt"}:
                    raise ValueError("Unexpected recording start field")
                # The service selects hints according to its configured backend;
                # SenseVoice cannot accept implicit Whisper vocabulary prompts.
                result = self.server.live.start(body.get("language", "auto"), body.get("script", "simplified"),
                    body.get("hotwords"), body.get("initial_prompt"))
            elif self.path == "/api/live/chunk":
                if set(body) - {"id", "sequence", "pcm", "final"}:
                    raise ValueError("Unexpected recording chunk field")
                result = self.server.live.feed(body.get("id"), body.get("sequence"), body.get("pcm"), body.get("final", False))
            elif self.path == "/api/live/cancel":
                if set(body) != {"id"}:
                    raise ValueError("Recording cancellation needs only a session id")
                result = self.server.live.cancel(body.get("id"))
            elif self.path in {"/api/speech/warmup", "/api/speech/unload"}:
                if body:
                    raise ValueError("Speech lifecycle operations accept an empty object")
                getattr(self.server.speech, self.path.rsplit("/", 1)[-1])()
                result = self.server.speech.status()
            elif self.path == "/api/assist":
                if set(body) - {"text", "action", "language"}:
                    raise ValueError("Unexpected assistant field")
                result = {"text": self.server.assistant.transform(body.get("text"), body.get("action", "polish"), body.get("language", "中文"))}
            else:
                self.error(404, "Not found.")
                return
            self.respond(200, result)
        except SpeechError as exc:
            self.error(exc.status_code, str(exc))
        except (ValueError, TypeError, KeyError, UnicodeError, AssistantError) as exc:
            self.error(400, str(exc))
        except TimeoutError:
            self.close_connection = True
            self.error(408, "Request timed out.")
        except Exception:
            self.error(500, "Local operation failed. Your text has not been changed.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    with Server(("127.0.0.1", args.port)) as server:
        print(f"Shuangsheng is ready at http://localhost:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
