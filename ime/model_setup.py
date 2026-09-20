"""Explicit, opt-in download of local speech weights."""
import argparse
import os

from .paths import configure_model_cache


def download_model_weights(model="large-v3"):
    """Network access only when explicitly called by the setup CLI or GUI."""
    configure_model_cache()
    from faster_whisper.utils import download_model
    return download_model(model)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.getenv("IME_WHISPER_MODEL", "large-v3"))
    args = parser.parse_args(argv)
    cache = configure_model_cache()
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        parser.exit(1, "Install requirements.txt in a Python 3.12 virtual environment first.\n")
    print(f"Downloading {args.model} into {cache}. Weights may require several GB.", flush=True)
    try:
        path = download_model_weights(args.model)
    except Exception as exc:
        parser.exit(1, f"Model download failed: {exc}\nCheck your connection and available disk space, then retry.\n")
    print(f"Model ready at {path}. Recognition can now run offline.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
