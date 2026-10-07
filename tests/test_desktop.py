import os
import tempfile
from pathlib import Path
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
        cls.temp = tempfile.TemporaryDirectory()
        cls.environment = patch.dict(os.environ, {"IME_SETTINGS_PATH": str(Path(cls.temp.name) / "settings.json"), "IME_LEXICON_PATH": str(Path(cls.temp.name) / "lexicon.json")})
        cls.environment.start()
        cls.app = DesktopApp(cls.root, hotkey=False)

    @classmethod
    def tearDownClass(cls):
        cls.app.close(force=True)
        cls.environment.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.app.output.delete("1.0", "end")
        self.app.query.set("")
        self.app.set_phase("idle")

    def test_fast_space_selects_current_pinyin_and_copy_preserves_text(self):
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

    def test_enter_commits_raw_text(self):
        self.app.query.set("nihao")
        self.app.commit_raw()
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "nihao")
        self.assertEqual(self.app.query.get(), "")

    def test_clear_is_reversible(self):
        self.app.output.insert("end", "保留")
        self.app.clear_text()
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "")
        self.app.undo_text()
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "保留")

    def test_partial_and_meter_do_not_commit_text(self):
        self.app.set_phase("recording")
        self.app.job.events.put(("partial", "正在说话"))
        self.app.job.events.put(("level", 0.7))
        self.app.poll()
        self.assertEqual(self.app.partial.get(), "正在说话")
        self.assertAlmostEqual(self.app.level.get(), 0.7)
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "")

    def test_cancel_close_retains_editor(self):
        self.app.output.insert("end", "未保存")
        with patch("tkinter.messagebox.askyesnocancel", return_value=None):
            self.app.close()
        self.assertFalse(self.app.closed)
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "未保存")

    def test_settings_dialog_applies_persistent_controls_and_locale(self):
        original = self.app.settings.snapshot()
        try:
            self.app.open_settings()
            self.root.update_idletasks()
            self.app.locale.set("en")
            self.app.theme.set("contrast")
            self.app.font_size.set(18)
            self.app.push_to_talk.set(True)
            self.app.fuzzy.set(True)
            self.app.fuzzy_pairs_value.set("zh:z, n:l")
            self.app.persist_preferences()
            saved = self.app.settings.snapshot()
            self.assertEqual(saved["locale"], "en")
            self.assertEqual(saved["theme"], "contrast")
            self.assertEqual(saved["font_size"], 18)
            self.assertEqual(saved["fuzzy_pairs"], [["zh", "z"], ["n", "l"]])
            self.assertTrue(saved["push_to_talk"])
            self.assertEqual(self.root.title(), "Shuangsheng · Chinese input and local dictation")
            self.assertEqual(self.app.record_button.cget("text"), "Record")
            self.assertEqual(self.app.output.cget("background"), "#000000")
            self.assertEqual(self.app.settings_window.title(), "Settings · Saved locally")
        finally:
            self.app.settings.update(original)
            self.app.locale.set(original["locale"])
            self.app.theme.set(original["theme"])
            self.app.font_size.set(original["font_size"])
            self.app.push_to_talk.set(original["push_to_talk"])
            self.app.fuzzy.set(original["fuzzy"])
            self.app.fuzzy_pairs_value.set(", ".join(":".join(pair) for pair in original["fuzzy_pairs"]))
            self.app.apply_theme()
            self.app.apply_locale()
            self.app.settings_window.destroy()

    def test_runtime_profile_cannot_enable_auto_insert(self):
        from ime.insertion import Target
        original = self.app.settings.snapshot()
        target = Target("win32", os.getpid() + 1000, 42, application_id="org.example.editor")
        try:
            self.app.settings.update({"app_profiles": {target.application_id: {"language": "yue", "script": "traditional", "input_scheme": "pinyin", "auto_insert": True}}})
            self.app.auto_insert.set(False)
            with patch.object(self.app.insertion, "capture", return_value=target), patch.object(self.app.job, "start", return_value=True) as start:
                self.app.toggle_recording()
            self.assertEqual(start.call_args.args[:2], ("yue", "traditional"))
            self.assertIsNone(self.app.original_target)
            self.assertFalse(self.app.auto_insert.get())
            self.assertEqual(self.app.last_application_id, target.application_id)
            self.assertNotIn("auto_insert", self.app.active_profile)
        finally:
            self.app.settings.update(original)
            self.app.set_phase("idle")

    def test_profile_lexicon_and_assistant_dialogs_open_without_workers(self):
        with patch.object(self.app.job, "start") as record:
            self.app.output.insert("end", "预览原文")
            self.app.manage_profiles()
            self.app.manage_lexicon()
            self.app.open_assistant()
            self.root.update_idletasks()
            self.assertEqual(self.app.output.get("1.0", "end-1c"), "预览原文")
            record.assert_not_called()
            for window in self.root.winfo_children():
                if isinstance(window, self.app.tk.Toplevel):
                    window.destroy()

    def test_update_dialog_is_user_triggered_and_injected_updater_drives_real_controls(self):
        from unittest.mock import Mock
        updater = Mock(current_version="0.4.0")
        updater.status.return_value = {"supported": True, "detail": "Verified side-by-side installation"}
        updater.check.return_value = {"version": "v0.5.0", "page_url": "https://example.test/release"}
        original = self.app.updater
        self.app.updater = updater
        try:
            self.app.open_updates()
            self.root.update_idletasks()
            updater.check.assert_not_called()
            updater.download.assert_not_called()
            self.assertEqual(str(self.app.update_check_button.cget("state")), "normal")
            self.assertEqual(str(self.app.update_install_button.cget("state")), "disabled")
            with patch("threading.Thread", ImmediateThread):
                self.app.check_update()
            self.app.handle_update_events()
            self.assertIn("v0.5.0", self.app.update_version_label.cget("text"))
            self.assertEqual(str(self.app.update_download_button.cget("state")), "normal")
            self.assertEqual(str(self.app.update_install_button.cget("state")), "disabled")
            updater.install.assert_not_called()
        finally:
            self.app.close_update_dialog()
            self.app.updater = original


