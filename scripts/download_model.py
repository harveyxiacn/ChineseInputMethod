"""Explicitly download speech weights before starting the offline service."""
import argparse
import os
from pathlib import Path

os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent.parent / ".cache" / "huggingface"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.getenv("IME_WHISPER_MODEL", "large-v3"))
    args = parser.parse_args()
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        parser.exit(1, "Install requirements.txt in a Python 3.11/3.12 virtual environment first.\n")
    print(f"Downloading {args.model}. Model weights may require several GB.", flush=True)
    path = download_model(args.model)
    print(f"Model ready at {path}\nStart with: python -m ime.server")


if __name__ == "__main__":
    main()
