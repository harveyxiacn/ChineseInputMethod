"""Explicitly download speech weights before starting the offline service."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ime.model_setup import main


if __name__ == "__main__":
    raise SystemExit(main())
