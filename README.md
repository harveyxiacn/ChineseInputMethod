# 双声 · Shuangsheng

A local Chinese input method with pinyin typing and Mandarin/Cantonese dictation.
Use the native **Fcitx5 keyboard on Linux** to type directly into applications,
or the cross-platform browser input pad. Speech runs on your machine; no API
keys or hosted speech service are used.

## System-wide Linux keyboard

Install Fcitx5 and its integrations. On Arch/CachyOS:

```bash
sudo pacman -S --needed fcitx5 fcitx5-gtk fcitx5-qt fcitx5-configtool cmake gcc
```

Set up voice dependencies and weights using the instructions below, then:

```bash
python3 scripts/install_native.py
```

This builds the native addon, installs it under `~/.local`, and enables
`shuangsheng.service` with your graphical login session. It adds a keyboard
profile only if you have no existing Fcitx5 profile; existing users should add
**Shuangsheng 双声** through `fcitx5-configtool`. Keep this checkout and its
`.venv` at their current location, or rebuild/reinstall after moving them.
PipeWire's `pw-record` is required for microphone capture.

For niri, set `XMODIFIERS "@im=fcitx"`, `QT_IM_MODULE "fcitx"`, and
`SDL_IM_MODULE "fcitx"` in your `environment` block. The installer can add these
to an existing environment file, with backup and `niri validate`:

```bash
python3 scripts/install_native.py --niri-environment ~/.config/niri/cfg/misc.kdl
```

Leave `GTK_IM_MODULE` unset for native GTK Wayland applications. Restart open
applications to pick up environment changes, or log out and back in. Some
Chromium/Electron versions need `--enable-wayland-ime
--wayland-text-input-version=3`. Applications must support the desktop's text
input protocol or an installed Fcitx GTK/Qt integration; this is not raw key
injection into arbitrary software. The default service targets non-GNOME,
non-KDE Linux sessions such as niri; other desktops may require their own
Fcitx startup configuration. See [Fcitx's Wayland setup](https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland).

| Shortcut (with Shuangsheng active) | Action |
| --- | --- |
| Ctrl+Space | Switch English / Chinese using Fcitx's default trigger |
| Letters, Space or 1–9 | Compose pinyin and select a candidate |
| Up/Down, PageUp/PageDown | Browse candidates and pages |
| Enter / Escape | Insert literal pinyin / cancel composition |
| Ctrl+Alt+Space | Start recording; press again to stop and transcribe |
| Ctrl+Alt+M / Ctrl+Alt+Y | Select Mandarin / Cantonese |
| Ctrl+Alt+T | Toggle simplified / traditional output |
| Escape while dictating | Cancel recording or transcription |

Stay in the same text field while dictating. Changing focus cancels dictation;
text is committed only into the original focused context. Recording ends after
115 seconds at most. Password/sensitive fields bypass this engine. Language
and script selections currently last for the Fcitx process lifetime.
The native adapter supports dictionary phrases, initials, prefixes and candidate
pages; it does not yet use the browser engine's sentence beam search.

```bash
systemctl --user status shuangsheng.service
journalctl --user -u shuangsheng.service -n 30
# Stop now and disable automatic startup:
systemctl --user disable --now shuangsheng.service
```

If disabling it permanently, also remove the Shuangsheng environment entries
and `~/.config/environment.d/70-shuangsheng.conf`, along with
`~/.local/share/dbus-1/services/org.fcitx.Fcitx5.service` (which activates this
service on demand), then log in again. Installer
backups use the suffix `.before-shuangsheng`.

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

The Linux Fcitx5 addon provides system-wide input in compatible applications.
The browser pad remains available on other operating systems. Native Windows
TSF and macOS InputMethodKit adapters are not implemented.

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
- `native/fcitx5/`: native C++ candidate UI and application text insertion.
- `ime/native_voice.py`: bounded PipeWire recording and local transcription;
  temporary recordings are deleted on completion or normal cancellation.
- `scripts/install_native.py`: user installation and login-session startup.

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

The native version also passed real Fcitx D-Bus preedit/commit tests for pinyin,
number selection, script switching, syllable boundaries, empty/unknown input,
Escape cancellation, and password-field bypass. Run
`python3 native/fcitx5/smoke_runtime.py` to repeat them after installation.
These checks use a dedicated input context and do not record your microphone.
