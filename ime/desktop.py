"""Native cross-platform Chinese composition and local dictation companion."""
from __future__ import annotations

import argparse
from array import array
import json
import queue
import re
from pathlib import Path
import sys
import time

from . import __version__
from .desktop_voice import DictationJob, GlobalShortcut, input_devices, pcm_to_wav
from .pinyin import PinyinEngine
from .speech import SpeechService


ENGLISH = {
    "双声 · 中文输入与本地听写": "Shuangsheng · Chinese input and local dictation",
    "检查更新…": "Check updates…", "关于双声 / 更新": "About Shuangsheng / Updates",
    "检查更新": "Check for updates", "下载更新": "Download update", "关闭": "Close",
    "双声": "Shuangsheng", "设置…": "Settings…", "开始录音": "Record", "停止并识别": "Stop and transcribe",
    "取消": "Cancel", "复制全部": "Copy all", "保存…": "Save…", "清空（可撤销）": "Clear (undo available)",
    "撤销": "Undo", "词库…": "Lexicon…", "本地助手…": "Local assistant…",
    "拼音 / 双拼 / 粤拼 · Space 选词 · Enter 原文": "Pinyin / Shuangpin / Jyutping · Space selects · Enter keeps raw text",
    "准备就绪。Space 选词；1–9 选词；Enter 输入原文；Esc 取消。": "Ready. Space selects; 1–9 chooses; Enter commits raw text; Esc cancels.",
    "设置 · 本机保存": "Settings · Saved locally", "语音语言": "Speech language", "文字": "Output script",
    "输入方案": "Input scheme", "麦克风": "Microphone", "刷新麦克风": "Refresh microphones",
    "计算设备": "Compute device", "语音引擎": "Speech backend", "配色": "Theme", "字体大小": "Font size",
    "候选数量": "Candidates", "界面语言": "UI language", "SenseVoice 本地目录": "SenseVoice local directory",
    "Whisper 专有词（逗号分隔）": "Whisper terminology (comma separated)", "Whisper 背景提示": "Whisper context hint",
    "紧凑窗口": "Compact window", "模糊拼音": "Fuzzy pinyin", "按住说话（松开识别）": "Hold to talk (release to transcribe)",
    "录音浮窗（支持 Windows / macOS，不抢焦点）": "Recording overlay (Windows / macOS; never activates)",
    "保存本地草稿用于恢复（包含文本）": "Save local drafts for recovery (contains text)",
    "自动插入原目标（仅已确认的非密码编辑框；否则留在此处）": "Auto paste into captured native text field; otherwise review here",
    "应用并保存": "Apply and save", "选择模型…": "Choose model…", "下载…": "Download…", "预热": "Warm up", "卸载": "Unload",
    "安装系统拼音…": "Install system input…", "安装系统输入方案…": "Install selected system scheme…", "设置已保存。": "Preferences saved.",
    "新词条": "New entry", "保存": "Save", "删除": "Delete", "导入…": "Import…", "导出…": "Export…",
    "短语": "Phrase", "拼音 / 粤拼": "Pinyin / Jyutping", "快捷码": "Shortcut", "置顶": "Pin",
    "生成预览": "Generate preview", "替换原文": "Replace original", "插入预览": "Insert preview",
    "本机 URL": "Local URL", "模型": "Model", "目标语言": "Target language",
    "启用本机助手（将本窗口文本发送到已配置的本机服务）": "Enable local assistant (send editor text to the configured local service)",
    "快捷键格式：Ctrl+Alt+Space 或 <ctrl>+<alt>+d。": "Shortcut format: Ctrl+Alt+Space or <ctrl>+<alt>+d.",
    "全局快捷键已禁用；请使用录音按钮。": "Global shortcut disabled; use the Record button.",
    "已清空。点击撤销可恢复。": "Cleared. Undo restores the text.", "没有可撤销的操作。": "Nothing to undo.",
    "还没有可复制的文字。": "No text to copy yet.", "文本已保存。": "Text saved.",
    "关闭前保存": "Save before closing", "文本尚未保存。保存后关闭？选择“否”放弃本次文本。": "Unsaved text. Save before closing? No discards this text.",
}



def literal_query(text):
    words = {"api", "python", "javascript", "typescript", "timeout", "hello", "world", "docker", "git", "github", "linux", "windows", "macos", "vscode", "http", "https", "email", "version", "npm", "pip", "node", "json", "html", "css"}
    return bool(re.search(r"[A-Z0-9@_/:\\.\-]", text) or text.lower() in words)


