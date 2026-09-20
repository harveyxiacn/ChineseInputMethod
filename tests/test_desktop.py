import os
import unittest
from unittest.mock import patch

from ime.desktop import DesktopApp, main


class DesktopSmokeTests(unittest.TestCase):
    def test_smoke_without_window_microphone_or_network(self):
        with patch("builtins.print") as output, patch.dict(os.environ, {"DISPLAY": ""}):
            self.assertEqual(main(["--smoke-test"]), 0)
        self.assertIn('"desktop": "ok"', output.call_args.args[0])


class DesktopWindowTests(unittest.TestCase):
    """Run with a native desktop or xvfb-run; unit tests elsewhere stay headless."""
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls.root = tk.Tk()
            cls.root.withdraw()
        except (ImportError, RuntimeError) as exc:
            raise unittest.SkipTest(f"Tk unavailable: {exc}") from exc
        except Exception as exc:
            if exc.__class__.__name__ == "TclError":
                raise unittest.SkipTest(f"Graphical display unavailable: {exc}") from exc
            raise
        cls.app = DesktopApp(cls.root, hotkey=False)

    @classmethod
    def tearDownClass(cls):
        cls.app.close()

    def setUp(self):
        self.app.output.delete("1.0", "end")
        self.app.query.set("")
        self.app.set_phase("idle")

    def test_fast_enter_selects_current_pinyin_and_copy_preserves_text(self):
        self.app.query.set("nihao")
        self.app.choose_first_candidate()
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "你好")
        self.app.copy_text()
        self.assertEqual(self.root.clipboard_get(), "你好")
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "你好")

    def test_traditional_candidates(self):
        self.app.script.set("繁體")
        self.app.query.set("zhongguo")
        self.app.choose_first_candidate()
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "中國")
        self.app.script.set("简体")

    def test_cancelled_queued_result_cannot_overwrite_text(self):
        self.app.output.insert("end", "保留文字")
        self.app.set_phase("cancelling")
        self.app.job.events.put(("result", "不要插入"))
        self.app.job.events.put(("idle", None))
        self.app.poll()
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "保留文字")
        self.assertEqual(self.app.phase, "idle")

    def test_clipboard_failure_keeps_recoverable_text(self):
        self.app.output.insert("end", "不能丢失")
        with patch.object(self.root, "clipboard_append", side_effect=self.app.tk.TclError()):
            self.app.copy_text()
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "不能丢失")
        self.assertIn("剪贴板暂不可用", self.app.status.get())


if __name__ == "__main__":
    unittest.main()
