"""Real Chromium + local HTTP integration; audio/model inference is stubbed.

Run .venv/bin/python tests/browser_smoke.py with socket/display permissions.
Fixtures use temporary settings/lexicon. No real microphone, model, external
application insertion, global keyboard hooks or user preference files are used.
"""
import base64
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import wave
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ime.server import Server
from ime.settings import SettingsStore
from ime.lexicon import LexiconStore
from ime.pinyin import PinyinEngine
from playwright.sync_api import sync_playwright, expect


class Speech:
    def __init__(self):
        self.calls = []
        self.loaded = False
        self.delay = 0

    def status(self):
        return {"available": True, "cached": True, "loaded": self.loaded, "model": "test", "device": "CPU", "backend": "faster-whisper", "detail": "Test adapter."}

    def warmup(self):
        self.loaded = True

    def unload(self):
        self.loaded = False

    def transcribe(self, audio, language, script, *, fast=False, hotwords=None, initial_prompt=None):
        assert language in {"zh", "yue", "auto", "en"}, language
        assert script in {"simplified", "traditional"}, script
        frames = 0
        if audio[:4] == b"RIFF":
            with wave.open(io.BytesIO(audio), "rb") as wav:
                assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
                frames = wav.getnframes()
                assert frames > 0
        self.calls.append({"fast": fast, "frames": frames, "language": language, "script": script, "hotwords": hotwords})
        time.sleep(self.delay)
        return {"text": "廣東話", "language": language}


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="ime-browser-") as directory:
        prefs = SettingsStore(Path(directory) / "settings.json")
        lexicon = LexiconStore(Path(directory) / "lexicon.json")
        engine = PinyinEngine(lexicon=lexicon, fuzzy_pairs=prefs.get("fuzzy_pairs"))
        speech = Speech()
        server = Server(("127.0.0.1", 0), speech=speech, pinyin=engine, settings=prefs)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=shutil.which("chromium"), headless=True,
                    args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
                page = browser.new_page(viewport={"width": 1360, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.wait_for_function("() => window.__shuangshengReady")
                editor, query = page.locator("#document"), page.locator("#pinyin")
                def at_end():
                    editor.evaluate("e => { e.focus(); e.setSelectionRange(e.value.length, e.value.length); e.dispatchEvent(new Event('select')); }")
                def clear():
                    editor.fill("")
                    at_end()
                    query.fill("")
                def done(name):
                    passed.append(name)
                    print(f"PASS {len(passed)}: {name}")
                def wait_idle():
                    expect(page.locator("#record-label")).to_have_text("开始录音", timeout=15000)
                    expect(page.locator("#record")).to_be_enabled()
                def finish():
                    with page.expect_response(lambda response: "/api/live/chunk" in response.url and response.request.post_data_json.get("final") is True, timeout=15000) as result:
                        page.locator("#record").click()
                    data = result.value.json()
                    wait_idle()
                    return data

                # Fast input shortcuts must select the latest query before debounce finishes.
                query.fill("nihao")
                query.press("Space")
                expect(editor).to_have_value("你好")
                page.locator("#script").select_option("traditional")
                editor.evaluate("e => { e.focus(); e.setSelectionRange(0,0); e.dispatchEvent(new Event('select')); }")
                query.fill("zhongguo")
                query.press("1")
                expect(editor).to_have_value("中國你好")
                query.fill("raw")
                query.press("Enter")
                expect(editor).to_have_value("中國raw你好")
                done("latest query Space / digits, caret insertion, Enter raw, simplified / traditional")

                clear()
                for text in ["Python3.12", "name@example.com", "v1.2.3"]:
                    query.press_sequentially(text, delay=8)
                    query.press("Enter")
                expect(editor).to_have_value("Python3.12name@example.comv1.2.3")
                query.fill("hello")
                query.press("Space")
                expect(editor).to_have_value("Python3.12name@example.comv1.2.3hello ")
                page.locator("#input-mode").click()
                expect(page.locator("#input-mode")).to_have_text("EN · 英文")
                query.press_sequentially("Hello world 2!", delay=5)
                query.press("Enter")
                expect(editor).to_have_value("Python3.12name@example.comv1.2.3hello Hello world 2!")
                page.locator("#input-mode").click()
                expect(page.locator("#input-mode")).to_have_text("中 · 中文")
                done("mixed Python versions, email, symbols, raw English spaces and mode toggle")

                clear()
                query.fill("nihao")
                expect(page.locator(".candidate").first).to_contain_text("你好")
                query.press(",")
                query.press("?")
                expect(editor).to_have_value("你好，？")
                page.locator("#clear").click()
                expect(editor).to_have_value("")
                page.locator("#undo").click()
                expect(editor).to_have_value("你好，？")
                done("Chinese punctuation and reversible clear")

                clear()
                page.locator("#scheme").select_option("jyutping")
                query.press_sequentially("nei5", delay=20)
                expect(query).to_have_value("nei5")
                expect(page.locator(".candidate").first).to_contain_text("你")
                query.press("Alt+1")
                expect(editor).to_have_value("你")
                page.locator("#scheme").select_option("pinyin")
                done("real Jyutping tone digit preserved; Alt+digit selects")

                clear()
                delayed = []
                def stale_route(route):
                    q = parse_qs(urlsplit(route.request.url).query).get("q", [""])[0]
                    if q == "nihao":
                        delayed.append(route)
                    else:
                        route.continue_()
                page.route("**/api/candidates?*", stale_route)
                with page.expect_request("**/api/candidates?*q=nihao*"):
                    query.fill("nihao")
                query.fill("zhongguo")
                expect(page.locator(".candidate").first).to_contain_text("中國")
                assert delayed
                delayed[0].fulfill(status=200, json={"candidates": [{"text": "过时结果", "pinyin": "ni hao"}]})
                expect(page.locator(".candidate").first).to_contain_text("中國")
                page.unroute("**/api/candidates?*", stale_route)
                query.press("Escape")
                expect(query).to_have_value("")
                done("async stale candidate response rejected; composition Escape clears")

                # Editor drafts must never become persisted context for selection learning.
                editor.fill("私密未提交草稿")
                at_end()
                with page.expect_request("**/api/commit") as committed:
                    query.fill("nihao")
                    query.press("Space")
                assert committed.value.post_data_json["context"] == ""
                done("learning persists committed choices without arbitrary draft context")

                page.locator("#settings-open").click()
                page.locator('[name="theme"]').select_option("dark")
                page.locator('[name="font_size"]').fill("22")
                page.locator('[name="candidate_count"]').fill("5")
                page.locator('[name="fuzzy"]').check()
                page.locator('[name="fuzzy_pairs"]').fill("zh:z, n:l")
                page.locator('[name="save_draft"]').check()
                page.locator('#settings-form button[type="submit"]').click()
                expect(page.locator("#settings-dialog")).not_to_be_visible()
                expect(page.locator("html")).to_have_class("dark")
                assert prefs.get("font_size") == 22 and prefs.get("candidate_count") == 5
                assert prefs.get("fuzzy_pairs") == [["zh", "z"], ["n", "l"]]
                draft = editor.input_value()
                page.reload()
                page.wait_for_function("() => window.__shuangshengReady")
                expect(editor).to_have_value(draft)
                assert page.evaluate("getComputedStyle(document.getElementById('document')).fontSize") == "22px"
                page.locator("#settings-open").click()
                page.locator('[name="save_draft"]').uncheck()
                page.locator('#settings-form button[type="submit"]').click()
                expect(page.locator("#settings-dialog")).not_to_be_visible()
                assert page.evaluate("localStorage.getItem('shuangsheng-draft')") is None
                done("settings persist across reload; drafts restore only with opt-in and delete on disable")

                page.locator("#settings-open").click()
                page.keyboard.press("Escape")
                expect(page.locator("#settings-dialog")).not_to_be_visible()
                page.locator("#lexicon-open").click()
                page.locator('#term-form [name="text"]').fill("项目联络\n签名")
                page.locator('#term-form [name="pinyin"]').fill("xiang mu")
                page.locator('#term-form [name="shortcut"]').fill("/sig")
                page.locator('#term-form [name="pinned"]').check()
                page.locator('#term-form button[type="submit"]').click()
                expect(page.locator("#terms")).to_contain_text("项目联络")
                assert lexicon.entries()[0]["pinned"]
                page.keyboard.press("Escape")
                expect(page.locator("#lexicon-dialog")).not_to_be_visible()
                done("native modal Escape and persistent pinned shortcut templates")

                page.locator("#language").select_option("yue")
                clear()
                page.locator("#audio-file").set_input_files({"name": "sample.wav", "mimeType": "audio/wav", "buffer": b"test audio"})
                expect(page.locator("#status")).to_contain_text("识别文字已插入")
                expect(editor).to_have_value("廣東話")
                assert speech.calls[-1]["language"] == "yue" and speech.calls[-1]["script"] == "traditional"
                done("upload transcription forwards language / script and inserts once")

                # Collect real incremental audio requests and final response text.
                chunks = []
                def observe_chunks(route):
                    request = route.request.post_data_json
                    chunks.append(dict(request))
                    route.continue_()
                page.route("**/api/live/chunk", observe_chunks)
                before = editor.input_value()
                at_end()
                page.locator("#record").click()
                expect(page.locator("#record-label")).to_have_text("停止并上屏")
                expect(page.locator("#live-transcript")).to_contain_text("廣東話", timeout=15000)
                expect(editor).to_have_value(before)
                result = finish()
                expect(editor).to_have_value(before + result["text"])
                assert chunks[0]["sequence"] == 0 and not chunks[0]["final"]
                assert [row["sequence"] for row in chunks] == list(range(len(chunks)))
                assert chunks[-1]["final"] and len(chunks) >= 2
                assert all(len(base64.b64decode(row["pcm"])) % 2 == 0 for row in chunks)
                assert speech.calls[-1]["fast"]
                page.unroute("**/api/live/chunk", observe_chunks)
                done("real incremental PCM live API, transient preview, final commit exactly once")

                pending_preview = []
                def hold_preview(route):
                    if not route.request.post_data_json["final"] and not pending_preview:
                        pending_preview.append(route)
                    else:
                        route.continue_()
                page.route("**/api/live/chunk", hold_preview)
                before = editor.input_value()
                at_end()
                with page.expect_request("**/api/live/chunk", timeout=15000):
                    page.locator("#record").click()
                page.locator("#record").click()
                expect(page.locator("#record-label")).to_have_text("正在收尾…")
                expect(editor).to_have_value(before)
                assert pending_preview
                with page.expect_response(lambda response: "/api/live/chunk" in response.url and response.request.post_data_json.get("final") is True, timeout=15000) as final_response:
                    response = pending_preview[0].fetch()
                    pending_preview[0].fulfill(response=response)
                result = final_response.value.json()
                wait_idle()
                expect(editor).to_have_value(before + result["text"])
                page.unroute("**/api/live/chunk", hold_preview)
                done("stop while live preview is in flight serializes final request and commits once")

                before = editor.input_value()
                at_end()
                page.locator("#record").click()
                expect(page.locator("#record-label")).to_have_text("停止并上屏")
                # An edit followed by an undo still invalidates the original insertion target.
                editor.fill(before + "changed")
                editor.fill(before)
                finish()
                expect(editor).to_have_value(before)
                expect(page.locator("#live-transcript")).to_contain_text("廣東話")
                expect(page.locator("#status")).to_contain_text("文本已修改")
                done("recording result remains preview when editor changes, even if text reverts")

                before = editor.input_value()
                page.locator("#record").click()
                expect(page.locator("#record-label")).to_have_text("停止并上屏")
                page.locator("#cancel-record").click()
                wait_idle()
                expect(editor).to_have_value(before)
                expect(page.locator("#live-transcript")).to_have_text("")
                done("recording cancellation discards partial/final text and releases microphone")

                # Lose the first accepted preview response; transport retry must reuse its bytes/sequence.
                requests = []
                def retry_route(route):
                    body = route.request.post_data_json
                    requests.append(dict(body))
                    if len(requests) == 1:
                        response = route.fetch()
                        assert response.ok
                        route.abort("failed")
                    else:
                        route.continue_()
                page.route("**/api/live/chunk", retry_route)
                before = editor.input_value()
                at_end()
                page.locator("#record").click()
                expect(page.locator("#live-transcript")).to_contain_text("廣東話", timeout=20000)
                result = finish()
                expect(editor).to_have_value(before + result["text"])
                assert requests[0] == requests[1], "Retry changed accepted PCM request"
                assert [row["sequence"] for row in requests[2:]] == list(range(1, len(requests) - 1))
                assert requests[-1]["final"]
                page.unroute("**/api/live/chunk", retry_route)
                done("accepted chunk response loss retries identical sequence/PCM without duplicate commit")

                page.locator("#settings-open").click()
                page.locator('[name="push_to_talk"]').check()
                page.locator('#settings-form button[type="submit"]').click()
                expect(page.locator("#record-label")).to_have_text("按住说话")
                before = editor.input_value()
                at_end()
                page.locator("#record").hover()
                page.mouse.down()
                expect(page.locator("#record-label")).to_have_text("松开结束")
                page.wait_for_function("() => document.getElementById('level').value > 0", timeout=10000)
                with page.expect_response(lambda response: "/api/live/chunk" in response.url and response.request.post_data_json.get("final") is True, timeout=15000) as held_result:
                    page.mouse.up()
                data = held_result.value.json()
                expect(page.locator("#record-label")).to_have_text("按住说话")
                expect(editor).to_have_value(before + data["text"])
                page.locator("#settings-open").click()
                page.locator('[name="push_to_talk"]').uncheck()
                page.locator('#settings-form button[type="submit"]').click()
                wait_idle()
                done("hold-to-talk uses pointer capture and ends on release with real PCM meter")

                # Escape closes a modal first while recording continues, then cancels recording.
                page.locator("#record").click()
                expect(page.locator("#record-label")).to_have_text("停止并上屏")
                page.locator("#settings-open").click()
                page.keyboard.press("Escape")
                expect(page.locator("#settings-dialog")).not_to_be_visible()
                expect(page.locator("#record-label")).to_have_text("停止并上屏")
                page.keyboard.press("Escape")
                wait_idle()
                done("modal Escape takes precedence over background dictation cancellation")

                page.locator("#warmup").click()
                expect(page.locator("#speech-status")).to_contain_text("已加载")
                page.locator("#unload").click()
                expect(page.locator("#speech-status")).to_contain_text("已缓存")
                done("model warmup/unload status controls")

                class Assistant:
                    def transform(self, text, action="polish", language="English"):
                        assert text and action in {"polish", "translate", "complete"}
                        return "建议文字"
                server.assistant = Assistant()
                page.locator('[data-assist="polish"]').click()
                before = editor.input_value()
                page.locator("#assistant-run").click()
                expect(page.locator("#assistant-result")).to_have_value("建议文字")
                expect(editor).to_have_value(before)
                page.locator("#assistant-accept").click()
                expect(editor).to_have_value("建议文字")
                page.locator("#undo").click()
                expect(editor).to_have_value(before)
                done("assistant API preview requires explicit acceptance; undo restores original")

                # Closing a running assistant preview ignores its late response.
                pending_assistant = []
                page.route("**/api/assist", lambda route: pending_assistant.append(route))
                page.locator('[data-assist="polish"]').click()
                with page.expect_request("**/api/assist"):
                    page.locator("#assistant-run").click()
                page.keyboard.press("Escape")
                expect(page.locator("#assistant-dialog")).not_to_be_visible()
                pending_assistant[0].fulfill(status=200, json={"text": "不得自动替换"})
                page.locator('[data-assist="polish"]').click()
                expect(page.locator("#assistant-result")).to_have_value("")
                expect(page.locator("#assistant-accept")).to_be_disabled()
                page.keyboard.press("Escape")
                page.unroute("**/api/assist")
                done("late assistant preview response cannot apply after Escape")

                editor.fill("今天的會議重點：保留人名、數字和否定表達。\n\n項目版本 Python3.12，郵箱 team@example.com。")
                at_end()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
                query.fill("nihao")
                expect(page.locator(".candidate").first).to_contain_text("你好")
                Path(".cache").mkdir(exist_ok=True)
                page.screenshot(path=".cache/mobile.png", full_page=True)
                page.set_viewport_size({"width": 1360, "height": 1000})
                page.screenshot(path=".cache/desktop.png", full_page=True)
                assert not errors, json.dumps(errors)
                done("390px mobile layout and desktop screenshots, no JavaScript page errors")
                browser.close()
                print(f"Browser smoke passed: {len(passed)} scenarios; screenshots .cache/desktop.png and .cache/mobile.png")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=10)


if __name__ == "__main__":
    main()