class DesktopApp:
    def __init__(self, root, *, hotkey=True, settings=None, updater=None):
        import tkinter as tk
        from tkinter import ttk
        from .settings import SettingsStore
        from .insertion import InsertionAdapter

        self.tk, self.ttk, self.root = tk, ttk, root
        self.settings = settings or SettingsStore()
        prefs = self.settings.snapshot()
        self.locale = tk.StringVar(value=prefs.get("locale", "zh"))
        self.root.title(self.t("双声 · 中文输入与本地听写"))
        self.root.geometry("720x570" if prefs.get("compact", True) else "900x760")
        self.root.minsize(570, 420)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.closed, self.phase = False, "idle"
        self.recorded_at = None
        self.device_ids = {"系统默认麦克风": None}
        self.commands = queue.Queue()
        self.engine = PinyinEngine(fuzzy_pairs=prefs.get("fuzzy_pairs"))
        self.service = SpeechService(settings=self.settings, lexicon=self.engine.lexicon)
        self.job = DictationJob(self.service)
        self.insertion = InsertionAdapter()
        self.original_target = None
        self.shortcut_enabled = hotkey
        self.shortcut = self.make_shortcut()
        self.query_after, self.draft_after = None, None
        self.candidates = []
        self.saved_text = ""
        self.learning_context = ""
        self.settings_window = None
        self.updater = updater
        self.update_window = None
        self.update_events = queue.Queue()
        self.update_operation = None
        self.update_generation = 0
        self.update_task_generation = 0
        self.update_supported = {"supported": False}
        self.update_offer = None
        self.update_downloaded = None
        self.update_status = tk.StringVar(value="")
        self.update_progress = tk.DoubleVar(value=0)
        self.active_profile = {}
        self.last_application_id = ""
        self.overlay = None
        self.language = tk.StringVar(value={"zh": "普通话", "yue": "粤语", "auto": "自动检测", "en": "English"}.get(prefs.get("language"), "普通话"))
        self.script = tk.StringVar(value="繁體" if prefs.get("script") == "traditional" else "简体")
        preferred = prefs.get("microphone", "")
        if preferred and preferred.split(":", 1)[0].isdigit():
            # Device indexes can change across restarts; resolve the saved name instead.
            self.device_ids[preferred] = preferred.split(":", 1)[1].strip()
        self.device = tk.StringVar(value=preferred if preferred in self.device_ids else "系统默认麦克风")
        self.query = tk.StringVar()
        self.partial = tk.StringVar()
        self.status = tk.StringVar(value=self.t("准备就绪。Space 选词；1–9 选词；Enter 输入原文；Esc 取消。"))
        self.hotkey_status = tk.StringVar(value=self.t("全局快捷键已禁用；请使用录音按钮。"))
        self.model_status = tk.StringVar()
        self.level = tk.DoubleVar(value=0)
        self.auto_insert = tk.BooleanVar(value=prefs.get("auto_insert", False))
        self.save_draft = tk.BooleanVar(value=prefs.get("save_draft", False))
        self.fuzzy = tk.BooleanVar(value=prefs.get("fuzzy", False))
        self.scheme = tk.StringVar(value=prefs.get("input_scheme", "pinyin"))
        self.input_mode = tk.StringVar(value=prefs.get("input_mode", "chinese"))
        self.fuzzy_pairs_value = tk.StringVar(value=", ".join(":".join(pair) for pair in prefs.get("fuzzy_pairs", [])))
        self.font_size = tk.IntVar(value=prefs.get("font_size", 14))
        self.candidate_count = tk.IntVar(value=prefs.get("candidate_count", 9))
        self.theme = tk.StringVar(value=prefs.get("theme", "system"))
        self.push_to_talk = tk.BooleanVar(value=prefs.get("push_to_talk", False))
        self.overlay_enabled = tk.BooleanVar(value=prefs.get("overlay", False))
        self.hotkey_value = tk.StringVar(value=prefs.get("hotkey", "<ctrl>+<alt>+<space>"))
        self.compute_device = tk.StringVar(value=prefs.get("device", "cpu"))
        self.backend_value = tk.StringVar(value=prefs.get("backend", "whisper"))
        self.sensevoice_model = tk.StringVar(value=prefs.get("sensevoice_model", ""))
        self.terminology = tk.StringVar(value=", ".join(prefs.get("hotwords", [])))
        self.initial_prompt = tk.StringVar(value=prefs.get("initial_prompt", ""))
        self.compact = tk.BooleanVar(value=prefs.get("compact", True))

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text=self.t("双声"), font=("TkDefaultFont", 18, "bold")).pack(side="left")
        ttk.Button(toolbar, text=self.t("设置…"), command=self.open_settings).pack(side="right")
        ttk.Button(toolbar, text=self.t("检查更新…"), command=self.open_updates).pack(side="right", padx=4)
        ttk.Button(toolbar, text="中 / EN", command=self.toggle_input_mode).pack(side="right", padx=4)
        self.record_button = ttk.Button(toolbar, text=self.t("开始录音"), command=self.toggle_recording)
        self.record_button.pack(side="right", padx=6)
        self.cancel_button = ttk.Button(toolbar, text=self.t("取消"), command=self.cancel_recording, state="disabled")
        self.cancel_button.pack(side="right")
        self.record_button.bind("<ButtonPress-1>", self.hold_press)
        self.record_button.bind("<ButtonRelease-1>", self.hold_release)
        self.output = tk.Text(frame, height=8, wrap="word", undo=True, font=("TkDefaultFont", self.font_size.get()))
        self.output.pack(fill="both", expand=True)
        self.output.bind("<<Modified>>", self.text_changed)
        self.output.edit_modified(False)
        live = ttk.Frame(frame)
        live.pack(fill="x", pady=4)
        ttk.Progressbar(live, variable=self.level, maximum=1, length=70).pack(side="left", padx=(0, 8))
        ttk.Label(live, textvariable=self.partial, wraplength=560).pack(side="left", fill="x", expand=True)
        composition = ttk.LabelFrame(frame, text=self.t("拼音 / 双拼 / 粤拼 · Space 选词 · Enter 原文"), padding=6)
        composition.pack(fill="x")
        self.query_box = ttk.Entry(composition, textvariable=self.query)
        self.query_box.pack(fill="x")
        self.query_box.bind("<Return>", self.commit_raw)
        self.query_box.bind("<space>", self.choose_first_candidate)
        self.query_box.bind("<Down>", self.focus_candidates)
        self.query_box.bind("<KeyPress>", self.number_candidate)
        self.query_box.bind("<Alt-KeyPress>", lambda event: self.number_candidate(event, force=True))
        self.query.trace_add("write", self.schedule_candidates)
        self.candidate_box = tk.Listbox(composition, height=3, exportselection=False, activestyle="dotbox")
        self.candidate_box.pack(fill="x", pady=(4, 0))
        self.candidate_box.bind("<Double-Button-1>", self.choose_candidate)
        self.candidate_box.bind("<Return>", self.commit_raw)
        self.candidate_box.bind("<space>", self.choose_candidate)
        self.candidate_box.bind("<KeyPress>", self.number_candidate)
        self.candidate_box.bind("<Alt-KeyPress>", lambda event: self.number_candidate(event, force=True))
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text=self.t("复制全部"), command=self.copy_text).pack(side="left")
        ttk.Button(actions, text=self.t("保存…"), command=self.save_text).pack(side="left", padx=6)
        ttk.Button(actions, text=self.t("清空（可撤销）"), command=self.clear_text).pack(side="left")
        ttk.Button(actions, text=self.t("撤销"), command=self.undo_text).pack(side="left", padx=6)
        ttk.Button(actions, text=self.t("词库…"), command=self.manage_lexicon).pack(side="right")
        ttk.Button(actions, text=self.t("本地助手…"), command=self.open_assistant).pack(side="right", padx=4)
        ttk.Label(frame, textvariable=self.status, wraplength=680).pack(anchor="w")
        self.root.bind("<Escape>", self.escape)
        self.apply_theme()
        self.refresh_model_status()
        if hotkey:
            self.hotkey_status.set(self.shortcut.start())
        self.root.after(50, self.poll)
        self.query_box.focus_set()
        from .updater import acknowledge_startup
        # Signal GUI readiness before an optional draft-recovery prompt can block.
        self.root.after_idle(acknowledge_startup)
        self.root.after_idle(self.restore_draft)

    def t(self, text):
        locale = getattr(self, "locale", None)
        return ENGLISH.get(text, text) if locale is not None and locale.get() == "en" else text

    def apply_locale(self):
        reverse = {value: key for key, value in ENGLISH.items()}
        def visit(widget):
            try:
                old = widget.cget("text")
                source = reverse.get(old, old)
                if source in ENGLISH:
                    widget.configure(text=self.t(source))
            except self.tk.TclError:
                pass
            for child in widget.winfo_children():
                visit(child)
        visit(self.root)
        self.root.title(self.t("双声 · 中文输入与本地听写"))
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.title(self.t("设置 · 本机保存"))

    def target_profile(self, target):
        if target is None or not target.application_id:
            return {}
        profile = self.settings.get("app_profiles", {}).get(target.application_id, {})
        # Auto-insertion is always a separate, explicit global preference.
        return {key: profile[key] for key in ("language", "script", "input_scheme") if key in profile}

    def update_overlay(self):
        if self.overlay is not None:
            self.overlay.update(self.status.get(), self.partial.get(), self.level.get())

    def update_message(self, chinese, english):
        return english if self.locale.get() == "en" else chinese

    def ensure_updater(self):
        if self.updater is None:
            from .updater import Updater
            self.updater = Updater()
        return self.updater

    def open_updates(self):
        if self.update_window is not None and self.update_window.winfo_exists():
            self.update_window.lift()
            return
        tk, ttk = self.tk, self.ttk
        window = self.update_window = tk.Toplevel(self.root)
        window.title(self.t("关于双声 / 更新"))
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self.close_update_dialog)
        window.bind("<Escape>", lambda _event: self.close_update_dialog())
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        try:
            updater = self.ensure_updater()
            self.update_supported = updater.status()
            version = updater.current_version
        except Exception as exc:
            self.update_supported = {"supported": False, "detail": str(exc)}
            version = __version__
        ttk.Label(frame, text=f"双声 · Shuangsheng {version}", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text=self.update_supported.get("detail", ""), wraplength=520).pack(anchor="w", pady=8)
        self.update_version_label = ttk.Label(frame, text="", wraplength=520)
        self.update_version_label.pack(anchor="w", pady=4)
        ttk.Progressbar(frame, variable=self.update_progress, maximum=1, length=500).pack(fill="x", pady=8)
        ttk.Label(frame, textvariable=self.update_status, wraplength=520).pack(anchor="w", pady=8)
        ttk.Label(frame, text=self.update_message("仅在点击后连接 GitHub；下载后校验文件并试运行。安装新版本会重启窗口，旧版本仍保留。", "GitHub is contacted only after clicking Check. Downloads are verified and smoke-tested. Installation restarts the window and retains the old version."), wraplength=520).pack(anchor="w")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(14, 0))
        self.update_check_button = ttk.Button(actions, text=self.t("检查更新"), command=self.check_update)
        self.update_check_button.pack(side="left")
        self.update_download_button = ttk.Button(actions, text=self.t("下载更新"), command=self.download_update)
        self.update_download_button.pack(side="left", padx=6)
        self.update_install_button = ttk.Button(actions, text=self.update_message("安装并重启", "Install and restart"), command=self.install_update)
        self.update_install_button.pack(side="left")
        self.update_close_button = ttk.Button(actions, text=self.t("关闭"), command=self.close_update_dialog)
        self.update_close_button.pack(side="right", padx=(10, 0))
        if self.update_operation:
            self.update_status.set(self.update_message("上一项更新操作正在结束，请稍候…", "The previous update operation is finishing…"))
        else:
            self.update_status.set(self.update_message("点击“检查更新”获取最新稳定版。", "Click Check for updates to find the latest stable release."))
        self.render_update_controls()

    def close_update_dialog(self):
        if self.update_operation == "install":
            self.update_status.set(self.update_message("正在安装并启动新版本，请等待完成。", "Installing and starting the new version; please wait."))
            return "break"
        self.update_generation += 1
        self.update_offer = None
        self.update_downloaded = None
        self.update_progress.set(0)
        if self.update_window is not None:
            self.update_window.destroy()
            self.update_window = None
        return "break"

    def render_update_controls(self):
        if self.update_window is None or not self.update_window.winfo_exists():
            return
        busy = self.update_operation is not None
        supported = bool(self.update_supported.get("supported"))
        self.update_check_button.configure(state="normal" if supported and not busy else "disabled")
        self.update_download_button.configure(state="normal" if self.update_offer and not busy else "disabled")
        self.update_install_button.configure(state="normal" if self.update_downloaded and not busy else "disabled")
        self.update_close_button.configure(state="disabled" if self.update_operation == "install" else "normal")
        if self.update_offer:
            version = self.update_offer["version"]
            self.update_version_label.configure(text=self.update_message(f"可用版本：{version}\n{self.update_offer.get('page_url', '')}", f"Available version: {version}\n{self.update_offer.get('page_url', '')}"))
            self.update_install_button.configure(text=self.update_message(f"安装 {version} 并重启", f"Install {version} and restart"))
        else:
            self.update_version_label.configure(text="")

    def run_update_operation(self, operation, action):
        import threading
        if self.update_operation is not None:
            return
        self.update_operation = operation
        generation = self.update_generation
        self.update_task_generation = generation
        self.render_update_controls()
        def worker():
            try:
                result = action(generation)
                self.update_events.put(("result", generation, operation, result))
            except Exception as exc:
                self.update_events.put(("error", generation, operation, str(exc)))
        threading.Thread(target=worker, daemon=True, name=f"desktop-update-{operation}").start()

    def check_update(self):
        if self.update_operation is not None:
            return
        self.update_offer = None
        self.update_downloaded = None
        self.update_progress.set(0)
        self.update_status.set(self.update_message("正在检查 GitHub 最新稳定版…", "Checking the latest stable GitHub release…"))
        self.run_update_operation("check", lambda _generation: self.ensure_updater().check())

    def download_update(self):
        if self.update_operation is not None or self.update_offer is None:
            return
        offer = dict(self.update_offer)
        self.update_downloaded = None
        self.update_progress.set(0)
        self.update_status.set(self.update_message(f"正在下载并验证 {offer['version']}…", f"Downloading and validating {offer['version']}…"))
        def action(generation):
            def progress(downloaded, total):
                if self.closed or generation != self.update_generation:
                    # Aborting the callback cancels a closed-dialog download without accepting its result.
                    raise RuntimeError("Update download cancelled")
                self.update_events.put(("progress", generation, "download", (downloaded, total)))
            return self.ensure_updater().download(offer, progress=progress)
        self.run_update_operation("download", action)

    def unlock_update_editor(self):
        for widget in (self.output, self.query_box, self.candidate_box):
            widget.configure(state="normal")
        self.set_phase("idle")

    def install_update(self):
        from tkinter import messagebox
        if self.update_operation is not None or self.update_downloaded is None:
            return
        if self.phase != "idle" or self.job.busy:
            self.update_status.set(self.update_message("请先结束录音或模型操作，再安装更新。", "Finish recording or model operations before installing."))
            return
        if self.query.get():
            self.update_status.set(self.update_message("请先提交或取消输入框中的组合，再安装更新。", "Commit or cancel the input composition before installing."))
            return
        if self.output.get("1.0", "end-1c") != self.saved_text:
            if not messagebox.askyesno(self.t("保存"), self.update_message("正文有未保存修改，安装前必须保存。现在选择保存位置？", "The editor has unsaved changes. Save them before installing?"), parent=self.update_window):
                return
            self.save_text()
            if self.output.get("1.0", "end-1c") != self.saved_text:
                self.update_status.set(self.update_message("文本尚未保存，未安装更新。", "Text is not saved; update installation was cancelled."))
                return
        version = self.update_offer["version"]
        confirmation = self.update_message(f"安装已验证的 {version} 并重启双声？旧版本会保留。", f"Install verified {version} and restart Shuangsheng? The old version is retained.")
        detail = self.update_supported.get("detail", "")
        if detail:
            confirmation += "\n\n" + detail
        if sys.platform.startswith("linux"):
            confirmation += "\n" + self.update_message("如已安装本机 Fcitx 插件，更新可能短暂中断输入。", "If the native Fcitx plugin is installed, updating may briefly interrupt input.")
        if not messagebox.askyesno(self.t("检查更新…"), confirmation, parent=self.update_window):
            return
        prepared = self.update_downloaded
        self.set_phase("updating")
        for widget in (self.output, self.query_box, self.candidate_box):
            widget.configure(state="disabled")
        self.update_status.set(self.update_message(f"正在安装并启动 {version}…", f"Installing and starting {version}…"))
        self.run_update_operation("install", lambda _generation: self.ensure_updater().install(prepared))

    def handle_update_events(self):
        events = getattr(self, "update_events", None)
        if events is None:
            return
        while True:
            try:
                kind, generation, operation, value = events.get_nowait()
            except queue.Empty:
                break
            if generation != self.update_task_generation:
                continue
            if kind != "progress":
                self.update_operation = None
            if generation != self.update_generation:
                self.render_update_controls()
                continue
            if kind == "progress":
                downloaded, total = value
                self.update_progress.set(min(1, downloaded / total) if total else 0)
                amount = f"{downloaded / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MiB" if total else f"{downloaded / (1024 * 1024):.1f} MiB"
                self.update_status.set(self.update_message(f"正在下载：{amount}", f"Downloading: {amount}"))
            elif kind == "error":
                self.update_status.set(self.update_message(f"更新失败：{value}。原版本仍可使用。", f"Update failed: {value}. The current version remains available."))
                if operation == "install":
                    self.unlock_update_editor()
            elif operation == "check":
                self.update_offer = value
                self.update_status.set(self.update_message(f"发现 {value['version']}，点击下载后验证。" if value else "当前已是最新稳定版。", f"Found {value['version']}. Download to validate." if value else "You already have the latest stable version."))
            elif operation == "download":
                self.update_downloaded = value
                self.update_progress.set(1)
                self.update_status.set(self.update_message("下载、校验和试运行已完成。点击指定版本的安装按钮确认重启。", "Download, validation and smoke test completed. Confirm installation using the version-labelled button."))
            elif operation == "install":
                self.close(force=True)
                return
            self.render_update_controls()

    def make_shortcut(self):
        value = self.settings.get("hotkey", "<ctrl>+<alt>+<space>")
        names = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "cmd": "cmd", "command": "cmd", "shift": "shift", "space": "space"}
        value = "+".join("<" + names[part.lower()] + ">" if part.lower() in names else part.lower() for part in value.split("+"))
        return GlobalShortcut(lambda: self.commands.put("toggle"),
                              hotkey=value,
                              mode="hold" if self.settings.get("push_to_talk", False) else "toggle",
                              on_release=lambda: self.commands.put("stop"))

    def context(self):
        return self.output.get("1.0", "insert")[-500:]

    def persist_preferences(self):
        try:
            self._persist_preferences()
        except (ValueError, OSError, RuntimeError, self.tk.TclError) as exc:
            self.status.set(f"设置未能应用：{exc}")

    def _persist_preferences(self):
        if self.phase != "idle":
            self.status.set("请等待录音或模型操作结束后再应用设置。")
            return
        self.settings.update({
            "language": {"普通话": "zh", "粤语": "yue", "自动检测": "auto", "English": "en"}[self.language.get()],
            "script": "traditional" if self.script.get() == "繁體" else "simplified",
            "microphone": self.device.get() if self.device.get() != "系统默认麦克风" else "",
            "font_size": self.font_size.get(), "candidate_count": self.candidate_count.get(),
            "theme": self.theme.get(), "compact": self.compact.get(), "input_scheme": self.scheme.get(),
            "fuzzy": self.fuzzy.get(), "input_mode": self.input_mode.get(),
            "fuzzy_pairs": [pair.strip().split(":") for pair in self.fuzzy_pairs_value.get().split(",") if pair.strip()], "save_draft": self.save_draft.get(), "auto_insert": self.auto_insert.get(),
            "push_to_talk": self.push_to_talk.get(), "hotkey": self.hotkey_value.get(),
            "overlay": self.overlay_enabled.get(), "locale": self.locale.get(),
            "model": self.settings.get("model", "large-v3"), "device": self.compute_device.get(),
            "backend": self.backend_value.get(), "sensevoice_model": self.sensevoice_model.get(),
            "hotwords": [term.strip() for term in self.terminology.get().split(",") if term.strip()],
            "initial_prompt": self.initial_prompt.get(),
        })
        self.output.configure(font=("TkDefaultFont", self.font_size.get()))
        self.root.geometry("720x570" if self.compact.get() else "900x760")
        self.apply_theme()
        self.apply_locale()
        self.engine.fuzzy_pairs = self.settings.get("fuzzy_pairs")
        self.update_candidates()
        if not self.save_draft.get():
            self.remove_draft()
        self.shortcut.stop()
        self.shortcut = self.make_shortcut()
        if self.shortcut_enabled:
            self.hotkey_status.set(self.shortcut.start())
        if self.service.device != self.compute_device.get():
            self.service.unload()
        self.service.device = self.compute_device.get()
        self.refresh_model_status()
        self.status.set(self.t("设置已保存。"))

    def apply_theme(self):
        dark = self.theme.get() in {"dark", "contrast"}
        contrast = self.theme.get() == "contrast"
        background = "#000000" if contrast else "#20232b" if dark else "#ffffff"
        foreground = "#ffffff" if contrast else "#eceff4" if dark else "#17202a"
        self.output.configure(background=background, foreground=foreground, insertbackground=foreground)
        self.candidate_box.configure(background=background, foreground=foreground,
                                     font=("TkDefaultFont", self.font_size.get()),
                                     selectbackground="#ffff00" if contrast else "#315a94", selectforeground="#000000" if contrast else "#ffffff")

    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        tk, ttk = self.tk, self.ttk
        window = self.settings_window = tk.Toplevel(self.root)
        window.title(self.t("设置 · 本机保存"))
        window.transient(self.root)
        window.geometry(f"530x{min(700, max(400, self.root.winfo_screenheight() - 100))}")
        container = ttk.Frame(window)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0)
        scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        frame = ttk.Frame(canvas, padding=14)
        content = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content, width=event.width))
        def row(label, variable, values):
            line = ttk.Frame(frame)
            line.pack(fill="x", pady=3)
            ttk.Label(line, text=self.t(label), width=14).pack(side="left")
            box = ttk.Combobox(line, textvariable=variable, values=values, state="readonly", width=30)
            box.pack(side="left", fill="x", expand=True)
            return box
        self.language_box = row("语音语言", self.language, ["普通话", "粤语", "自动检测", "English"])
        self.script_box = row("文字", self.script, ["简体", "繁體"])
        row("输入方案", self.scheme, ["pinyin", "shuangpin", "jyutping"])
        row("Chinese / English", self.input_mode, ["chinese", "english"])
        ttk.Label(frame, text="Fuzzy pairs: zh:z, n:l, ang:an" if self.locale.get() == "en" else "模糊音对：zh:z, n:l, ang:an").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.fuzzy_pairs_value).pack(fill="x")
        self.device_box = row("麦克风", self.device, list(self.device_ids))
        self.refresh_button = ttk.Button(frame, text=self.t("刷新麦克风"), command=self.refresh_devices)
        self.refresh_button.pack(anchor="e")
        row("计算设备", self.compute_device, ["cpu", "cuda", "auto"])
        row("语音引擎", self.backend_value, ["whisper", "sensevoice"])
        for label, variable in [("SenseVoice 本地目录", self.sensevoice_model), ("Whisper 专有词（逗号分隔）", self.terminology), ("Whisper 背景提示", self.initial_prompt)]:
            ttk.Label(frame, text=self.t(label)).pack(anchor="w")
            ttk.Entry(frame, textvariable=variable).pack(fill="x")
        row("界面语言", self.locale, ["zh", "en"])
        row("配色", self.theme, ["system", "light", "dark", "contrast"])
        row("字体大小", self.font_size, [10, 12, 14, 16, 18, 22])
        row("候选数量", self.candidate_count, [3, 5, 7, 9])
        ttk.Entry(frame, textvariable=self.hotkey_value).pack(fill="x", pady=3)
        ttk.Label(frame, text=self.t("快捷键格式：Ctrl+Alt+Space 或 <ctrl>+<alt>+d。"), wraplength=420).pack(anchor="w")
        for title, variable in [("紧凑窗口", self.compact), ("模糊拼音", self.fuzzy), ("按住说话（松开识别）", self.push_to_talk),
                                ("录音浮窗（支持 Windows / macOS，不抢焦点）", self.overlay_enabled),
                                ("保存本地草稿用于恢复（包含文本）", self.save_draft),
                                ("自动插入原目标（仅已确认的非密码编辑框；否则留在此处）", self.auto_insert)]:
            ttk.Checkbutton(frame, text=self.t(title), variable=variable).pack(anchor="w", pady=2)
        ttk.Label(frame, textvariable=self.hotkey_status, wraplength=430).pack(anchor="w", pady=4)
        ttk.Label(frame, textvariable=self.model_status, wraplength=430).pack(anchor="w")
        models = ttk.Frame(frame)
        models.pack(fill="x", pady=6)
        self.model_button = ttk.Button(models, text=self.t("选择模型…"), command=self.choose_model)
        self.model_button.pack(side="left")
        self.download_button = ttk.Button(models, text=self.t("下载…"), command=self.download_model)
        self.download_button.pack(side="left", padx=4)
        ttk.Button(models, text=self.t("预热"), command=lambda: self.model_action("warmup")).pack(side="left", padx=4)
        ttk.Button(models, text=self.t("卸载"), command=lambda: self.model_action("unload")).pack(side="left")
        self.rime_button = ttk.Button(frame, text=self.t("安装系统输入方案…"), command=self.install_rime)
        self.rime_button.pack(anchor="w")
        ttk.Button(frame, text="Application profiles…" if self.locale.get() == "en" else "应用配置…", command=self.manage_profiles).pack(anchor="w", pady=4)
        ttk.Button(frame, text=self.t("应用并保存"), command=self.persist_preferences).pack(anchor="e", pady=8)
        self.set_phase(self.phase)

    def refresh_model_status(self):
        state = self.service.status()
        if self.locale.get() == "en":
            readiness = "loaded" if state.get("loaded") else "cached / ready to load" if state.get("ready") else "not ready"
        else:
            readiness = "已加载" if state.get("loaded") else "可加载" if state.get("ready") else "未就绪"
        self.model_status.set(f"{state.get('backend', 'whisper')} · {state['model']} · {readiness} · {state.get('device', self.service.device)}" + (f" · {state['error']}" if state.get("error") else ""))

    def model_action(self, action):
        import threading
        if self.phase != "idle":
            return
        self.set_phase("setup")
        self.status.set("正在预热本地模型…" if action == "warmup" else "正在释放模型…")
        def worker():
            try:
                getattr(self.service, action)()
                self.commands.put(("model_ready", None))
            except Exception as exc:
                self.commands.put(("model_error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="desktop-model").start()

    def hold_press(self, _event):
        if self.push_to_talk.get():
            self.toggle_recording() if self.phase == "idle" else None
            return "break"

    def hold_release(self, _event):
        if self.push_to_talk.get():
            if self.phase in {"starting", "recording"}:
                self.toggle_recording()
            return "break"

    def refresh_devices(self):
        # Device probing can be slow, so it must not block Tk's event loop.
        import threading
        self.refresh_button.configure(state="disabled")

        def worker():
            try:
                self.commands.put(("devices", input_devices()))
            except Exception as exc:
                self.commands.put(("device_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="desktop-devices").start()

    def choose_model(self):
        if self.phase != "idle":
            return
        from tkinter import filedialog
        directory = filedialog.askdirectory(parent=self.root, title="选择已下载的 faster-whisper 模型目录")
        if not directory:
            return
        path = Path(directory)
        if any(not (path / name).is_file() for name in ["model.bin", "config.json", "tokenizer.json"]):
            self.status.set("该目录不是完整的 faster-whisper 模型，需含 model.bin、config.json 和 tokenizer.json。")
            return
        self.settings.update({"model": str(path), "backend": "whisper"})
        self.backend_value.set("whisper")
        self.service = SpeechService(settings=self.settings, lexicon=self.engine.lexicon)
        self.job.service = self.service
        self.refresh_model_status()
        self.status.set("已保存本地模型。下次录音时加载。")

    def download_model(self):
        import threading
        from tkinter import messagebox
        if self.phase != "idle":
            return
        if not messagebox.askyesno(
            "下载本地语音模型", "将从 Hugging Face 下载 large-v3 模型，需要数 GB 磁盘空间和网络流量。"
            "下载后普通话和粤语识别均在本机运行。是否开始？", parent=self.root,
        ):
            return
        self.set_phase("setup")
        self.status.set("正在下载 large-v3 模型。可能需要较长时间；拼音和文本编辑仍可使用。")

        def worker():
            try:
                from .model_setup import download_model_weights
                download_model_weights("large-v3")
                self.commands.put(("model_downloaded", None))
            except Exception:
                self.commands.put(("model_error", "模型下载失败。请检查网络、磁盘空间和 Hugging Face 访问权限后重试。"))

        threading.Thread(target=worker, daemon=True, name="desktop-model-download").start()

    def install_rime(self):
        import threading
        from tkinter import messagebox
        if self.phase != "idle":
            return
        scheme = self.scheme.get()
        scheme_name = {"pinyin": "双声拼音", "shuangpin": "双声小鹤双拼", "jyutping": "双声粤拼"}[scheme]
        try:
            from scripts.install_rime import current_platform, default_user_dir, install
            target = current_platform()
            directory = default_user_dir(target)
            frontend = {"windows": "小狼毫 Weasel", "macos": "鼠须管 Squirrel", "linux": "Fcitx5-Rime"}[target]
        except (ImportError, ValueError, KeyError) as exc:
            self.status.set(f"无法确定 Rime 安装位置：{exc}")
            return
        if not messagebox.askyesno(
            "安装双声系统输入方案", f"请先安装 {frontend}。\n\n"
            f"将当前选择的 {scheme_name}（{scheme}）及共享拼音依赖写入：\n{directory}\n\n"
            "已有文件修改前会备份。完成后需在输入法菜单中重新部署。是否继续？", parent=self.root,
        ):
            return
        self.set_phase("setup")
        self.status.set(f"正在安装 {scheme_name}…")

        def worker():
            try:
                result = install(directory, schemes=[scheme])
                self.commands.put(("rime_installed", (frontend, result, scheme_name)))
            except Exception as exc:
                self.commands.put(("rime_error", f"系统拼音配置安装失败：{exc}"))

        threading.Thread(target=worker, daemon=True, name="desktop-rime-install").start()

    def toggle_input_mode(self):
        mode = "english" if self.input_mode.get() == "chinese" else "chinese"
        try:
            self.settings.update({"input_mode": mode})
            self.input_mode.set(mode)
            self.update_candidates()
            self.status.set("English input" if mode == "english" else "中文输入")
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))

    def schedule_candidates(self, *_):
        if self.query_after is not None:
            self.root.after_cancel(self.query_after)
        self.query_after = self.root.after(100, self.update_candidates)

    def update_candidates(self):
        self.query_after = None
        script = self.active_profile.get("script", "traditional" if self.script.get() == "繁體" else "simplified")
        context = self.context()
        if self.input_mode.get() == "english":
            self.candidates = []
        elif self.query.get():
            try:
                self.candidates = self.engine.candidates(self.query.get(), limit=self.candidate_count.get(), script=script,
                                                        context=context, scheme=self.active_profile.get("input_scheme", self.scheme.get()), fuzzy=self.fuzzy.get())
            except ValueError as exc:
                self.candidates = []
                self.status.set(str(exc))
        else:
            self.candidates = self.engine.predict(context, limit=self.candidate_count.get(), script=script)
        self.candidate_box.delete(0, "end")
        for index, candidate in enumerate(self.candidates, 1):
            self.candidate_box.insert("end", f"{index}.  {candidate['text']}    {candidate.get('pinyin', '')}")
        if self.candidates:
            self.candidate_box.selection_set(0)

    def focus_candidates(self, _event=None):
        if self.candidates:
            self.candidate_box.focus_set()
        return "break"

    def choose_first_candidate(self, _event=None):
        if self.input_mode.get() == "english" or literal_query(self.query.get()):
            return None
        if self.query_after is not None:
            self.root.after_cancel(self.query_after)
            self.query_after = None
        self.update_candidates()
        return self.choose_candidate()

    def number_candidate(self, event, *, force=False):
        if self.input_mode.get() == "english":
            return None
        scheme = self.active_profile.get("input_scheme", self.scheme.get())
        if not force and (scheme == "jyutping" or literal_query(self.query.get()) or self.query.get() in {"v", "r"}):
            return None
        digit = event.char or getattr(event, "keysym", "")
        if digit in "123456789" and digit and self.query.get():
            if self.query_after is not None:
                self.root.after_cancel(self.query_after)
            self.update_candidates()
            index = int(digit) - 1
            if index < len(self.candidates):
                self.candidate_box.selection_clear(0, "end")
                self.candidate_box.selection_set(index)
                self.choose_candidate()
                return "break"

    def choose_candidate(self, _event=None):
        selected = self.candidate_box.curselection()
        if selected and selected[0] < len(self.candidates):
            candidate = self.candidates[selected[0]]["text"]
            query = self.query.get()
            # Context saved to the personal lexicon contains only explicit composition commits,
            # never arbitrary editor drafts or unreviewed automatic dictation output.
            context = self.learning_context if self.context().endswith(self.learning_context) else ""
            self.output.edit_separator()
            self.output.insert("insert", candidate)
            self.output.edit_separator()
            self.output.see("insert")
            if query:
                try:
                    self.engine.learn(query, candidate, context=context)
                except (OSError, ValueError):
                    self.status.set("文字已输入，但个人词库暂时无法保存。")
            self.learning_context = (context + candidate)[-120:]
            self.query.set("")
            self.update_candidates()
            self.query_box.focus_set()
        return "break"

    def commit_raw(self, _event=None):
        value = self.query.get()
        if value:
            context = self.learning_context if self.context().endswith(self.learning_context) else ""
            self.output.edit_separator()
            self.output.insert("insert", value)
            self.learning_context = (context + value)[-120:]
            self.output.edit_separator()
            self.query.set("")
            self.update_candidates()
        return "break"

    def escape(self, _event=None):
        if self.query.get():
            self.query.set("")
        self.cancel_recording()
        return "break"

    def set_phase(self, phase):
        self.phase = phase
        idle = phase == "idle"
        recording = phase in {"starting", "recording"}
        self.record_button.configure(text=self.t("停止并识别" if recording else "开始录音"),
                                     state="normal" if idle or recording else "disabled")
        self.cancel_button.configure(state="normal" if phase in {"starting", "recording", "stopping", "transcribing"} else "disabled")
        for name in ["language_box", "script_box", "device_box", "model_button", "download_button", "rime_button"]:
            widget = getattr(self, name, None)
            if widget is not None and widget.winfo_exists():
                widget.configure(state=("readonly" if name.endswith("_box") else "normal") if idle else "disabled")
        if idle:
            self.level.set(0)
            self.active_profile = {}
            self.refresh_model_status()
        if phase in {"idle", "cancelling", "setup"} and self.overlay is not None:
            self.overlay.hide()

    def toggle_recording(self):
        if self.phase in {"starting", "recording"}:
            self.job.stop()
            self.set_phase("stopping")
            self.status.set("Stopping recording…" if self.locale.get() == "en" else "正在停止录音…")
        elif self.phase == "idle":
            target = self.insertion.capture()
            self.original_target = target if self.auto_insert.get() else None
            self.active_profile = self.target_profile(target)
            if target is not None:
                self.last_application_id = target.application_id
            self.partial.set("")
            language = {"普通话": "zh", "粤语": "yue", "自动检测": "auto", "English": "en"}[self.language.get()]
            language = self.active_profile.get("language", language)
            script = self.active_profile.get("script", "traditional" if self.script.get() == "繁體" else "simplified")
            # The service resolves saved terminology; SenseVoice cannot consume Whisper hints.
            prompt = ((self.settings.get("initial_prompt", "") + " " + self.context()).strip()[:2000]
                      if self.service.status().get("backend") == "faster-whisper" else None)
            if self.job.start(language, script, self.device_ids.get(self.device.get()), initial_prompt=prompt or None):
                self.set_phase("starting")
                self.recorded_at = None
                self.status.set("Opening microphone…" if self.locale.get() == "en" else "正在打开麦克风…")
                if self.overlay_enabled.get():
                    if self.overlay is None:
                        from .desktop_overlay import RecordingOverlay
                        self.overlay = RecordingOverlay(self.root, self.tk, self.ttk)
                    if not self.overlay.show():
                        self.status.set("Opening microphone; overlay unavailable on this platform." if self.locale.get() == "en" else "正在打开麦克风；当前平台无法显示不抢焦点的浮窗。")
                    self.update_overlay()

    def cancel_recording(self):
        if self.phase == "updating":
            return
        if self.phase not in {"idle", "setup"}:
            self.job.cancel()
            self.original_target = None
            self.partial.set("")
            self.set_phase("cancelling")
            self.status.set("Cancelled; wait for local inference to finish before recording again." if self.locale.get() == "en" else "已取消；若正在识别，等待本地模型结束后即可再次录音。")

    def poll(self):
        if self.closed:
            return
        self.handle_update_events()
        if self.closed:
            return
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                break
            if command == "toggle":
                self.toggle_recording()
            elif command == "stop":
                if self.phase in {"starting", "recording"}:
                    self.toggle_recording()
            elif command[0] == "model_ready":
                self.refresh_model_status()
                self.set_phase("idle")
                self.status.set("模型状态已更新。")
            elif command[0] == "devices":
                self.device_ids = {"系统默认麦克风": None}
                self.device_ids.update({f"{index}: {name}": index for index, name in command[1]})
                if getattr(self, "device_box", None) is not None and self.device_box.winfo_exists():
                    self.device_box.configure(values=list(self.device_ids))
                preferred = self.settings.get("microphone", "")
                self.device.set(preferred if preferred in self.device_ids else "系统默认麦克风")
                if getattr(self, "refresh_button", None) is not None and self.refresh_button.winfo_exists():
                    self.refresh_button.configure(state="normal")
                self.status.set(f"找到 {len(command[1])} 个输入设备。")
            elif command[0] == "device_error":
                if getattr(self, "refresh_button", None) is not None and self.refresh_button.winfo_exists():
                    self.refresh_button.configure(state="normal")
                self.status.set(command[1])
            elif command[0] == "model_downloaded":
                self.settings.update({"model": "large-v3", "backend": "whisper"})
                self.backend_value.set("whisper")
                self.service = SpeechService(settings=self.settings, lexicon=self.engine.lexicon)
                self.job.service = self.service
                self.model_status.set("本地语音模型：large-v3")
                self.status.set("large-v3 模型已下载，可以开始录音。")
                self.set_phase("idle")
            elif command[0] == "model_error":
                self.status.set(command[1])
                self.set_phase("idle")
            elif command[0] == "rime_installed":
                from tkinter import messagebox
                frontend, result, scheme_name = command[1]
                self.set_phase("idle")
                self.status.set(f"{scheme_name}已安装，请在 {frontend} 中重新部署。")
                messagebox.showinfo(
                    f"{scheme_name}已安装", f"安装位置：{result['user_dir']}\n"
                    f"备份文件：{len(result['backups'])} 个\n\n"
                    f"请在 {frontend} 菜单中执行“重新部署”，然后使用 Ctrl+` 或 F4 选择“{scheme_name}”。"
                    "\n如果尚未安装对应系统输入法，请先安装并在系统设置中启用。", parent=self.root,
                )
            elif command[0] == "rime_error":
                self.status.set(command[1])
                self.set_phase("idle")
        while True:
            try:
                kind, value = self.job.events.get_nowait()
            except queue.Empty:
                break
            if kind == "recording" and self.phase == "starting":
                self.recorded_at = time.monotonic()
                self.set_phase("recording")
            elif kind == "transcribing" and self.phase != "cancelling":
                self.set_phase("transcribing")
                self.status.set("正在本机识别，首次加载模型可能较慢…")
            elif kind == "partial" and self.phase != "cancelling":
                self.partial.set(value)
            elif kind == "level" and self.phase != "cancelling":
                self.level.set(max(0, min(1, float(value))))
            elif kind == "stage" and self.phase != "cancelling":
                self.status.set(str(value))
            elif kind == "result" and self.phase != "cancelling":
                existing = self.output.get("1.0", "end-1c")
                self.output.insert("end", ("\n" if existing and not existing.endswith("\n") else "") + value)
                self.output.see("end")
                self.partial.set("")
                result = self.insertion.insert(self.original_target, value, enabled=self.auto_insert.get())
                self.original_target = None
                self.status.set(("Paste requested at the original target; text is retained here." if result.inserted else "Transcription ready. Review or copy the text; automatic insertion was not performed.") if self.locale.get() == "en" else ("已向原目标发送粘贴请求；文本也保留在这里。" if result.inserted else "识别完成。" + result.detail + " 可修改或复制文本。"))
            elif kind == "error":
                self.status.set(value)
            elif kind == "cancelled":
                self.status.set("本次录音已取消。")
            elif kind == "idle":
                self.set_phase("idle")
        if self.phase == "recording" and self.recorded_at is not None:
            seconds = min(115, int(time.monotonic() - self.recorded_at))
            self.status.set(f"Recording: {seconds} / 115 seconds. Use the shortcut or Stop." if self.locale.get() == "en" else f"正在录音：{seconds} / 115 秒。再次按快捷键或点击停止并识别。")
        self.update_overlay()
        if self.shortcut.listener is not None and not self.shortcut.listener.is_alive():
            self.shortcut.stop()
            self.hotkey_status.set("全局快捷键已停止；请使用录音按钮并检查系统权限。")
        self.root.after(50, self.poll)

    def copy_text(self):
        text = self.output.get("1.0", "end-1c")
        if not text:
            self.status.set(self.t("还没有可复制的文字。"))
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            self.status.set("已复制。请切换到目标应用粘贴；Linux 下请保持本窗口打开直到粘贴完成。")
        except self.tk.TclError:
            self.status.set("剪贴板暂不可用，文字仍在窗口中。请手动选择复制，或保存文本。")

    def save_text(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.root, title="保存文本", defaultextension=".txt",
                                          filetypes=[("文本文件", "*.txt")])
        if path:
            try:
                Path(path).write_text(self.output.get("1.0", "end-1c"), encoding="utf-8")
                self.saved_text = self.output.get("1.0", "end-1c")
                self.status.set(self.t("文本已保存。"))
            except OSError:
                self.status.set("无法保存，请选择有写入权限的位置。文字仍在窗口中。")

    def clear_text(self):
        self.output.edit_separator()
        self.output.delete("1.0", "end")
        self.learning_context = ""
        self.output.edit_separator()
        self.status.set(self.t("已清空。点击撤销可恢复。"))

    def undo_text(self):
        try:
            self.output.edit_undo()
        except self.tk.TclError:
            self.status.set(self.t("没有可撤销的操作。"))

    @property
    def draft_path(self):
        return Path(self.settings.path).with_name("desktop-draft.txt")

    def remove_draft(self):
        try:
            self.draft_path.unlink(missing_ok=True)
        except OSError:
            self.status.set("无法删除旧草稿，请检查设置目录权限。")

    def text_changed(self, _event=None):
        if not self.output.edit_modified():
            return
        self.output.edit_modified(False)
        if self.draft_after is not None:
            self.root.after_cancel(self.draft_after)
        if self.save_draft.get():
            self.draft_after = self.root.after(700, self.write_draft)

    def write_draft(self):
        self.draft_after = None
        if not self.save_draft.get():
            return
        # Drafts are opt-in plain UTF-8 text, never audio. Replace atomically.
        import os
        import tempfile
        temp_path = None
        try:
            self.draft_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.draft_path.parent,
                                             prefix=".draft-", delete=False) as stream:
                temp_path = Path(stream.name)
                stream.write(self.output.get("1.0", "end-1c"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.draft_path)
        except OSError:
            self.status.set("草稿恢复文件无法保存，文字仍在窗口中。")
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def restore_draft(self):
        if not self.save_draft.get():
            return
        from tkinter import messagebox
        try:
            if self.draft_path.stat().st_size > 5 * 1024 * 1024:
                return
            text = self.draft_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return
        if text and messagebox.askyesno("恢复本地草稿", "恢复上次保存在本机的文本草稿？", parent=self.root):
            self.output.insert("1.0", text)
            self.status.set("已恢复草稿。请保存需要保留的文字。")

    def open_assistant(self):
        from tkinter import messagebox
        import threading
        from .assistant import LocalAssistant
        tk, ttk = self.tk, self.ttk
        original = self.output.get("1.0", "end-1c")
        if not original:
            self.status.set("先输入需要润色、翻译或续写的文字。")
            return
        window = tk.Toplevel(self.root)
        window.title("本地助手 · 预览后应用")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        enabled = tk.BooleanVar(value=self.settings.get("llm_enabled", False))
        url = tk.StringVar(value=self.settings.get("llm_url", "http://127.0.0.1:8080/v1"))
        model = tk.StringVar(value=self.settings.get("llm_model", "local"))
        action = tk.StringVar(value="polish")
        language = tk.StringVar(value="中文")
        ttk.Checkbutton(frame, text=self.t("启用本机助手（将本窗口文本发送到已配置的本机服务）"), variable=enabled).pack(anchor="w")
        for label, variable in [("本机 URL", url), ("模型", model), ("目标语言", language)]:
            ttk.Label(frame, text=self.t(label)).pack(anchor="w")
            ttk.Entry(frame, textvariable=variable).pack(fill="x")
        ttk.Combobox(frame, textvariable=action, values=["polish", "translate", "complete"], state="readonly").pack(fill="x", pady=5)
        preview = tk.Text(frame, height=10, width=60, wrap="word")
        preview.pack(fill="both", expand=True)
        status = tk.StringVar(value="仅在点击生成预览时运行。原文保留直到应用。")
        ttk.Label(frame, textvariable=status, wraplength=480).pack(anchor="w")
        events = queue.Queue()
        result_ready = [False]
        def generate():
            try:
                self.settings.update({"llm_enabled": enabled.get(), "llm_url": url.get(), "llm_model": model.get()})
            except (ValueError, OSError) as exc:
                messagebox.showerror("无法保存助手设置", str(exc), parent=window)
                return
            generate_button.configure(state="disabled")
            apply_button.configure(state="disabled")
            insert_button.configure(state="disabled")
            result_ready[0] = False
            status.set("正在本机生成预览…")
            selected_action, selected_language = action.get(), language.get()
            def worker():
                try:
                    events.put((True, LocalAssistant(self.settings).transform(original, action=selected_action, language=selected_language)))
                except Exception as exc:
                    events.put((False, str(exc)))
            threading.Thread(target=worker, daemon=True, name="desktop-assistant").start()
        def poll():
            if not window.winfo_exists() or self.closed:
                return
            try:
                success, text = events.get_nowait()
            except queue.Empty:
                window.after(100, poll)
                return
            generate_button.configure(state="normal")
            if success:
                preview.delete("1.0", "end")
                preview.insert("1.0", text)
                result_ready[0] = True
                apply_button.configure(state="normal")
                insert_button.configure(state="normal")
                status.set("预览可编辑；点击替换或插入应用。")
            else:
                status.set(text)
            window.after(100, poll)
        def apply(replace):
            if not result_ready[0]:
                return
            if replace and self.output.get("1.0", "end-1c") != original:
                status.set("原文已修改，请重新打开助手，或选择插入预览。")
                return
            self.output.edit_separator()
            if replace:
                self.output.delete("1.0", "end")
            self.output.insert("insert", preview.get("1.0", "end-1c"))
            self.output.edit_separator()
            window.destroy()
            self.status.set("已应用助手预览；可以撤销。")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=6)
        generate_button = ttk.Button(buttons, text=self.t("生成预览"), command=generate)
        generate_button.pack(side="left")
        apply_button = ttk.Button(buttons, text=self.t("替换原文"), command=lambda: apply(True), state="disabled")
        apply_button.pack(side="left", padx=5)
        insert_button = ttk.Button(buttons, text=self.t("插入预览"), command=lambda: apply(False), state="disabled")
        insert_button.pack(side="left")
        window.after(100, poll)

    def manage_profiles(self):
        from tkinter import messagebox
        tk, ttk = self.tk, self.ttk
        window = tk.Toplevel(self.root)
        window.title("Application profiles" if self.locale.get() == "en" else "按应用选择语言 / 输入方案")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        application = tk.StringVar(value=self.last_application_id)
        language = tk.StringVar(value="zh")
        script = tk.StringVar(value="simplified")
        scheme = tk.StringVar(value="pinyin")
        ttk.Label(frame, text="Windows: executable path · macOS: bundle ID" if self.locale.get() == "en" else "Windows 使用可执行文件完整路径；macOS 使用应用 bundle ID。", wraplength=440).pack(anchor="w")
        ttk.Label(frame, text="Last recording target is prefilled." if self.locale.get() == "en" else "已预填最近一次录音的原目标标识。自动插入始终单独控制。", wraplength=440).pack(anchor="w")
        box = ttk.Combobox(frame, textvariable=application, values=list(self.settings.get("app_profiles", {})), width=60)
        box.pack(fill="x", pady=6)
        def load(_event=None):
            profile = self.settings.get("app_profiles", {}).get(application.get(), {})
            language.set(profile.get("language", "zh"))
            script.set(profile.get("script", "simplified"))
            scheme.set(profile.get("input_scheme", "pinyin"))
        box.bind("<<ComboboxSelected>>", load)
        for title, var, values in [("语音语言", language, ["zh", "yue", "en", "auto"]),
                                   ("文字", script, ["simplified", "traditional"]),
                                   ("输入方案", scheme, ["pinyin", "shuangpin", "jyutping"])]:
            ttk.Label(frame, text=self.t(title)).pack(anchor="w")
            ttk.Combobox(frame, textvariable=var, values=values, state="readonly").pack(fill="x")
        def write(delete=False):
            key = application.get().strip()
            if sys.platform == "win32":
                key = key.casefold()
            profiles = self.settings.get("app_profiles", {})
            if delete:
                profiles.pop(key, None)
            else:
                profiles[key] = {"language": language.get(), "script": script.get(), "input_scheme": scheme.get()}
            try:
                self.settings.update({"app_profiles": profiles})
                box.configure(values=list(profiles))
                self.status.set(self.t("设置已保存。"))
            except (ValueError, OSError) as exc:
                messagebox.showerror(self.t("设置…"), str(exc), parent=window)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text=self.t("保存"), command=write).pack(side="left")
        ttk.Button(buttons, text=self.t("删除"), command=lambda: write(True)).pack(side="left", padx=6)
        load()

    def manage_lexicon(self):
        from tkinter import filedialog, messagebox
        tk, ttk = self.tk, self.ttk
        window = tk.Toplevel(self.root)
        window.title("共享个人词库 / 快捷短语")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        listing = tk.Listbox(frame, width=60, height=12, exportselection=False)
        listing.pack(fill="both", expand=True)
        entries = []
        pinyin, shortcut = tk.StringVar(), tk.StringVar()
        pinned = tk.BooleanVar()
        ttk.Label(frame, text=self.t("短语")).pack(anchor="w")
        phrase_text = tk.Text(frame, height=3, width=55, wrap="word", undo=True)
        phrase_text.pack(fill="x")
        for label, var in [("拼音 / 粤拼", pinyin), ("快捷码", shortcut)]:
            ttk.Label(frame, text=self.t(label)).pack(anchor="w")
            ttk.Entry(frame, textvariable=var).pack(fill="x")
        ttk.Checkbutton(frame, text=self.t("置顶"), variable=pinned).pack(anchor="w")
        def refresh():
            entries[:] = self.engine.lexicon.entries()
            listing.delete(0, "end")
            for item in entries:
                listing.insert("end", f"{'★ ' if item['pinned'] else ''}{item['text']}  [{item['pinyin']}]  {item['shortcut']}")
            self.update_candidates()
        def selected(_event=None):
            if listing.curselection():
                item = entries[listing.curselection()[0]]
                phrase_text.delete("1.0", "end")
                phrase_text.insert("1.0", item['text'])
                pinyin.set(item['pinyin'])
                shortcut.set(item['shortcut'])
                pinned.set(item['pinned'])
        listing.bind("<<ListboxSelect>>", selected)
        def upsert():
            try:
                selected_id = entries[listing.curselection()[0]]["id"] if listing.curselection() else None
                self.engine.lexicon.upsert(phrase_text.get("1.0", "end-1c"), pinyin.get(), shortcut.get(), pinned.get(), id=selected_id)
                refresh()
            except (ValueError, OSError) as exc:
                messagebox.showerror("无法保存词条", str(exc), parent=window)
        def new():
            listing.selection_clear(0, "end")
            phrase_text.delete("1.0", "end")
            pinyin.set("")
            shortcut.set("")
            pinned.set(False)
        def delete():
            if listing.curselection():
                try:
                    self.engine.lexicon.delete(entries[listing.curselection()[0]]["id"])
                    refresh()
                except OSError as exc:
                    messagebox.showerror("无法删除", str(exc), parent=window)
        def export():
            path = filedialog.asksaveasfilename(parent=window, defaultextension=".json", filetypes=[("JSON", "*.json")])
            if path:
                try:
                    Path(path).write_text(json.dumps(self.engine.lexicon.entries(), ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError as exc:
                    messagebox.showerror("无法导出", str(exc), parent=window)
        def import_entries():
            path = filedialog.askopenfilename(parent=window, filetypes=[("JSON", "*.json")])
            if not path:
                return
            try:
                if Path(path).stat().st_size > 5 * 1024 * 1024:
                    raise ValueError("词库文件超过 5 MiB。")
                items = json.loads(Path(path).read_text(encoding="utf-8"))
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    raise ValueError("词库应为 JSON 词条数组。")
                # Validate every item in a temporary store before changing the shared lexicon.
                import tempfile
                from .lexicon import LexiconStore
                with tempfile.TemporaryDirectory() as directory:
                    check = LexiconStore(Path(directory) / "lexicon.json")
                    for item in items:
                        check.upsert(item["text"], item.get("pinyin", ""), item.get("shortcut", ""), item.get("pinned", False))
                for item in items:
                    self.engine.lexicon.upsert(item["text"], item.get("pinyin", ""), item.get("shortcut", ""), item.get("pinned", False))
                refresh()
            except (ValueError, OSError, UnicodeError, KeyError) as exc:
                messagebox.showerror("无法导入", str(exc), parent=window)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=6)
        for label, action in [("新词条", new), ("保存", upsert), ("删除", delete), ("导入…", import_entries), ("导出…", export)]:
            ttk.Button(buttons, text=self.t(label), command=action).pack(side="left", padx=3)
        refresh()

    def close(self, *, force=False):
        from tkinter import messagebox
        if not force and getattr(self, "update_operation", None) == "install":
            self.status.set(self.update_message("正在安装并启动新版本，请等待完成。", "Installing and starting the new version; please wait."))
            return
        discarded = False
        if not force and self.output.get("1.0", "end-1c") != self.saved_text:
            answer = messagebox.askyesnocancel(self.t("关闭前保存"), self.t("文本尚未保存。保存后关闭？选择“否”放弃本次文本。"), parent=self.root)
            if answer is None:
                return
            discarded = answer is False
            if answer:
                self.save_text()
                if self.output.get("1.0", "end-1c") != self.saved_text:
                    return
        if discarded:
            self.remove_draft()
        elif self.save_draft.get():
            self.write_draft()
        self.closed = True
        self.update_generation = getattr(self, "update_generation", 0) + 1
        self.job.cancel()
        self.shortcut.stop()
        if self.overlay is not None:
            self.overlay.close()
        self.root.destroy()


def smoke_test():
    engine = PinyinEngine()
    for query, text, script in [("nihao", "你好", "simplified"), ("zhongguo", "中國", "traditional")]:
        if not any(candidate["text"] == text for candidate in engine.candidates(query, script=script)):
            raise RuntimeError("Bundled Chinese dictionary validation failed")
    pcm_to_wav(array("h", [100] * 4000).tobytes(), 16000)
    result = {"desktop": "ok", "dictionary": engine.info, "speech": SpeechService().status()}
    print(json.dumps(result, ensure_ascii=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true", help="Check bundled assets without opening devices or windows")
    parser.add_argument("--gui-smoke-test", action="store_true", help="Open Tk and exercise composition/clipboard without audio, downloads, or global hotkeys")
    parser.add_argument("--no-hotkey", action="store_true", help="Disable global keyboard listening")
    args = parser.parse_args(argv)
    if args.smoke_test:
        return smoke_test()
    try:
        import tkinter as tk
        root = tk.Tk()
    except ImportError:
        print("Tk is unavailable. Install python3-tk (Linux), or use the packaged desktop release.", file=sys.stderr)
        return 1
    except tk.TclError:
        print("Cannot open the desktop window. Run from a graphical desktop session.", file=sys.stderr)
        return 1
    app = DesktopApp(root, hotkey=not args.no_hotkey and not args.gui_smoke_test)
    failures = []
    if args.gui_smoke_test:
        def verify_window():
            try:
                app.query.set("nihao")
                app.choose_first_candidate()
                if app.output.get("1.0", "end-1c") != "你好":
                    raise RuntimeError("Desktop pinyin selection failed")
                app.copy_text()
                if root.clipboard_get() != "你好":
                    raise RuntimeError("Desktop clipboard copy failed")
                app.set_phase("recording")
                app.open_settings()
                if str(app.model_button["state"]) != "disabled":
                    raise RuntimeError("Desktop recording controls failed")
                app.set_phase("idle")
                print(json.dumps({"desktop_gui": "ok", "tk": root.tk.call("info", "patchlevel")}))
            except Exception as exc:
                failures.append(str(exc))
                print(f"Desktop GUI validation failed: {exc}", file=sys.stderr)
            finally:
                app.close(force=True)
        root.after(150, verify_window)
    root.mainloop()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
