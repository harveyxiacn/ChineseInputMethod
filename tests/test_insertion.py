import os
import unittest
from unittest.mock import Mock

from ime.insertion import InsertionAdapter, Target, MacOSBackend, WindowsBackend


class InsertionTests(unittest.TestCase):
    def setUp(self):
        self.target = Target("win32", os.getpid() + 1000, 42, window_id=99)
        self.backend = Mock()
        self.backend.capture.return_value = self.target
        self.backend.paste.return_value = True
        self.adapter = InsertionAdapter("win32", self.backend)

    def test_disabled_does_not_touch_target_or_clipboard(self):
        self.assertFalse(self.adapter.insert(self.target, "你好").inserted)
        self.backend.capture.assert_not_called()
        self.backend.paste.assert_not_called()

    def test_same_target_pastes_exact_text(self):
        result = self.adapter.insert(self.target, "你好\n世界", enabled=True)
        self.assertTrue(result.inserted)
        self.backend.paste.assert_called_once_with(self.target, "你好\n世界")

    def test_changed_control_or_app_or_window_never_pastes(self):
        for changed in [Target("win32", self.target.app_id, 43, window_id=99),
                        Target("win32", self.target.app_id + 1, 42, window_id=99),
                        Target("win32", self.target.app_id, 42, window_id=100)]:
            with self.subTest(target=changed):
                self.backend.capture.return_value = changed
                self.assertFalse(self.adapter.insert(self.target, "私密", enabled=True).inserted)
                self.backend.paste.assert_not_called()

    def test_sensitive_target_and_sensitive_current_target_fail(self):
        secure = Target("win32", self.target.app_id, 42, sensitive=True)
        self.assertFalse(self.adapter.insert(secure, "私密", enabled=True).inserted)
        self.backend.capture.return_value = secure
        self.assertIsNone(self.adapter.capture())
        self.backend.paste.assert_not_called()

    def test_own_application_is_not_external_target(self):
        self.backend.capture.return_value = Target("win32", os.getpid(), 42)
        self.assertIsNone(self.adapter.capture())

    def test_linux_never_calls_backend(self):
        adapter = InsertionAdapter("linux", self.backend)
        self.assertIsNone(adapter.capture())
        self.assertFalse(adapter.insert(self.target, "你好", enabled=True).inserted)
        self.backend.capture.assert_not_called()
        self.backend.paste.assert_not_called()

    def test_missing_target_and_capture_errors_retain_text(self):
        self.assertFalse(self.adapter.insert(None, "你好", enabled=True).inserted)
        self.backend.capture.side_effect = RuntimeError("permission")
        self.assertIsNone(self.adapter.capture())
        self.assertFalse(self.adapter.insert(self.target, "你好", enabled=True).inserted)
        self.backend.paste.assert_not_called()

    def test_paste_error_and_false_are_recoverable(self):
        self.backend.paste.return_value = False
        self.assertFalse(self.adapter.insert(self.target, "你好", enabled=True).inserted)
        self.backend.paste.side_effect = OSError()
        self.assertFalse(self.adapter.insert(self.target, "你好", enabled=True).inserted)

    def test_empty_and_non_string_text_rejected(self):
        for text in ["", None, 42, "secret\0truncated"]:
            self.assertFalse(self.adapter.insert(self.target, text, enabled=True).inserted)
        self.backend.paste.assert_not_called()

    def test_macos_focus_change_after_clipboard_does_not_emit_keys(self):
        backend = MacOSBackend.__new__(MacOSBackend)
        backend.capture = Mock(side_effect=[self.target, None])
        backend.appkit = Mock()
        backend.appkit.NSPasteboard.generalPasteboard.return_value.setString_forType_.return_value = True
        backend.quartz = Mock()
        self.assertFalse(backend.paste(self.target, "你好"))
        backend.quartz.CGEventPostToPid.assert_not_called()

    def test_windows_focus_change_before_clipboard_does_not_allocate(self):
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.capture = Mock(return_value=None)
        backend.c, backend.user, backend.kernel = Mock(), Mock(), Mock()
        self.assertFalse(backend.paste(self.target, "你好"))
        backend.kernel.GlobalAlloc.assert_not_called()

    def test_windows_focus_changes_after_clipboard_no_paste_and_clipboard_keeps_owned_handle(self):
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.capture = Mock(side_effect=[self.target, self.target, None])
        backend.c, backend.user, backend.kernel = Mock(), Mock(), Mock()
        backend.kernel.GlobalAlloc.return_value = 100
        backend.kernel.GlobalLock.return_value = 200
        backend.user.OpenClipboard.return_value = True
        backend.user.EmptyClipboard.return_value = True
        backend.user.SetClipboardData.return_value = 100
        self.assertFalse(backend.paste(self.target, "你好"))
        backend.user.SendMessageTimeoutW.assert_not_called()
        backend.user.CloseClipboard.assert_called_once()
        backend.kernel.GlobalFree.assert_not_called()

    def test_windows_failed_clipboard_open_frees_unowned_allocation(self):
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.capture = Mock(return_value=self.target)
        backend.c, backend.user, backend.kernel = Mock(), Mock(), Mock()
        backend.kernel.GlobalAlloc.return_value = 100
        backend.kernel.GlobalLock.return_value = 200
        backend.user.OpenClipboard.return_value = False
        self.assertFalse(backend.paste(self.target, "你好"))
        backend.kernel.GlobalFree.assert_called_once_with(100)
        backend.user.SendMessageTimeoutW.assert_not_called()

    def test_macos_secure_or_unknown_or_readonly_control_not_captured(self):
        for role, subrole, editable in [("AXTextField", "AXSecureTextField", True),
                                        ("AXWebArea", None, True), ("AXTextArea", None, False)]:
            with self.subTest(role=role, subrole=subrole, editable=editable):
                backend = MacOSBackend.__new__(MacOSBackend)
                backend.ax = Mock()
                backend.ax.AXIsProcessTrusted.return_value = True
                backend.ax.AXUIElementGetPid.return_value = (0, self.target.app_id)
                backend.ax.AXUIElementIsAttributeSettable.return_value = (0, editable)
                backend.attr = Mock(side_effect=[object(), object(), role, subrole, True])
                self.assertIsNone(backend.capture())

    def test_macos_capture_provides_stable_bundle_id_separate_from_pid(self):
        backend = MacOSBackend.__new__(MacOSBackend)
        backend.ax, backend.appkit = Mock(), Mock()
        backend.ax.AXIsProcessTrusted.return_value = True
        backend.ax.AXUIElementGetPid.return_value = (0, self.target.app_id)
        backend.ax.AXUIElementIsAttributeSettable.return_value = (0, True)
        element = object()
        backend.attr = Mock(side_effect=[object(), element, "AXTextArea", None, True])
        backend.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_.return_value.bundleIdentifier.return_value = "org.example.editor"
        captured = backend.capture()
        self.assertEqual(captured.app_id, self.target.app_id)
        self.assertIs(captured.control_id, element)
        self.assertEqual(captured.application_id, "org.example.editor")

    def test_windows_application_id_failure_closes_process_handle(self):
        from ctypes import wintypes
        import ctypes
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.kernel = Mock()
        backend.c, backend.w = ctypes, wintypes
        backend.kernel.OpenProcess.return_value = 100
        backend.kernel.QueryFullProcessImageNameW.return_value = False
        self.assertEqual(backend.application_id(self.target.app_id), "")
        backend.kernel.CloseHandle.assert_called_once_with(100)


if __name__ == "__main__":
    unittest.main()
