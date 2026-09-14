import http.client
import json
import threading
import unittest

from ime.server import Server, parse_upload
from ime.speech import SpeechError


def multipart(audio=b"audio", language="yue", script="traditional"):
    body = b""
    for name, value in [("language", language.encode()), ("script", script.encode()), ("file", audio)]:
        body += b"--test-boundary\r\nContent-Disposition: form-data; name=\"" + name.encode() + b"\"\r\n\r\n" + value + b"\r\n"
    return body + b"--test-boundary--\r\n"


class FakeSpeech:
    def status(self):
        return {"available": True, "model": "test", "detail": "ready"}

    def transcribe(self, audio, language, script):
        if audio == b"bad":
            raise SpeechError("Invalid audio", 400)
        return {"text": "你好", "language": language, "script": script}


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(("127.0.0.1", 0), speech=FakeSpeech())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_candidates_and_script(self):
        status, data = self.request("GET", "/api/candidates?q=zhongguo&script=traditional")
        self.assertEqual(status, 200)
        self.assertIn("中國", [c["text"] for c in json.loads(data)["candidates"]])

    def test_static_and_health(self):
        for path in ["/", "/app.js", "/styles.css", "/api/health"]:
            self.assertEqual(self.request("GET", path)[0], 200)

    def test_cannot_read_arbitrary_files(self):
        self.assertEqual(self.request("GET", "/../requirements.txt")[0], 404)

    def test_rejects_cross_origin_and_host(self):
        self.assertEqual(self.request("GET", "/api/health", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/transcribe", multipart(), {"Origin": "https://attacker.example"})[0], 403)

    def test_upload_forwards_language_and_script(self):
        status, data = self.request("POST", "/api/transcribe", multipart(), {"Content-Type": "multipart/form-data; boundary=test-boundary"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"text": "你好", "language": "yue", "script": "traditional"})

    def test_error_codes(self):
        headers = {"Content-Type": "multipart/form-data; boundary=test-boundary"}
        self.assertEqual(self.request("POST", "/api/transcribe", multipart(language="invalid"), headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/transcribe", multipart(audio=b"bad"), headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/transcribe", b"", headers)[0], 413)
        self.assertEqual(self.request("GET", "/api/candidates?q=" + "a" * 129)[0], 400)


class MultipartTests(unittest.TestCase):
    def test_binary_audio_preserved(self):
        audio = bytes(range(256)) * 5
        self.assertEqual(parse_upload("multipart/form-data; boundary=test-boundary", multipart(audio))[0], audio)

    def test_rejects_malformed(self):
        with self.assertRaises(ValueError):
            parse_upload("multipart/form-data; boundary=test-boundary", b"garbage")


if __name__ == "__main__":
    unittest.main()
