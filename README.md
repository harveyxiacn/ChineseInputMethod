# 双声 · Shuangsheng

A local Chinese input pad with pinyin typing and Mandarin/Cantonese dictation.
The browser provides the editor; a Python service performs all input processing
on your machine. No API keys or hosted speech service are used.

## Run

Pinyin works immediately with Python 3.10+ and the bundled dictionary:

```bash
python3 -m ime.server
```

Open **http://localhost:8765**. Type `nihao`, `zhongguo`, `woaini`, or
`jintiantianqi` in the pinyin field. Space selects the
highlighted candidate, 1–9 selects by number, arrows browse, Escape clears the
composition, and Enter inserts the literal spelling. Apostrophes can separate
syllables (`xi'an`); initials (`nh`) and pasted tone marks are supported.
Choose simplified or traditional output. Click within the document to position
the insertion cursor. Copy or download the finished text.

## Enable local voice

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/download_model.py
python -m ime.server
```

The dependencies and model need internet access during setup. The default
Whisper **large-v3** download is approximately 3 GB and is stored under
`.cache/huggingface` in this project. `HF_HOME` can override that location.
After setup, the server loads only cached weights and transcription works
offline. The first recording loads the model and takes longer. CPU inference
uses int8; allow several GB of available RAM. Long recordings can take longer
than their duration on CPU. Python 3.11/3.12 are conservative choices for binary
dependency availability; this workspace was also tested with Python 3.14.

Select **Mandarin** (`zh`) or **Cantonese** (`yue`), start recording, and stop to
transcribe. You can instead upload WAV, MP3, M4A, Ogg, FLAC, or WebM audio.
Browser recording requires microphone permission and localhost or HTTPS.
Uploads are limited to 16 MiB and audio to 120 seconds. Choose the language
explicitly for short clips; automatic detection is less reliable and can
detect languages beyond Chinese. Transcription preserves speech in Chinese;
OpenCC applies the requested character style afterward.

For an NVIDIA GPU with the CUDA/cuDNN dependencies required by faster-whisper:

```bash
IME_WHISPER_DEVICE=cuda python -m ime.server
```

`IME_WHISPER_MODEL` can select a different faster-whisper model name or local
model directory. Download the same selected model before starting. Use a
multilingual v3 model for Cantonese; older Whisper models do not have the
dedicated `yue` token. Missing weights, unsupported languages, invalid files,
and busy inference return visible errors. There is no cloud fallback.

## Design and scope

This version is a browser input pad, not an installed operating-system keyboard.
It does not inject text into other applications; use copy/paste. A native IME
would add an IBus/Fcitx5 adapter on Linux, TSF on Windows, or InputMethodKit on
macOS, reusing the candidate and transcription services.

- `static/`: responsive, keyboard-accessible editor; no frontend build required.
- `ime/pinyin.py`: indexed dictionary lookup, syllable normalization, prefix and
  initials completion, and bounded beam search for combined phrases.
- `ime/data/`: over 70,000 source dictionary rows, with explicit character forms,
  original source, and license texts. Runtime deduplication reduces entry count.
- `ime/speech.py`: lazy faster-whisper inference, Mandarin/Cantonese selection,
  voice activity detection, duration checks, and one transcription at a time.
- `ime/conversion.py`: OpenCC simplified/traditional conversion.
- `ime/server.py`: standard-library HTTP service restricted to localhost and
  same-origin requests. No audio persistence or document storage.

Pinyin ranking uses basic dictionary weights and a small authored common-word
overlay; it does not reproduce Rime's statistical ranking or learn your habits.
Speech is transcribed after recording stops, not streamed. Whisper can make
mistakes, especially with accents, noise, code-switching, or short Cantonese
clips; review results before using them. Text exists only in the current tab
and is lost on reload unless copied or downloaded.

## Open-source foundations

| Project | Use here | License |
| --- | --- | --- |
| [Rime Luna Pinyin](https://github.com/rime/rime-luna-pinyin) | Bundled source dictionary and derived TSV | LGPL-3.0; source and terms in `ime/data/` |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Local inference using CTranslate2 | MIT |
| [Whisper](https://github.com/openai/whisper) | Multilingual speech model | MIT |
| [OpenCC Python](https://github.com/yichen0831/opencc-python) | Simplified/traditional conversion | Apache-2.0 |
| [librime](https://github.com/rime/librime) | Future native engine option; not embedded | BSD-3-Clause |

Dictionary provenance and regeneration instructions are in
[ime/data/ATTRIBUTION.md](ime/data/ATTRIBUTION.md). The importer can regenerate
the bundled dictionary using `python scripts/build_dictionary.py`. An explicit
`--download` refreshes upstream source. Keep its license and attribution with
any redistributed dictionary. Dependency licenses remain applicable separately.

Original application code is released under the [MIT license](LICENSE).
The bundled Rime dictionary retains its upstream LGPL terms. See
[CONTRIBUTING.md](CONTRIBUTING.md) to contribute.

## Verify

```bash
python -m unittest discover -v
node --check static/app.js
```

Unit and HTTP integration tests cover pinyin conversion/segmentation, binary
uploads, request validation, speech-language forwarding, concurrency, offline
model loading, and conversion failures. Speech unit tests use model doubles;
they do not establish real-world recognition accuracy. HTTP tests bind a
temporary localhost port. The optional browser smoke test is described in
`tests/browser_smoke.py` when Playwright and Chromium are installed.

The initial implementation passed 29 tests and the Chromium smoke test. A real
CPU large-v3 run transcribed the [public FunASR Mandarin sample](https://github.com/modelscope/FunASR/tree/main/examples)
as “欢迎大家来体验达摩院推出的语音识别模型。” in approximately 5.5 seconds,
including model loading on the development machine. The loaded model's `yue`
support and Cantonese request routing were verified; Cantonese recognition
accuracy has not yet been evaluated with a real Cantonese test corpus. Sample
audio and downloaded model weights are excluded from the repository.
