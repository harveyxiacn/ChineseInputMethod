"""Writable model storage, shared by source and packaged entry points."""
import os
from pathlib import Path
import sys


def model_home(*, frozen=None, platform=None, environ=None, home=None):
    env = os.environ if environ is None else environ
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]).expanduser()
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        # Preserve existing source installations and downloaded weights.
        return Path(__file__).resolve().parents[1] / ".cache" / "huggingface"
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if platform == "win32":
        cache = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif platform == "darwin":
        cache = home / "Library" / "Caches"
    else:
        cache = Path(env.get("XDG_CACHE_HOME") or home / ".cache")
    return cache / "shuangsheng" / "huggingface"


def configure_model_cache():
    """Call before importing huggingface_hub, which reads settings at import."""
    os.environ.setdefault("HF_HOME", str(model_home()))
    return Path(os.environ["HF_HOME"])
