import unittest
from unittest.mock import Mock, patch

from ime.desktop_overlay import RecordingOverlay


class OverlayTests(unittest.TestCase):
    def test_linux_falls_back_without_creating_window(self):
        root, tk, ttk = Mock(), Mock(), Mock()
        overlay = RecordingOverlay(root, tk, ttk, "linux")
        self.assertFalse(overlay.show())
        overlay.update("recording", "preview", .5)
        overlay.hide()
        overlay.close()
        tk.Toplevel.assert_not_called()

    def test_windows_noactivate_style_set_before_show(self):
        import ctypes
        tk, ttk = Mock(), Mock()
        window = tk.Toplevel.return_value
        window.winfo_id.return_value = 99
        user = Mock()
        user.GetParent.return_value = 100
        user.GetWindowLongPtrW.return_value = 0
        calls = Mock()
        calls.attach_mock(user.SetWindowLongPtrW, "style")
        calls.attach_mock(window.deiconify, "show")
        with patch.object(ctypes, "WinDLL", return_value=user, create=True):
            overlay = RecordingOverlay(Mock(), tk, ttk, "win32")
            self.assertTrue(overlay.show())
        self.assertEqual(user.SetWindowLongPtrW.call_args.args, (100, -20, 0x08000000 | 0x80))
        self.assertEqual([call[0] for call in calls.mock_calls], ["style", "show"])
        self.assertEqual(user.SetWindowPos.call_args.args[-1], 0x0010 | 0x0040)
        window.focus_set.assert_not_called()
        window.focus_force.assert_not_called()
        overlay.update("recording", "你好", 0.7)
        self.assertIn("你好", ttk.Label.return_value.configure.call_args.kwargs["text"])
        overlay.hide()
        window.withdraw.assert_called()

    def test_failed_overlay_is_graceful(self):
        tk = Mock()
        tk.Toplevel.side_effect = RuntimeError("unavailable")
        overlay = RecordingOverlay(Mock(), tk, Mock(), "win32")
        self.assertFalse(overlay.show())
        self.assertFalse(overlay.available)

    def test_macos_panel_is_nonactivating_and_never_made_key(self):
        import sys
        appkit = Mock()
        appkit.NSWindowStyleMaskBorderless = 0
        appkit.NSWindowStyleMaskNonactivatingPanel = 128
        appkit.NSFloatingWindowLevel = 3
        appkit.NSBackingStoreBuffered = 2
        panel = appkit.NSPanel.alloc.return_value.initWithContentRect_styleMask_backing_defer_.return_value
        with patch.dict(sys.modules, {"AppKit": appkit}):
            overlay = RecordingOverlay(Mock(), Mock(), Mock(), "darwin")
            self.assertTrue(overlay.show())
        init = appkit.NSPanel.alloc.return_value.initWithContentRect_styleMask_backing_defer_
        self.assertEqual(init.call_args.args[1], 128)
        panel.setIgnoresMouseEvents_.assert_called_once_with(True)
        panel.orderFrontRegardless.assert_called_once()
        panel.makeKeyAndOrderFront_.assert_not_called()
        overlay.update("recording", "hello", .2)
        self.assertIn("hello", overlay.label.setStringValue_.call_args.args[0])
        overlay.close()
        panel.orderOut_.assert_called_once()
        panel.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
