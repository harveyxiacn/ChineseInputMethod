"""Opt-in insertion into a captured native editable target.

Unknown controls, secure fields, unavailable accessibility, and Linux fall back
 to review/copy. Never activates applications or changes focus. macOS key events
 cannot provide an atomic OS-level focus-and-paste transaction; recheck immediately
 before each event and keep all results in the editor for recovery.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import sys


@dataclass(frozen=True)
class Target:
    platform: str
    app_id: int
    control_id: object
    sensitive: bool = False
    window_id: int = 0
    application_id: str = ""


@dataclass(frozen=True)
class InsertionResult:
    inserted: bool
    detail: str


class InsertionAdapter:
    def __init__(self, platform=None, backend=None):
        self.platform = platform or sys.platform
        self.backend = backend
        if backend is None:
            try:
                if self.platform == "win32":
                    self.backend = WindowsBackend()
                elif self.platform == "darwin":
                    self.backend = MacOSBackend()
            except (ImportError, OSError, AttributeError):
                self.backend = None

    def capture(self):
        if self.backend is None or self.platform not in {"win32", "darwin"}:
            return None
        try:
            target = self.backend.capture()
            if target is not None and not target.sensitive and target.app_id != os.getpid():
                return target
        except Exception:
            pass
        return None

    def insert(self, target, text, *, enabled=False):
        if not enabled:
            return InsertionResult(False, "自动插入未启用。")
        if not isinstance(text, str) or not text or "\0" in text:
            return InsertionResult(False, "没有可插入的文字。")
        if target is None or target.sensitive or self.capture() != target:
            return InsertionResult(False, "原目标不可用、受保护或焦点已改变。请检查后复制。")
        try:
            if self.backend.paste(target, text):
                return InsertionResult(True, "已发送粘贴请求。")
        except Exception:
            pass
        return InsertionResult(False, "系统未接受粘贴，请检查后复制。")


class WindowsBackend:
    """Only standard native Edit/RichEdit controls, never unknown app widgets."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.user = c.WinDLL("user32", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        class GUIThreadInfo(c.Structure):
            _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD), ("hwndActive", w.HWND),
                        ("hwndFocus", w.HWND), ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                        ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND), ("rcCaret", w.RECT)]
        self.GUIThreadInfo = GUIThreadInfo
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        self.user.GetWindowThreadProcessId.restype = w.DWORD
        self.user.GetGUIThreadInfo.argtypes = [w.DWORD, c.POINTER(GUIThreadInfo)]
        self.user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        self.user.GetWindowLongW.argtypes = [w.HWND, c.c_int]
        self.user.GetWindowLongW.restype = c.c_long
        self.user.IsWindowEnabled.argtypes = [w.HWND]
        self.user.IsWindowVisible.argtypes = [w.HWND]
        self.user.OpenClipboard.argtypes = [w.HWND]
        self.user.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
        self.user.SetClipboardData.restype = w.HANDLE
        self.kernel.GlobalAlloc.argtypes = [w.UINT, c.c_size_t]
        self.kernel.GlobalAlloc.restype = w.HANDLE
        self.kernel.GlobalLock.argtypes = [w.HANDLE]
        self.kernel.GlobalLock.restype = c.c_void_p
        self.kernel.GlobalUnlock.argtypes = [w.HANDLE]
        self.kernel.GlobalFree.argtypes = [w.HANDLE]
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.user.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, c.c_size_t, c.c_ssize_t,
                                                 w.UINT, w.UINT, c.POINTER(c.c_size_t)]
        self.user.SendMessageTimeoutW.restype = c.c_ssize_t

    def capture(self):
        c, w, user = self.c, self.w, self.user
        window = user.GetForegroundWindow()
        pid = w.DWORD()
        thread = user.GetWindowThreadProcessId(window, c.byref(pid))
        info = self.GUIThreadInfo()
        info.cbSize = c.sizeof(info)
        if not window or not thread or not user.GetGUIThreadInfo(thread, c.byref(info)):
            return None
        control = info.hwndFocus
        control_pid = w.DWORD()
        user.GetWindowThreadProcessId(control, c.byref(control_pid))
        if control_pid.value != pid.value or int(info.hwndActive or 0) != int(window):
            return None
        name = c.create_unicode_buffer(256)
        user.GetClassNameW(control, name, len(name))
        if name.value.lower() not in {"edit", "richedit", "richedit20w", "richedit50w"}:
            return None
        style = user.GetWindowLongW(control, -16)
        # Password / read-only controls or invisible/disabled targets are never eligible.
        if style & (0x20 | 0x800) or not user.IsWindowEnabled(control) or not user.IsWindowVisible(control):
            return None
        return Target("win32", pid.value, int(control), window_id=int(window), application_id=self.application_id(pid.value))

    def application_id(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            buffer = self.c.create_unicode_buffer(32768)
            length = self.w.DWORD(len(buffer))
            if self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, self.c.byref(length)):
                return buffer.value.casefold()
            return ""
        finally:
            self.kernel.CloseHandle(handle)

    def paste(self, target, text):
        c, user, kernel = self.c, self.user, self.kernel
        if self.capture() != target:
            return False
        # Clipboard owns allocation only after SetClipboardData succeeds.
        payload = text.encode("utf-16-le") + b"\0\0"
        handle = kernel.GlobalAlloc(0x2, len(payload))
        if not handle:
            return False
        owned = True
        opened = False
        try:
            address = kernel.GlobalLock(handle)
            if not address:
                return False
            c.memmove(address, payload, len(payload))
            kernel.GlobalUnlock(handle)
            if self.capture() != target or not user.OpenClipboard(target.window_id):
                return False
            opened = True
            if not user.EmptyClipboard() or not user.SetClipboardData(13, handle):
                return False
            owned = False
            user.CloseClipboard()
            opened = False
            if self.capture() != target:
                return False
            # Targeted WM_PASTE avoids a global-key focus race and never activates a window.
            result = c.c_size_t()
            return bool(user.SendMessageTimeoutW(target.control_id, 0x302, 0, 0, 0x2, 1000, c.byref(result)))
        finally:
            if opened:
                user.CloseClipboard()
            if owned:
                kernel.GlobalFree(handle)


