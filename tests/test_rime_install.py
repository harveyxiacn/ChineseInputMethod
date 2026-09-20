"""Verify native Rime installation preserves real-world user configuration."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts import install_rime


class RimeInstallTests(unittest.TestCase):
    def test_installs_complete_isolated_dictionary_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory) / "Rime 空格"
            first = install_rime.install(user)
            self.assertIn("shuangsheng.schema.yaml", first["changed"])
            self.assertGreater((user / "shuangsheng_essay.txt").stat().st_size, 5_000_000)
            dictionary = (user / "shuangsheng.dict.yaml").read_text(encoding="utf-8")
            self.assertIn("name: shuangsheng\n", dictionary)
            self.assertIn("vocabulary: shuangsheng_essay\n", dictionary)
            self.assertIn("Rime Developers", dictionary)
            self.assertNotIn("name: luna_pinyin\n", dictionary)
            self.assertEqual(install_rime.install(user)["changed"], [])
            self.assertFalse(list(user.glob("*.before-shuangsheng-*")))
            self.assertFalse((user / "luna_pinyin.dict.yaml").exists())

    def test_existing_lists_preferences_and_backups_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory)
            before = ("# my comments\ncustomization: {generator: User}\npatch:\n"
                      "  schema_list: [{schema: luna_pinyin}, {schema: custom}]\n"
                      "  schema_list/+: [{schema: another}]\n"
                      "  menu/page_size: 5\n  switcher/hotkeys: [Control+grave]\n").encode()
            config = user / "default.custom.yaml"
            config.write_bytes(before)
            existing_dict = user / "luna_pinyin.dict.yaml"
            existing_dict.write_bytes(b"custom user's dictionary")
            result = install_rime.install(user)
            after = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertEqual(after["patch"]["schema_list"], [{"schema": "luna_pinyin"}, {"schema": "custom"}])
            self.assertEqual(after["patch"]["schema_list/+"], [{"schema": "another"}, {"schema": "shuangsheng"}])
            self.assertEqual(after["patch"]["menu/page_size"], 5)
            self.assertEqual(after["customization"], {"generator": "User"})
            self.assertEqual(Path(result["backups"][0]).read_bytes(), before)
            self.assertEqual(existing_dict.read_bytes(), b"custom user's dictionary")
            self.assertEqual(install_rime.install(user)["changed"], [])

    def test_schema_already_registered_preserves_config_bytes(self):
        for original in (b'# Comment\npatch: {schema_list: [{schema: shuangsheng}]}\n',
                         b'patch: {"schema_list/@next": {schema: shuangsheng}}\n',
                         b'patch: {"schema_list/+": [{schema: shuangsheng}]}\n'):
            with self.subTest(original=original):
                self.assertEqual(install_rime.merge_schema_config(original), original)

    def test_yaml_anchors_and_merge_keys_keep_user_values(self):
        original = b'base: &base {"menu/page_size": 5}\npatch:\n  <<: *base\n  "menu/page_size": 9\n'
        merged = yaml.safe_load(install_rime.merge_schema_config(original))
        self.assertEqual(merged["patch"]["menu/page_size"], 9)
        self.assertEqual(merged["patch"]["schema_list/+"], [{"schema": "shuangsheng"}])

    def test_invalid_yaml_never_partially_installs(self):
        for before in (b"patch: [broken", b"patch: []\n", b"- list\n", b"patch: {}\npatch: {}\n",
                       b'patch: {"schema_list/+": bad}\n', b"\xff"):
            with self.subTest(before=before), tempfile.TemporaryDirectory() as directory:
                user = Path(directory)
                config = user / "default.custom.yaml"
                config.write_bytes(before)
                with self.assertRaises(ValueError):
                    install_rime.install(user)
                self.assertEqual(config.read_bytes(), before)
                self.assertEqual(list(user.iterdir()), [config])

    def test_dry_run_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory) / "new"
            result = install_rime.install(user, dry_run=True)
            self.assertIn("default.custom.yaml", result["changed"])
            self.assertFalse(user.exists())

    def test_failed_write_rolls_back_files(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory)
            original = b"# keep\npatch: {}\n"
            (user / "default.custom.yaml").write_bytes(original)
            real_write = install_rime.atomic_write

            def fail_dictionary(path, content):
                if path.name == "shuangsheng.dict.yaml":
                    raise OSError("Simulated disk failure")
                real_write(path, content)

            with patch.object(install_rime, "atomic_write", side_effect=fail_dictionary):
                with self.assertRaises(OSError):
                    install_rime.install(user)
            self.assertFalse((user / "shuangsheng.schema.yaml").exists())
            self.assertEqual((user / "default.custom.yaml").read_bytes(), original)

    def test_symlink_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory)
            original = user / "original"
            original.write_bytes(b"preserve me")
            try:
                (user / "shuangsheng.schema.yaml").symlink_to(original)
            except OSError:
                self.skipTest("Symlinks unavailable to this Windows account")
            with self.assertRaises(ValueError):
                install_rime.install(user)
            self.assertEqual(original.read_bytes(), b"preserve me")
            self.assertFalse((user / "default.custom.yaml").exists())

    def test_os_data_directories(self):
        with patch.object(install_rime.Path, "home", return_value=Path("/home/test")):
            self.assertEqual(install_rime.default_user_dir("macos"), Path("/home/test/Library/Rime"))
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/xdg"}):
            self.assertEqual(install_rime.default_user_dir("linux"), Path("/tmp/xdg/fcitx5/rime"))
        # Avoid consulting the actual Windows user's registry in a portable test.
        with patch.object(install_rime, "windows_user_dir", return_value=Path("Rime custom")):
            self.assertEqual(install_rime.default_user_dir("windows"), Path("Rime custom"))


if __name__ == "__main__":
    unittest.main()
