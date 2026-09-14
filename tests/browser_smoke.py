"""Optional: .venv/bin/python tests/browser_smoke.py (pip install playwright).

Uses installed Chromium and a real localhost server. Speech inference is stubbed
to test the upload UI; this is not a speech accuracy test.
"""
import json
from pathlib import Path
import shutil
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ime.server import Server
from playwright.sync_api import sync_playwright, expect


class Speech:
    def status(self):
        return {"available": True, "loaded": False, "model": "test", "detail": "Test speech adapter."}

    def transcribe(self, audio, language, script):
        assert language == "yue", language
        assert script == "traditional", script
        return {"text": "廣東話", "language": "yue"}


def main():
    server = Server(("127.0.0.1", 0), speech=Speech())
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which("chromium"), headless=True)
            page = browser.new_page(viewport={"width": 1360, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.locator("#pinyin").fill("nihao")
            expect(page.locator('.candidate').first).to_contain_text('你好')
            page.locator("#pinyin").press("Space")
            assert page.locator("#document").input_value() == "你好"
            page.locator("#script").select_option("traditional")
            page.locator("#document").evaluate("e => { e.focus(); e.setSelectionRange(0,0); e.dispatchEvent(new Event('select')); }")
            page.locator("#pinyin").fill("zhongguo")
            expect(page.locator('.candidate').first).to_contain_text('中國')
            page.locator("#pinyin").press("1")
            assert page.locator("#document").input_value() == "中國你好"
            page.locator("#pinyin").fill("hello")
            page.locator("#pinyin").press("Enter")
            assert page.locator("#document").input_value() == "中國hello你好"
            page.locator("#language").select_option("yue")
            page.locator("#audio-file").set_input_files({"name": "sample.wav", "mimeType": "audio/wav", "buffer": b"test audio"})
            expect(page.locator('#status')).to_contain_text('Transcription added')
            assert '廣東話' in page.locator('#document').input_value()
            assert "Transcription added" in page.locator("#status").inner_text()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator("#pinyin").fill("nihao")
            page.locator("#pinyin").press("Escape")
            assert page.locator("#pinyin").input_value() == ""
            Path(".cache").mkdir(exist_ok=True)
            page.screenshot(path=".cache/mobile.png", full_page=True)
            page.set_viewport_size({"width": 1360, "height": 1000})
            page.screenshot(path=".cache/desktop.png", full_page=True)
            assert not errors, json.dumps(errors)
            browser.close()
            print("Browser smoke passed: pinyin, script, caret, raw input, voice upload, mobile layout, no JS errors.")
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == "__main__":
    main()