class MacOSBackend:
    def __init__(self):
        import ApplicationServices as ax
        import AppKit
        import Quartz
        self.ax, self.appkit, self.quartz = ax, AppKit, Quartz

    def attr(self, element, name):
        error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if error == 0 else None

    def capture(self):
        ax = self.ax
        if not ax.AXIsProcessTrusted():
            return None
        system = ax.AXUIElementCreateSystemWide()
        app = self.attr(system, "AXFocusedApplication")
        if app is None:
            return None
        error, pid = ax.AXUIElementGetPid(app, None)
        element = self.attr(app, "AXFocusedUIElement")
        if error != 0 or element is None:
            return None
        role, subrole = self.attr(element, "AXRole"), self.attr(element, "AXSubrole")
        if role not in {"AXTextField", "AXTextArea"} or subrole == "AXSecureTextField":
            return None
        if self.attr(element, "AXEnabled") != True:
            return None
        error, editable = ax.AXUIElementIsAttributeSettable(element, "AXValue", None)
        if error != 0 or not editable:
            return None
        application = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        application_id = str(application.bundleIdentifier() or application.executableURL().path()) if application else ""
        return Target("darwin", int(pid), element, application_id=application_id)

    def paste(self, target, text):
        if self.capture() != target:
            return False
        board = self.appkit.NSPasteboard.generalPasteboard()
        board.clearContents()
        if not board.setString_forType_(text, self.appkit.NSPasteboardTypeString):
            return False
        if self.capture() != target:
            return False
        q = self.quartz
        # Post to the captured process, rather than globally; recheck focus before posting.
        down = q.CGEventCreateKeyboardEvent(None, 9, True)  # ANSI V
        up = q.CGEventCreateKeyboardEvent(None, 9, False)
        if down is None or up is None:
            return False
        q.CGEventSetFlags(down, q.kCGEventFlagMaskCommand)
        q.CGEventSetFlags(up, q.kCGEventFlagMaskCommand)
        if self.capture() != target:
            return False
        q.CGEventPostToPid(target.app_id, down)
        q.CGEventPostToPid(target.app_id, up)
        return True
