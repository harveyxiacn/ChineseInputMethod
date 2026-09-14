"""Dependency-free localhost HTTP service. Run with python -m ime.server."""
from __future__ import annotations

import argparse
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .pinyin import PinyinEngine
from .speech import SpeechError, SpeechService

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
    if language not in {"auto", "zh", "yue"}:
        raise ValueError("Language must be auto, zh, or yue.")
    if script not in {"simplified", "traditional"}:
        raise ValueError("Script must be simplified or traditional.")
    return fields["file"], language, script


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, pinyin=None, speech=None):
        self.pinyin = pinyin or PinyinEngine()
        self.speech = speech or SpeechService()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "Shuangsheng/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

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
            self.respond(200, {"speech": self.server.speech.status(),
                               "dictionary": getattr(self.server.pinyin, "info", {"source": "bundled"})})
        elif url.path == "/api/candidates":
            params = parse_qs(url.query)
            query = params.get("q", [""])[0]
            script = params.get("script", ["simplified"])[0]
            if len(query) > 128 or script not in {"simplified", "traditional"}:
                self.error(400, "Use at most 128 pinyin characters and a valid script.")
                return
            try:
                self.respond(200, {"candidates": self.server.pinyin.candidates(query, script=script)})
            except ValueError as exc:
                self.error(400, str(exc))
            except RuntimeError as exc:
                self.error(503, str(exc))
        else:
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/index.html": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/styles.css": ("styles.css", "text/css; charset=utf-8")}
            if url.path not in files:
                self.error(404, "Not found.")
                return
            filename, mime = files[url.path]
            self.respond(200, (STATIC / filename).read_bytes(), mime)

    def do_POST(self):
        if not self.trusted_request():
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with Server(("127.0.0.1", args.port)) as server:
        print(f"Shuangsheng is ready at http://localhost:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
