from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import types

from ime.paths import model_home
from ime.model_setup import main


class ModelPathsTests(unittest.TestCase):
    def test_explicit_cache_wins_on_every_platform(self):
        for platform in ("win32", "darwin", "linux"):
            self.assertEqual(model_home(frozen=True, platform=platform,
                                       environ={"HF_HOME": "/custom/models"}), Path("/custom/models"))

    def test_packaged_models_are_outside_install_directory(self):
        for platform, relative in (("win32", "AppData/Local"),
                                   ("darwin", "Library/Caches"), ("linux", ".cache")):
            with self.subTest(platform=platform):
                self.assertEqual(model_home(frozen=True, platform=platform, environ={}, home="/user"),
                                 Path("/user") / relative / "shuangsheng/huggingface")

    def test_platform_cache_overrides(self):
        for platform, key in (("win32", "LOCALAPPDATA"), ("linux", "XDG_CACHE_HOME")):
            self.assertEqual(model_home(frozen=True, platform=platform, environ={key: "/cache"}),
                             Path("/cache/shuangsheng/huggingface"))

    def test_source_keeps_existing_weights(self):
        self.assertEqual(model_home(frozen=False, environ={}),
                         Path(__file__).resolve().parents[1] / ".cache/huggingface")

    def test_download_uses_explicit_model_and_shared_cache(self):
        module = types.ModuleType("faster_whisper.utils")
        module.download_model = MagicMock(return_value="/cache/model")
        with patch.dict("sys.modules", {"faster_whisper.utils": module}), \
                patch("ime.model_setup.configure_model_cache", return_value=Path("/cache")), \
                patch("builtins.print"):
            self.assertEqual(main(["--model", "large-v3"]), 0)
        module.download_model.assert_called_once_with("large-v3")
