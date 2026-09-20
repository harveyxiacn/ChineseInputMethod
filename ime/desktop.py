"""Native cross-platform Chinese composition and local dictation companion."""
from __future__ import annotations

import argparse
from array import array
import json
import queue
from pathlib import Path
import sys
import time

from .desktop_voice import DictationJob, GlobalShortcut, input_devices, pcm_to_wav
from .pinyin import PinyinEngine
from .speech import SpeechService


class DesktopApp:
    def __init__(self, root, *, hotkey=True):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.root.title("双声 · 中文输入与本地听写")
        self.root.geometry("820x680")
        self.root.minsize(660, 570)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.closed = False
        self.phase = "idle"
        self.recorded_at = None
        self.device_ids = {"系统默认麦克风": None}
        self.commands = queue.Queue()
        self.service = SpeechService()
        self.job = DictationJob(self.service)
        self.engine = PinyinEngine()
        self.shortcut = GlobalShortcut(lambda: self.commands.put("toggle"))
        self.query_after = None
        self.candidates = []

        self.language = tk.StringVar(value="普通话")
        self.script = tk.StringVar(value="简体")
        self.device = tk.StringVar(value="系统默认麦克风")
        self.query = tk.StringVar()
        self.status = tk.StringVar(value="准备就绪。可输入拼音，或点击开始录音。")
        self.hotkey_status = tk.StringVar(value="全局快捷键已禁用；请使用录音按钮。")
        self.model_status = tk.StringVar(value=f"本地语音模型：{self.service.model_name}")

        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="双声", font=("TkDefaultFont", 22, "bold")).pack(anchor="w")
        ttk.Label(frame, text="普通话 / 粤语听写 · 拼音候选 · 简繁输出").pack(anchor="w", pady=(2, 12))

        options = ttk.Frame(frame)
        options.pack(fill="x")
        ttk.Label(options, text="语音").pack(side="left")
        self.language_box = ttk.Combobox(options, textvariable=self.language, state="readonly",
                                        values=["普通话", "粤语", "自动检测"], width=12)
        self.language_box.pack(side="left", padx=(6, 18))
        ttk.Label(options, text="文字").pack(side="left")
        self.script_box = ttk.Combobox(options, textvariable=self.script, state="readonly",
                                      values=["简体", "繁體"], width=10)
        self.script_box.pack(side="left", padx=6)
        self.script_box.bind("<<ComboboxSelected>>", lambda _event: self.update_candidates())
        self.rime_button = ttk.Button(options, text="安装系统拼音…", command=self.install_rime)
        self.rime_button.pack(side="right")

        microphone = ttk.Frame(frame)
        microphone.pack(fill="x", pady=(10, 4))
        ttk.Label(microphone, text="麦克风").pack(side="left")
        self.device_box = ttk.Combobox(microphone, textvariable=self.device,
                                      values=list(self.device_ids), state="readonly")
        self.device_box.pack(side="left", fill="x", expand=True, padx=6)
        self.refresh_button = ttk.Button(microphone, text="刷新设备", command=self.refresh_devices)
        self.refresh_button.pack(side="right")

        recording = ttk.Frame(frame)
        recording.pack(fill="x", pady=6)
        self.record_button = ttk.Button(recording, text="开始录音", command=self.toggle_recording)
        self.record_button.pack(side="left")
        self.cancel_button = ttk.Button(recording, text="取消", command=self.cancel_recording, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Label(recording, text="最长 115 秒；停止后在本机识别。").pack(side="left")
        ttk.Label(frame, textvariable=self.hotkey_status, wraplength=760).pack(anchor="w")

        model = ttk.Frame(frame)
        model.pack(fill="x", pady=(8, 10))
        ttk.Label(model, textvariable=self.model_status, wraplength=540).pack(side="left", fill="x", expand=True)
        self.model_button = ttk.Button(model, text="选择本地模型", command=self.choose_model)
        self.model_button.pack(side="right")
        self.download_button = ttk.Button(model, text="下载语音模型…", command=self.download_model)
        self.download_button.pack(side="right", padx=6)

        pinyin = ttk.LabelFrame(frame, text="拼音输入", padding=8)
        pinyin.pack(fill="x")
        self.query_box = ttk.Entry(pinyin, textvariable=self.query)
        self.query_box.pack(fill="x")
        self.query_box.bind("<Return>", self.choose_first_candidate)
        self.query_box.bind("<Down>", self.focus_candidates)
        self.query.trace_add("write", self.schedule_candidates)
        self.candidate_box = tk.Listbox(pinyin, height=4, exportselection=False, activestyle="dotbox")
        self.candidate_box.pack(fill="x", pady=(6, 0))
        self.candidate_box.bind("<Double-Button-1>", self.choose_candidate)
        self.candidate_box.bind("<Return>", self.choose_candidate)

        ttk.Label(frame, text="文本（可编辑；双击候选或回车选词）").pack(anchor="w", pady=(10, 4))
        output_frame = ttk.Frame(frame)
        output_frame.pack(fill="both", expand=True)
        self.output = tk.Text(output_frame, height=7, wrap="word", undo=True, font=("TkDefaultFont", 12))
        self.output.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(output_frame, command=self.output.yview)
        scrollbar.pack(side="right", fill="y")
        self.output.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(10, 5))
        ttk.Button(actions, text="复制全部", command=self.copy_text).pack(side="left")
        ttk.Button(actions, text="保存文本…", command=self.save_text).pack(side="left", padx=8)
        ttk.Button(actions, text="清空", command=lambda: self.output.delete("1.0", "end")).pack(side="left")
        ttk.Label(frame, text="识别后请复制文字，再切换到目标应用粘贴。音频只在内存中处理。",
                  wraplength=760).pack(anchor="w")
        ttk.Label(frame, textvariable=self.status, wraplength=760).pack(anchor="w", pady=(6, 0))
        self.root.bind("<Escape>", lambda _event: self.cancel_recording())
        if hotkey:
            self.hotkey_status.set(self.shortcut.start())
        self.root.after(50, self.poll)
        self.query_box.focus_set()

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
        from tkinter import filedialog
        directory = filedialog.askdirectory(parent=self.root, title="选择已下载的 faster-whisper 模型目录")
        if not directory:
            return
        path = Path(directory)
        if not (path / "model.bin").is_file() or not (path / "config.json").is_file():
            self.status.set("该目录不是 faster-whisper 模型，请选择含 model.bin 和 config.json 的目录。")
            return
        self.service = SpeechService()
        self.service.model_name = str(path)
        self.job.service = self.service
        self.model_status.set(f"本地语音模型：{path.name}")
        self.status.set("已选择本地模型（本次会话）。下次录音时加载。")

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
        try:
            from scripts.install_rime import current_platform, default_user_dir, install
            target = current_platform()
            directory = default_user_dir(target)
            frontend = {"windows": "小狼毫 Weasel", "macos": "鼠须管 Squirrel", "linux": "Fcitx5-Rime"}[target]
        except (ImportError, ValueError, KeyError) as exc:
            self.status.set(f"无法确定 Rime 安装位置：{exc}")
            return
        if not messagebox.askyesno(
            "安装双声系统拼音", f"请先安装 {frontend}。\n\n"
            f"将双声拼音方案写入：\n{directory}\n\n"
            "已有文件修改前会备份。完成后需在输入法菜单中重新部署。是否继续？", parent=self.root,
        ):
            return
        self.set_phase("setup")
        self.status.set("正在安装双声拼音方案…")

        def worker():
            try:
                result = install(directory)
                self.commands.put(("rime_installed", (frontend, result)))
            except Exception as exc:
                self.commands.put(("rime_error", f"系统拼音配置安装失败：{exc}"))

        threading.Thread(target=worker, daemon=True, name="desktop-rime-install").start()

    def schedule_candidates(self, *_):
        if self.query_after is not None:
            self.root.after_cancel(self.query_after)
        self.query_after = self.root.after(150, self.update_candidates)

    def update_candidates(self):
        self.query_after = None
        script = "traditional" if self.script.get() == "繁體" else "simplified"
        self.candidates = self.engine.candidates(self.query.get(), script=script)
        self.candidate_box.delete(0, "end")
        for index, candidate in enumerate(self.candidates, 1):
            self.candidate_box.insert("end", f"{index}.  {candidate['text']}    {candidate['pinyin']}")
        if self.candidates:
            self.candidate_box.selection_set(0)

    def focus_candidates(self, _event=None):
        if self.candidates:
            self.candidate_box.focus_set()
        return "break"

    def choose_first_candidate(self, _event=None):
        # A fast Enter must use the current query, not the previous debounce result.
        if self.query_after is not None:
            self.root.after_cancel(self.query_after)
            self.query_after = None
        self.update_candidates()
        return self.choose_candidate()

    def choose_candidate(self, _event=None):
        selected = self.candidate_box.curselection()
        if selected and selected[0] < len(self.candidates):
            self.output.insert("insert", self.candidates[selected[0]]["text"])
            self.output.see("insert")
            self.query.set("")
            self.query_box.focus_set()
        return "break"

    def set_phase(self, phase):
        self.phase = phase
        idle = phase == "idle"
        recording = phase in {"starting", "recording"}
        self.record_button.configure(text="停止并识别" if recording else "开始录音",
                                     state="normal" if idle or recording else "disabled")
        self.cancel_button.configure(state="normal" if phase in {"starting", "recording", "stopping", "transcribing"} else "disabled")
        for widget in [self.language_box, self.script_box, self.device_box]:
            widget.configure(state="readonly" if idle else "disabled")
        self.model_button.configure(state="normal" if idle else "disabled")
        self.download_button.configure(state="normal" if idle else "disabled")
        self.rime_button.configure(state="normal" if idle else "disabled")

    def toggle_recording(self):
        if self.phase in {"starting", "recording"}:
            self.job.stop()
            self.set_phase("stopping")
            self.status.set("正在停止录音…")
        elif self.phase == "idle":
            language = {"普通话": "zh", "粤语": "yue", "自动检测": "auto"}[self.language.get()]
            script = "traditional" if self.script.get() == "繁體" else "simplified"
            if self.job.start(language, script, self.device_ids.get(self.device.get())):
                self.set_phase("starting")
                self.recorded_at = None
                self.status.set("正在打开麦克风…")

    def cancel_recording(self):
        if self.phase not in {"idle", "setup"}:
            self.job.cancel()
            self.set_phase("cancelling")
            self.status.set("已取消；若正在识别，等待本地模型结束后即可再次录音。")

    def poll(self):
        if self.closed:
            return
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                break
            if command == "toggle":
                self.toggle_recording()
            elif command[0] == "devices":
                self.device_ids = {"系统默认麦克风": None}
                self.device_ids.update({f"{index}: {name}": index for index, name in command[1]})
                self.device_box.configure(values=list(self.device_ids))
                self.device.set("系统默认麦克风")
                self.refresh_button.configure(state="normal")
                self.status.set(f"找到 {len(command[1])} 个输入设备。")
            elif command[0] == "device_error":
                self.refresh_button.configure(state="normal")
                self.status.set(command[1])
            elif command[0] == "model_downloaded":
                self.service = SpeechService()
                self.service.model_name = "large-v3"
                self.job.service = self.service
                self.model_status.set("本地语音模型：large-v3")
                self.status.set("large-v3 模型已下载，可以开始录音。")
                self.set_phase("idle")
            elif command[0] == "model_error":
                self.status.set(command[1])
                self.set_phase("idle")
            elif command[0] == "rime_installed":
                from tkinter import messagebox
                frontend, result = command[1]
                self.set_phase("idle")
                self.status.set(f"双声拼音方案已安装，请在 {frontend} 中重新部署。")
                messagebox.showinfo(
                    "双声拼音方案已安装", f"安装位置：{result['user_dir']}\n"
                    f"备份文件：{len(result['backups'])} 个\n\n"
                    f"请在 {frontend} 菜单中执行“重新部署”，然后使用 Ctrl+` 或 F4 选择“双声拼音”。"
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
            elif kind == "result" and self.phase != "cancelling":
                existing = self.output.get("1.0", "end-1c")
                self.output.insert("end", ("\n" if existing and not existing.endswith("\n") else "") + value)
                self.output.see("end")
                self.status.set("识别完成。可修改文本，再点击复制。")
            elif kind == "error":
                self.status.set(value)
            elif kind == "cancelled":
                self.status.set("本次录音已取消。")
            elif kind == "idle":
                self.set_phase("idle")
        if self.phase == "recording" and self.recorded_at is not None:
            seconds = min(115, int(time.monotonic() - self.recorded_at))
            self.status.set(f"正在录音：{seconds} / 115 秒。再次按快捷键或点击停止并识别。")
        if self.shortcut.listener is not None and not self.shortcut.listener.is_alive():
            self.shortcut.stop()
            self.hotkey_status.set("全局快捷键已停止；请使用录音按钮并检查系统权限。")
        self.root.after(50, self.poll)

    def copy_text(self):
        text = self.output.get("1.0", "end-1c")
        if not text:
            self.status.set("还没有可复制的文字。")
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
                self.status.set("文本已保存。")
            except OSError:
                self.status.set("无法保存，请选择有写入权限的位置。文字仍在窗口中。")

    def close(self):
        self.closed = True
        self.job.cancel()
        self.shortcut.stop()
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
                if str(app.model_button["state"]) != "disabled":
                    raise RuntimeError("Desktop recording controls failed")
                app.set_phase("idle")
                print(json.dumps({"desktop_gui": "ok", "tk": root.tk.call("info", "patchlevel")}))
            except Exception as exc:
                failures.append(str(exc))
                print(f"Desktop GUI validation failed: {exc}", file=sys.stderr)
            finally:
                app.close()
        root.after(150, verify_window)
    root.mainloop()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