class Value:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value
    def trace_add(self, *_):
        pass


class TextBuffer:
    def __init__(self, value=""):
        self.value = value
    def get(self, *_):
        return self.value
    def insert(self, _position, text):
        self.value += text
    def delete(self, *_):
        self.value = ""
    def see(self, *_):
        pass
    def edit_separator(self):
        pass


class DesktopCompositionUnitTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        self.app = DesktopApp.__new__(DesktopApp)
        self.app.root = Mock()
        self.app.output = TextBuffer("已有上下文")
        self.app.learning_context = "已有上下文"
        self.app.query = Value("nihao")
        self.app.script = Value("简体")
        self.app.scheme = Value("pinyin")
        self.app.input_mode = Value("chinese")
        self.app.fuzzy = Value(False)
        self.app.candidate_count = Value(9)
        self.app.status = Value()
        self.app.active_profile = {}
        self.app.query_after = 12
        self.app.candidate_box = Mock()
        self.app.candidate_box.curselection.return_value = (0,)
        self.app.query_box = Mock()
        self.app.engine = Mock()
        self.app.engine.candidates.return_value = [{"text": "你好", "pinyin": "ni hao"}]
        self.app.engine.predict.return_value = [{"text": "世界", "pinyin": "shi jie"}]

    def test_constructor_uses_persisted_preferences_without_audio_or_hotkey(self):
        import sys
        import types
        from unittest.mock import Mock
        from ime.settings import SettingsStore
        from ime.updater import acknowledge_startup
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            store.update({"language": "yue", "script": "traditional", "font_size": 18,
                          "hotkey": "Ctrl+Alt+Space", "auto_insert": False})
            module = types.ModuleType("tkinter")
            for name in ["StringVar", "BooleanVar", "IntVar", "DoubleVar"]:
                setattr(module, name, lambda value="": Value(value))
            output = TextBuffer()
            for name in ["pack", "bind", "configure", "edit_modified"]:
                setattr(output, name, Mock(return_value=False))
            module.Text = Mock(return_value=output)
            module.Listbox = Mock()
            module.ttk = Mock()
            module.TclError = RuntimeError
            with patch.dict(sys.modules, {"tkinter": module}), patch("ime.desktop.PinyinEngine"), patch("ime.desktop.GlobalShortcut") as shortcut:
                app = DesktopApp(Mock(), hotkey=False, settings=store)
            self.assertEqual(app.language.get(), "粤语")
            self.assertEqual(app.script.get(), "繁體")
            self.assertEqual(app.font_size.get(), 18)
            self.assertEqual(shortcut.call_args.kwargs["hotkey"], "<ctrl>+<alt>+<space>")
            shortcut.return_value.start.assert_not_called()
            self.assertFalse(app.auto_insert.get())
            self.assertEqual(app.root.after_idle.call_args_list[0].args, (acknowledge_startup,))
            self.assertEqual(app.root.after_idle.call_args_list[1].args, (app.restore_draft,))

    def test_enter_commits_raw_without_learning(self):
        self.assertEqual(self.app.commit_raw(), "break")
        self.assertEqual(self.app.output.value, "已有上下文nihao")
        self.assertEqual(self.app.query.get(), "")
        self.app.engine.learn.assert_not_called()

    def test_space_refreshes_then_selects_and_learns_with_previous_context(self):
        self.app.choose_first_candidate()
        self.app.root.after_cancel.assert_called_with(12)
        self.app.engine.candidates.assert_called_once_with("nihao", limit=9, script="simplified", context="已有上下文", scheme="pinyin", fuzzy=False)
        self.app.engine.learn.assert_called_once_with("nihao", "你好", context="已有上下文")
        self.app.engine.predict.assert_called_once_with("已有上下文你好", limit=9, script="simplified")
        self.assertEqual(self.app.output.value, "已有上下文你好")
        self.assertEqual(self.app.candidates[0]["text"], "世界")

    def test_number_refreshes_before_choosing(self):
        from types import SimpleNamespace
        self.app.candidate_box.curselection.return_value = (1,)
        self.app.engine.candidates.return_value += [{"text": "您好", "pinyin": "nin hao"}]
        self.assertEqual(self.app.number_candidate(SimpleNamespace(char="2")), "break")
        self.assertEqual(self.app.output.value, "已有上下文您好")

    def test_invalid_digit_and_non_ascii_digit_do_not_choose(self):
        from types import SimpleNamespace
        for digit in ["9", "２", "", "0"]:
            self.assertIsNone(self.app.number_candidate(SimpleNamespace(char=digit)))
        self.assertEqual(self.app.output.value, "已有上下文")
        self.app.engine.learn.assert_not_called()

    def test_learning_failure_does_not_duplicate_committed_text(self):
        self.app.engine.learn.side_effect = OSError("disk full")
        self.app.choose_first_candidate()
        self.assertEqual(self.app.output.value, "已有上下文你好")
        self.assertEqual(self.app.query.get(), "")
        self.assertIn("暂时无法保存", self.app.status.get())

    def test_prediction_selection_does_not_create_fake_query_learning(self):
        self.app.query.set("")
        self.app.update_candidates()
        self.app.choose_candidate()
        self.assertEqual(self.app.output.value, "已有上下文世界")
        self.app.engine.learn.assert_not_called()

    def test_context_is_bounded(self):
        self.app.output.value = "中" * 1000
        self.assertEqual(self.app.context(), "中" * 500)

    def test_arbitrary_editor_draft_is_not_persisted_as_learning_context(self):
        self.app.learning_context = ""
        self.app.output.value = "未明确提交的敏感草稿"
        self.app.choose_first_candidate()
        self.app.engine.learn.assert_called_once_with("nihao", "你好", context="")
        self.assertEqual(self.app.learning_context, "你好")
        self.app.query.set("nihao")
        self.app.choose_first_candidate()
        self.assertEqual(self.app.engine.learn.call_args.kwargs["context"], "你好")

    def test_jyutping_tones_and_mixed_numbers_are_preserved(self):
        from types import SimpleNamespace
        self.app.scheme.set("jyutping")
        self.app.query.set("nei5")
        self.assertIsNone(self.app.number_candidate(SimpleNamespace(char="5")))
        self.app.engine.candidates.return_value = [{"text": "你", "pinyin": "nei5"}]
        self.assertEqual(self.app.number_candidate(SimpleNamespace(char="1"), force=True), "break")
        self.assertEqual(self.app.output.value, "已有上下文你")
        self.app.scheme.set("pinyin")
        for raw in ["Python", "python", "v", "v1.2.3", "name@example.com"]:
            self.app.query.set(raw)
            self.assertIsNone(self.app.number_candidate(SimpleNamespace(char="3")))

    def test_english_mode_preserves_space_and_number_keys(self):
        from types import SimpleNamespace
        self.app.input_mode.set("english")
        self.assertIsNone(self.app.choose_first_candidate())
        self.assertIsNone(self.app.number_candidate(SimpleNamespace(char="2")))
        self.app.query.set("Hello world 2")
        self.app.commit_raw()
        self.assertEqual(self.app.output.value, "已有上下文Hello world 2")
        self.assertEqual(self.app.candidates, [])
        self.app.engine.learn.assert_not_called()

    def test_unavailable_scheme_keeps_raw_text_recoverable(self):
        self.app.scheme.set("jyutping")
        self.app.engine.candidates.side_effect = ValueError("Jyutping dictionary unavailable")
        self.app.update_candidates()
        self.assertEqual(self.app.candidates, [])
        self.assertIn("unavailable", self.app.status.get())
        self.app.commit_raw()
        self.assertEqual(self.app.output.value, "已有上下文nihao")

    def test_application_profile_only_applies_safe_keys(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        self.app.settings = Mock()
        self.app.settings.get.return_value = {"org.editor": {"language": "en", "script": "traditional", "input_scheme": "pinyin", "auto_insert": True, "model": "other"}}
        self.assertEqual(self.app.target_profile(SimpleNamespace(application_id="org.editor")),
                         {"language": "en", "script": "traditional", "input_scheme": "pinyin"})
        self.assertEqual(self.app.target_profile(SimpleNamespace(application_id="unknown")), {})
        self.assertEqual(self.app.target_profile(None), {})

    def test_environment_backend_override_prevents_unsupported_whisper_hint(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ime.settings import SettingsStore
        from ime.speech import SpeechService
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            store.update({"backend": "whisper", "initial_prompt": "configured whisper hint"})
            with patch.dict(os.environ, {"IME_SPEECH_BACKEND": "sensevoice", "IME_SENSEVOICE_MODEL": ""}):
                self.app.service = SpeechService(settings=store)
                self.assertEqual(self.app.service.status()["backend"], "sensevoice")
                self.app.settings = store
                self.app.phase = "idle"
                self.app.language = Value("普通话")
                self.app.device = Value("系统默认麦克风")
                self.app.device_ids = {"系统默认麦克风": None}
                self.app.insertion = Mock()
                self.app.insertion.capture.return_value = None
                self.app.auto_insert = Value(False)
                self.app.partial = Value()
                self.app.overlay_enabled = Value(False)
                self.app.locale = Value("zh")
                self.app.job = Mock()
                self.app.job.start.return_value = True
                self.app.set_phase = Mock()
                self.app.toggle_recording()
                self.assertIsNone(self.app.job.start.call_args.kwargs["initial_prompt"])

    def test_install_rime_only_installs_explicitly_selected_scheme(self):
        import queue
        import sys
        import types
        from unittest.mock import Mock
        self.app.phase = "idle"
        self.app.scheme.set("jyutping")
        self.app.set_phase = Mock()
        self.app.commands = queue.Queue()
        self.app.root = Mock()
        tkinter = types.ModuleType("tkinter")
        tkinter.messagebox = Mock()
        tkinter.messagebox.askyesno.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            result = {"user_dir": directory, "backups": []}
            class ImmediateThread:
                def __init__(self, target, **_):
                    self.target = target
                def start(self):
                    self.target()
            with patch.dict(sys.modules, {"tkinter": tkinter}), patch("scripts.install_rime.current_platform", return_value="linux"), patch("scripts.install_rime.default_user_dir", return_value=Path(directory)), patch("scripts.install_rime.install", return_value=result) as install, patch("threading.Thread", ImmediateThread):
                self.app.install_rime()
            install.assert_called_once_with(Path(directory), schemes=["jyutping"])
            self.assertIn("双声粤拼", tkinter.messagebox.askyesno.call_args.args[1])
            self.assertEqual(self.app.commands.get_nowait(), ("rime_installed", ("Fcitx5-Rime", result, "双声粤拼")))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_draft_writes_only_after_opt_in(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            self.app.settings = SimpleNamespace(path=Path(directory) / "settings.json")
            self.app.save_draft = Value(False)
            self.app.draft_after = None
            self.app.write_draft()
            self.assertFalse(self.app.draft_path.exists())
            self.app.save_draft.set(True)
            self.app.write_draft()
            self.assertEqual(self.app.draft_path.read_text(), "已有上下文")
            self.app.remove_draft()
            self.assertFalse(self.app.draft_path.exists())

    def test_cancel_close_retains_editor_headlessly(self):
        self.app.saved_text = ""
        self.app.closed = False
        with patch.dict("sys.modules", {"tkinter": __import__("types").SimpleNamespace(messagebox=__import__("unittest.mock").mock.Mock())}):
            # Use an explicit module mock because this runtime has no shared Tk libraries.
            import sys
            sys.modules["tkinter"].messagebox.askyesnocancel.return_value = None
            self.app.close()
        self.assertFalse(self.app.closed)
        self.assertEqual(self.app.output.value, "已有上下文")


class ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class DesktopUpdateTests(unittest.TestCase):
    def setUp(self):
        import queue
        from types import ModuleType
        from unittest.mock import Mock
        # These test updater orchestration without a GUI or installed Tk libraries.
        # DesktopWindowTests separately exercise the real Tk update dialog.
        tkinter = ModuleType("tkinter")
        messagebox = ModuleType("tkinter.messagebox")
        messagebox.askyesno = Mock()
        messagebox.askyesnocancel = Mock()
        tkinter.messagebox = messagebox
        modules = patch.dict("sys.modules", {"tkinter": tkinter, "tkinter.messagebox": messagebox})
        modules.start()
        self.addCleanup(modules.stop)
        self.app = DesktopApp.__new__(DesktopApp)
        app = self.app
        app.locale = Value("en")
        app.root = Mock()
        app.output = TextBuffer("saved text")
        app.output.configure = Mock()
        app.query = Value("")
        app.query_box = Mock()
        app.candidate_box = Mock()
        app.saved_text = "saved text"
        app.status = Value()
        app.closed = False
        app.phase = "idle"
        app.job = Mock(busy=False)
        app.updater = Mock(current_version="0.4.0")
        app.updater.status.return_value = {"supported": True, "detail": "Native plugin update may restart Fcitx."}
        app.updater.check.return_value = {"version": "v0.5.0", "page_url": "https://example.test/release"}
        app.updater.download.side_effect = lambda offer, progress: (progress(512, 1024), {"version": offer["version"], "validated": True})[1]
        app.update_events = queue.Queue()
        app.update_operation = None
        app.update_generation = app.update_task_generation = 0
        app.update_window = Mock()
        app.update_window.winfo_exists.return_value = True
        app.update_supported = app.updater.status()
        app.update_offer = app.update_downloaded = None
        app.update_status, app.update_progress = Value(), Value(0)
        for name in ["check", "download", "install", "close"]:
            setattr(app, f"update_{name}_button", Mock())
        app.update_version_label = Mock()
        app.set_phase = Mock(side_effect=lambda phase: setattr(app, "phase", phase))
        app.close = Mock()
        self.thread = patch("threading.Thread", ImmediateThread)
        self.thread.start()
        self.addCleanup(self.thread.stop)

    def prepare(self):
        self.app.check_update()
        self.app.handle_update_events()
        self.app.download_update()
        self.app.handle_update_events()

    def test_check_download_and_explicit_install_are_separate(self):
        self.app.check_update()
        self.assertIsNone(self.app.update_offer)  # Worker never touches Tk state.
        self.assertEqual(self.app.update_operation, "check")
        self.app.handle_update_events()
        self.assertEqual(self.app.update_offer["version"], "v0.5.0")
        self.app.download_update()
        self.assertIsNone(self.app.update_downloaded)
        self.app.handle_update_events()
        self.assertTrue(self.app.update_downloaded["validated"])
        self.assertEqual(self.app.update_progress.get(), 1)
        self.app.updater.install.assert_not_called()
        self.app.close.assert_not_called()
        self.assertTrue(any("Install v0.5.0" in call.kwargs.get("text", "") for call in self.app.update_install_button.configure.call_args_list))

    def test_download_progress_and_unknown_length(self):
        self.app.update_operation = "download"
        self.app.update_events.put(("progress", 0, "download", (512, 1024)))
        self.app.handle_update_events()
        self.assertEqual(self.app.update_progress.get(), 0.5)
        self.app.update_events.put(("progress", 0, "download", (1024, 0)))
        self.app.handle_update_events()
        self.assertEqual(self.app.update_progress.get(), 0)
        self.assertIn("MiB", self.app.update_status.get())

    def test_dialog_close_discards_stale_check_result(self):
        self.app.check_update()
        self.app.close_update_dialog()
        self.app.handle_update_events()
        self.assertIsNone(self.app.update_offer)
        self.assertIsNone(self.app.update_operation)
        self.assertIsNone(self.app.update_window)
        self.app.updater.install.assert_not_called()

    def test_dialog_close_aborts_download_on_next_progress(self):
        self.app.update_offer = self.app.updater.check.return_value
        def download(_offer, progress):
            self.app.close_update_dialog()
            progress(1, 2)
            self.fail("Closed dialog accepted download progress")
        self.app.updater.download.side_effect = download
        self.app.download_update()
        self.app.handle_update_events()
        self.assertIsNone(self.app.update_downloaded)
        self.assertIsNone(self.app.update_operation)
        self.app.updater.install.assert_not_called()

    def test_install_closes_only_after_success_and_shows_version_and_platform_detail(self):
        self.prepare()
        with patch("tkinter.messagebox.askyesno", return_value=True) as confirm:
            self.app.install_update()
        self.assertIn("v0.5.0", confirm.call_args.args[1])
        self.assertIn("Native plugin", confirm.call_args.args[1])
        self.assertEqual(self.app.phase, "updating")
        self.app.output.configure.assert_called_with(state="disabled")
        self.app.close.assert_not_called()
        self.app.handle_update_events()
        self.app.updater.install.assert_called_once_with(self.app.update_downloaded)
        self.app.close.assert_called_once_with(force=True)

    def test_install_failure_unlocks_editor_and_retains_application(self):
        self.prepare()
        self.app.updater.install.side_effect = RuntimeError("new GUI did not acknowledge startup")
        with patch("tkinter.messagebox.askyesno", return_value=True):
            self.app.install_update()
        self.app.handle_update_events()
        self.assertEqual(self.app.phase, "idle")
        self.app.output.configure.assert_called_with(state="normal")
        self.assertIn("did not acknowledge", self.app.update_status.get())
        self.app.close.assert_not_called()

    def test_dirty_text_cancelled_or_failed_save_never_installs(self):
        self.prepare()
        self.app.output.value = "unsaved text"
        with patch("tkinter.messagebox.askyesno", return_value=False):
            self.app.install_update()
        self.app.updater.install.assert_not_called()
        self.app.save_text = __import__("unittest.mock").mock.Mock()  # Save chooser cancellation.
        with patch("tkinter.messagebox.askyesno", return_value=True) as confirm:
            self.app.install_update()
        self.app.save_text.assert_called_once()
        self.assertEqual(confirm.call_count, 1)
        self.assertIn("not saved", self.app.update_status.get())
        self.app.updater.install.assert_not_called()
        self.assertEqual(self.app.output.value, "unsaved text")

    def test_successful_save_precedes_install_confirmation(self):
        from unittest.mock import Mock
        self.prepare()
        self.app.output.value = "unsaved text"
        self.app.save_text = Mock(side_effect=lambda: setattr(self.app, "saved_text", self.app.output.value))
        with patch("tkinter.messagebox.askyesno", side_effect=[True, False]) as confirm:
            self.app.install_update()
        self.assertEqual(confirm.call_count, 2)
        self.assertEqual(self.app.saved_text, "unsaved text")
        self.app.updater.install.assert_not_called()

    def test_install_rejects_recording_and_uncommitted_composition(self):
        self.prepare()
        for phase, busy, query in [("recording", True, ""), ("idle", False, "nihao")]:
            self.app.phase, self.app.job.busy = phase, busy
            self.app.query.set(query)
            with patch("tkinter.messagebox.askyesno") as confirm:
                self.app.install_update()
            confirm.assert_not_called()
        self.app.updater.install.assert_not_called()

    def test_busy_install_cannot_close_or_cancel(self):
        self.app.update_operation = "install"
        self.app.phase = "updating"
        self.app.close_update_dialog()
        self.app.update_window.destroy.assert_not_called()
        DesktopApp.close(self.app)
        self.assertFalse(self.app.closed)
        self.app.root.destroy.assert_not_called()
        self.app.cancel_recording()
        self.assertEqual(self.app.phase, "updating")
        self.app.job.cancel.assert_not_called()

    def test_check_is_async_and_duplicate_click_does_not_start_more_work(self):
        import threading
        self.thread.stop()
        started, release = threading.Event(), threading.Event()
        def check():
            started.set()
            if not release.wait(5):
                raise RuntimeError("test timed out")
            return None
        self.app.updater.check.side_effect = check
        try:
            self.app.check_update()
            self.assertTrue(started.wait(1))
            self.assertEqual(self.app.update_operation, "check")
            self.app.check_update()
            self.assertEqual(self.app.updater.check.call_count, 1)
            self.assertEqual(self.app.output.get(), "saved text")
        finally:
            release.set()
        event = self.app.update_events.get(timeout=2)
        self.app.update_events.put(event)
        self.app.handle_update_events()
        self.assertIsNone(self.app.update_offer)
        self.assertIn("latest stable", self.app.update_status.get())


if __name__ == "__main__":
    unittest.main()
