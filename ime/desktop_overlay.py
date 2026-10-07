"""Optional recording HUD. Native nonactivating windows on Windows/macOS only.

Linux uses the main window meter/transcript: Tk cannot promise a nonactivating
HUD across Wayland compositors. Failure to create a HUD never changes recording.
"""
from __future__ import annotations
import sys


class RecordingOverlay:
    def __init__(self, root, tk, ttk, platform=None):
        self.root, self.tk, self.ttk = root, tk, ttk
        self.platform = platform or sys.platform
        self.window = self.panel = None
        self.label = None
        self.available = self.platform in {"win32", "darwin"}

    def show(self):
        if not self.available:
            return False
        try:
            if self.platform == "darwin":
                self._show_mac()
            else:
                self._show_windows()
            return True
        except Exception:
            self.available = False
            self.hide()
            return False

    def _show_windows(self):
        import ctypes as c
        from ctypes import wintypes as w
        window = self.window
        if window is None:
            window = self.window = self.tk.Toplevel(self.root)
            window.withdraw()
            window.overrideredirect(True)
            window.geometry("360x92+30+30")
            self.label = self.ttk.Label(window, text="", wraplength=340, padding=12)
            self.label.pack(fill="both", expand=True)
            window.update_idletasks()
        user = c.WinDLL("user32", use_last_error=True)
        user.GetParent.argtypes = [w.HWND]
        user.GetParent.restype = w.HWND
        hwnd = user.GetParent(window.winfo_id()) or window.winfo_id()
        get_style = getattr(user, "GetWindowLongPtrW", user.GetWindowLongW)
        set_style = getattr(user, "SetWindowLongPtrW", user.SetWindowLongW)
        get_style.argtypes, get_style.restype = [w.HWND, c.c_int], c.c_ssize_t
        set_style.argtypes, set_style.restype = [w.HWND, c.c_int, c.c_ssize_t], c.c_ssize_t
        user.SetWindowPos.argtypes = [w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT]
        style = get_style(hwnd, -20)
        # NOACTIVATE | TOOLWINDOW: no focus, no taskbar item, no keyboard input.
        set_style(hwnd, -20, style | 0x08000000 | 0x80)
        window.deiconify()
        user.SetWindowPos(hwnd, -1, 30, 30, 360, 92, 0x0010 | 0x0040)

    def _show_mac(self):
        import AppKit as a
        if self.panel is None:
            panel = self.panel = a.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                a.NSMakeRect(30, 30, 360, 92), a.NSWindowStyleMaskBorderless | a.NSWindowStyleMaskNonactivatingPanel,
                a.NSBackingStoreBuffered, False)
            panel.setReleasedWhenClosed_(False)
            panel.setLevel_(a.NSFloatingWindowLevel)
            panel.setHidesOnDeactivate_(False)
            panel.setIgnoresMouseEvents_(True)
            label = self.label = a.NSTextField.alloc().initWithFrame_(a.NSMakeRect(12, 10, 336, 72))
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            panel.contentView().addSubview_(label)
        self.panel.orderFrontRegardless()

    def update(self, stage, partial, level):
        if self.label is None:
            return
        value = f"{stage}  {'▰' * round(max(0, min(1, level)) * 10)}\n{partial[-120:]}"
        if self.platform == "darwin":
            self.label.setStringValue_(value)
        else:
            self.label.configure(text=value)

    def hide(self):
        if self.panel is not None:
            self.panel.orderOut_(None)
        if self.window is not None:
            self.window.withdraw()

    def close(self):
        self.hide()
        if self.window is not None:
            self.window.destroy()
            self.window = None
        if self.panel is not None:
            self.panel.close()
            self.panel = None
