"""Chinese script conversion, deliberately without lossy fallback tables."""
from functools import lru_cache


class ConversionError(ValueError):
    pass


@lru_cache(maxsize=2)
def _converter(script):
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise ConversionError(
            "Chinese script conversion requires opencc-python-reimplemented. "
            "Install the project requirements or choose original script."
        ) from exc
    return OpenCC("t2s" if script == "simplified" else "s2t")


def convert_text(text: str, script: str = "simplified") -> str:
    if script not in {"simplified", "traditional", "original"}:
        raise ConversionError("script must be simplified, traditional, or original")
    if script == "original" or not text:
        return text
    return _converter(script).convert(text)
