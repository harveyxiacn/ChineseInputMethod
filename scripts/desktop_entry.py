"""Executable entry point; administrative commands also work without a GUI."""
from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path
import sys

if not getattr(sys, "frozen", False) and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def verify_runtime() -> dict:
    """Exercise bundled binary libraries and VAD without a model or microphone."""
    import wave
    from ime.speech import SpeechService
    from ime.conversion import convert_text

    modules = ("tkinter", "sounddevice", "faster_whisper", "ctranslate2",
               "av", "onnxruntime", "opencc", "yaml")
    for module in modules:
        importlib.import_module(module)
    import ctranslate2
    compute_types = sorted(ctranslate2.get_supported_compute_types("cpu"))
    assert "int8" in compute_types, "The CPU inference backend does not support the configured int8 mode"
    if sys.platform == "darwin":
        import HIServices
        import Quartz
        assert callable(HIServices.AXIsProcessTrusted), "macOS accessibility bindings missing"
    # The real backend needs desktop permissions/display. Check the common
    # keyboard code here; interactive platform testing is documented separately.
    os.environ["PYNPUT_BACKEND"] = "dummy"
    importlib.import_module("pynput.keyboard")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    waveform = SpeechService._decode(audio.getvalue())
    from faster_whisper.vad import get_speech_timestamps
    assert get_speech_timestamps(waveform) == [], "Silence incorrectly detected as speech"
    assert convert_text("汉语", "traditional") == "漢語", "OpenCC resources missing"
    import ime
    root = Path(ime.__file__).resolve().parents[1]
    assert (root / "static" / "index.html").is_file(), "Browser assets missing"
    assert (root / "native" / "rime" / "shuangsheng.schema.yaml").is_file(), "Rime schema missing"
    assert (root / "ime" / "data" / "luna_pinyin.dict.yaml").is_file(), "Rime dictionary missing"
    return {"libraries": list(modules), "audio_samples": len(waveform), "vad": "passed",
            "cpu_compute_types": compute_types}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # Windowed macOS/Windows launchers can have no standard streams.
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    if args and args[0] == "--install-rime":
        from scripts.install_rime import main as install
        return install(args[1:])
    if args and args[0] == "--download-model":
        from ime.model_setup import main as download
        return download(args[1:])
    if args and args[0] == "--serve":
        from ime.server import main as serve
        return serve(args[1:])
    if args and args[0] == "--smoke-test":
        from ime.desktop import main as desktop
        result = desktop(["--smoke-test"])
        if result:
            return result
        checks = verify_runtime()
        if len(args) > 1:
            Path(args[1]).write_text(json.dumps(checks, indent=2), encoding="utf-8")
        print(json.dumps(checks))
        return 0
    from ime.desktop import main as desktop
    return desktop(args)


if __name__ == "__main__":
    raise SystemExit(main())
