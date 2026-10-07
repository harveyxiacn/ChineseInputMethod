#!/usr/bin/env python3
"""Evaluate authored input cases and explicitly supplied local audio offline."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
from types import SimpleNamespace
import wave

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ime.evaluation import NORMALIZATION, aggregate_scores, score_transcript


def read_manifest(path):
    path = Path(path).resolve()
    with path.open("rb") as handle:
        data = handle.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Manifest must be at most 1 MiB")
    value = json.loads(data)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("cases"), list):
        raise ValueError("Manifest needs schema_version=1 and a cases list")
    if len(value["cases"]) > 200:
        raise ValueError("Manifest is limited to 200 explicitly selected cases")
    seen = set()
    for case in value["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"] or case["id"] in seen:
            raise ValueError("Every case needs a unique nonempty string id")
        seen.add(case["id"])
    return path.parent, value["cases"]


def timed(callback):
    start = time.perf_counter()
    result = callback()
    return result, time.perf_counter() - start


def evaluate_candidates(path, top_k=9, include_text=False):
    from ime.lexicon import LexiconStore
    from ime.pinyin import PinyinEngine
    _, cases = read_manifest(path)
    rows = []
    # Isolate personal terms/learning so fixtures never read or change them.
    with tempfile.TemporaryDirectory(prefix="shuangsheng-evaluation-") as directory:
        engine, setup_seconds = timed(lambda: PinyinEngine(lexicon=LexiconStore(Path(directory) / "lexicon.json")))
        for case in cases:
            if not isinstance(case.get("query"), str) or not isinstance(case.get("expected"), str):
                raise ValueError("Input cases need query and expected text")
            candidates, cold = timed(lambda: engine.candidates(case["query"], top_k, case.get("script", "simplified"),
                scheme=case.get("scheme", "pinyin"), context=case.get("context", ""), fuzzy=case.get("fuzzy", False)))
            warm_candidates, warm = timed(lambda: engine.candidates(case["query"], top_k, case.get("script", "simplified"),
                scheme=case.get("scheme", "pinyin"), context=case.get("context", ""), fuzzy=case.get("fuzzy", False)))
            texts = [candidate["text"] for candidate in candidates]
            rank = texts.index(case["expected"]) + 1 if case["expected"] in texts else None
            row = {"id": case["id"], "scheme": case.get("scheme", "pinyin"),
                   "first_candidate_correct": rank == 1, "top_k_correct": rank is not None,
                   "rank": rank, "candidate_count": len(candidates),
                   "first_query_seconds": cold, "warm_query_seconds": warm,
                   "repeat_consistent": candidates == warm_candidates}
            if include_text:
                row.update(query=case["query"], expected=case["expected"], candidates=texts)
            rows.append(row)
    return {"backend": engine.info, "setup_seconds": setup_seconds, "top_k": top_k,
            "cases": rows, "summary": {"cases": len(rows),
                "first_candidate_accuracy": sum(row["first_candidate_correct"] for row in rows) / len(rows) if rows else None,
                "top_k_accuracy": sum(row["top_k_correct"] for row in rows) / len(rows) if rows else None}}


def live_timings(service, audio, language, script, hints):
    from ime.live_speech import LiveTranscript, MIN_PREVIEW, RATE, wav_bytes
    try:
        with wave.open(io.BytesIO(audio), "rb") as handle:
            if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (RATE, 1, 2):
                return {"available": False, "reason": "Live timing needs a 16 kHz mono PCM16 WAV"}
            pcm = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError):
        return {"available": False, "reason": "Live timing needs a PCM WAV"}
    started = time.perf_counter()
    first = None
    finalizing = False

    def emit(text):
        nonlocal first
        if text and first is None and not finalizing:
            first = time.perf_counter() - started

    live = LiveTranscript(service, language, script, emit, **hints)
    total = len(pcm) // 2
    for end in range(MIN_PREVIEW, total, MIN_PREVIEW):
        live.update(wav_bytes(pcm[:end * 2]))
    finalizing = True
    _, final_seconds = timed(lambda: live.update(wav_bytes(pcm), final=True))
    return {"available": True, "method": "snapshot replay without realtime microphone capture",
            "model_state": "warm", "snapshot_interval_seconds": MIN_PREVIEW / RATE,
            "first_preview_compute_seconds": first, "stop_to_final_compute_seconds": final_seconds}


def evaluate_audio(path, *, model="large-v3", device="cpu", backend="whisper", sensevoice_model="", include_text=False):
    from ime.lexicon import LexiconStore
    from ime.speech import MAX_AUDIO_BYTES, SpeechService
    base, cases = read_manifest(path)
    rows, scores = [], []
    config = {"model": model, "device": device, "backend": backend,
              "sensevoice_model": sensevoice_model, "hotwords": [], "initial_prompt": ""}
    with tempfile.TemporaryDirectory(prefix="shuangsheng-evaluation-") as directory:
        service = SpeechService(settings=SimpleNamespace(snapshot=lambda: dict(config)),
                                lexicon=LexiconStore(Path(directory) / "lexicon.json"))
        for case in cases:
            if not isinstance(case.get("path"), str) or not isinstance(case.get("reference"), str):
                raise ValueError("Audio cases need a local path and reference text")
            if len(case["reference"]) > 10000:
                raise ValueError("Reference transcripts are limited to 10000 characters")
            score_transcript(case["reference"], "", case.get("terms", []))
            if type(case.get("live", False)) is not bool:
                raise ValueError("live must be a boolean")
            source = Path(case["path"]).expanduser()
            if not source.is_absolute():
                source = base / source
            with source.open("rb") as handle:
                audio = handle.read(MAX_AUDIO_BYTES + 1)
            if not audio or len(audio) > MAX_AUDIO_BYTES:
                raise ValueError("Audio must be nonempty and at most 20 MiB")
            language, script = case.get("language", "auto"), case.get("script", "original")
            hints = {key: case[key] for key in ("hotwords", "initial_prompt") if key in case}
            # Release model ownership before the cold call; OS disk caches remain.
            service.unload()
            cold, cold_seconds = timed(lambda: service.transcribe(audio, language, script, **hints))
            warm, warm_seconds = timed(lambda: service.transcribe(audio, language, script, **hints))
            score = score_transcript(case["reference"], warm["text"], case.get("terms", []))
            scores.append(score)
            row = {"id": case["id"], "language": language, "script": script,
                   "detected_language": warm.get("language"), **score,
                   "cold_seconds": cold_seconds, "warm_seconds": warm_seconds,
                   "cold_definition": "model unloaded; filesystem cache retained; includes decoding and inference",
                   "cold_warm_agree": cold["text"] == warm["text"],
                   "live_timing": live_timings(service, audio, language, script, hints) if case.get("live", False)
                                  else {"available": False, "reason": "Case did not request live snapshot replay"}}
            if include_text:
                row.update(reference=case["reference"], transcript=warm["text"], terms=case.get("terms", []))
            rows.append(row)
        state = service.status()
        service.unload()
    return {"backend": state["backend"], "model": state["model"], "device": state["device"],
            "cases": rows, "summary": aggregate_scores(scores)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "benchmarks" / "examples.json")
    parser.add_argument("--audio", type=Path, help="Explicit local audio/reference manifest; otherwise no speech inference")
    parser.add_argument("--top-k", type=int, default=9)
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
    parser.add_argument("--backend", choices=["whisper", "sensevoice"], default="whisper")
    parser.add_argument("--sensevoice-model", default="")
    parser.add_argument("--include-text", action="store_true", help="Include queries/references/transcripts in the report")
    parser.add_argument("--output", type=Path, help="Write JSON report here instead of stdout")
    args = parser.parse_args(argv)
    if not 1 <= args.top_k <= 50:
        parser.error("--top-k must be between 1 and 50")
    try:
        report = {"schema_version": 1, "normalization": NORMALIZATION,
                  "environment": {"python": platform.python_version(), "system": platform.system(),
                                  "release": platform.release(), "architecture": platform.machine(),
                                  "cpu": platform.processor() or "unavailable", "logical_cpus": os.cpu_count(),
                                  "gpu": "not probed; selected ASR device is reported when audio is requested"},
                  "input": evaluate_candidates(args.cases, args.top_k, args.include_text)}
        if args.audio:
            previous_logging = logging.root.manager.disable
            try:
                if not args.include_text:
                    logging.disable(logging.CRITICAL)
                # Model libraries print status/progress; keep stdout valid JSON
                # and suppress Python diagnostic text unless explicitly selected.
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                        sys.stderr if args.include_text else io.StringIO()):
                    report["speech"] = evaluate_audio(args.audio, model=args.model, device=args.device,
                        backend=args.backend, sensevoice_model=args.sensevoice_model, include_text=args.include_text)
            finally:
                logging.disable(previous_logging)
        else:
            report["speech"] = {"available": False, "reason": "No explicit local audio manifest supplied"}
        rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except Exception as exc:
        # Backend diagnostics can include transcript snippets; keep CLI failures
        # private unless the user explicitly opted into text reporting.
        parser.exit(1, f"Evaluation failed ({type(exc).__name__})" + (f": {exc}" if args.include_text else "; use --include-text for details") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
