# 双声 · Shuangsheng

A local Chinese input method with pinyin typing and Mandarin/Cantonese dictation
for **Windows, macOS, and Linux**. Speech runs on your machine; no API keys or
hosted speech service are used.

| Platform | System-wide pinyin | Local voice and input pad |
| --- | --- | --- |
| Windows x64 | Shuangsheng Rime scheme with [Weasel / 小狼毫](https://rime.im/) | Packaged desktop application |
| macOS Apple Silicon and Intel | Shuangsheng Rime scheme with [Squirrel / 鼠须管](https://rime.im/) | Packaged `.app` |
| Linux x64 | Native Fcitx5 addon below, or Shuangsheng Rime scheme | Packaged desktop application |
| All three, from source | OS adapters above | Python desktop application or localhost browser pad |

Rime provides native composition, candidate windows, sentence input, and local
learning on Windows and macOS. The desktop companion records and transcribes
Mandarin/Cantonese, lets you review the result, and copies it for pasting into
your application. The Linux Fcitx5 addon also offers integrated dictation with
live preedit and direct insertion into the original text field.

## Install Windows / macOS / Linux releases

Download your platform archive from
[GitHub Releases](https://github.com/harveyxiacn/ChineseInputMethod/releases).
Extract the entire archive before starting Shuangsheng; keep its support files
together. Release assets include SHA-256 checksums and third-party notices.
Windows and macOS builds are currently unsigned; macOS builds are not notarized.
See [release installation and verification](docs/releases.md).

For system-wide pinyin on Windows or macOS, first install Weasel or Squirrel,
then install the bundled Shuangsheng scheme and redeploy Rime as described in
[the Rime setup guide](docs/rime.md). These existing native frontends handle the
operating system integration; this project does not replace their installers.

Open the desktop app for local voice. Pinyin works before downloading speech
weights. Use the explicit model download action once, or select a local model
directory; model files are not included in release archives. See
[desktop controls, setup, and permissions](docs/desktop.md).

From a source checkout (Python 3.12 recommended):

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate
python -m pip install -r requirements-desktop.txt
python scripts/install_rime.py
python -m ime.desktop
```

The Rime installer preserves existing schemas and backs up modified settings.
On Linux, the Fcitx5 addon below is the preferred integrated keyboard; the Rime
installer is an alternative for users with an existing Rime frontend.

## System-wide Linux keyboard

Install Fcitx5 and its integrations. On Arch/CachyOS:

```bash
sudo pacman -S --needed fcitx5 fcitx5-gtk fcitx5-qt fcitx5-configtool libime opencc boost cmake gcc
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
| Ctrl+Alt+Space | Start recording with live preview; press again to finish |
| Ctrl+Alt+M / Ctrl+Alt+Y | Select Mandarin / Cantonese |
| Ctrl+Alt+T | Toggle simplified / traditional output |
| Escape while dictating | Cancel recording or transcription |

Stay in the same text field while dictating. Changing focus cancels dictation;
text is committed only into the original focused context. Recording ends after
115 seconds at most. While recording, the native input field shows a revisable
preedit and the browser shows a live transcript below the recording button.
Stopping performs a final transcription and inserts the result once. Escape
in the native input method discards the preview.

Native live dictation checks for new audio every 3 seconds and processes
windows of at most 12 seconds, preferring pauses after 5 seconds as boundaries.
Confirmed windows are reused; stopping recognizes only the remaining tail.
Live decoding uses a narrower beam for speed. Continuous speech without pauses
may lose accuracy at window boundaries. Model loading and CPU inference still
affect latency. If final inference or capture fails, the native addon inserts
the latest displayed preview and warns that the ending may be incomplete;
Escape and changing focus still cancel without inserting text. Error diagnostics
are available in `journalctl --user -u shuangsheng.service`.

The browser currently uses complete audio snapshots for its live preview, so
long browser recordings may still slow down. Browser capture requires AudioWorklet
on localhost or HTTPS. Uploaded files use one-shot transcription.
Rebuild/reinstall the native addon after updating this checkout.

Password/sensitive fields bypass this engine. Language
and script selections currently last for the Fcitx process lifetime.
Chinese mode converts common punctuation (`, . ? ! : ; ( ) [ ] < >`),
backslash to `、`, paired quotes, `^` to `……`, and `_` to `——`.
A period immediately after a typed digit stays ASCII for decimals. Apostrophes
inside pinyin remain syllable separators. The browser applies this conversion
in its pinyin input; the document editor remains free-form. Switch to English
mode for URLs or code in native applications.

The native adapter uses libime's full pinyin dictionary and statistical language
model, including sentence composition and phrase-by-phrase selection. Type a
full sentence such as `wojintianxiangquchaoshimaidongxi`, or select shorter
phrases from the candidate list while continuing to compose the remainder.
This native engine is independent of the browser pad's basic TSV decoder.
The installed libime dictionary on the development machine has 300,234 entries.
Selected words and phrases improve future ranking. Learning stays locally in
`~/.local/share/shuangsheng/{user.dict,history}` (or under `XDG_DATA_HOME`).

Dictation validates the recorded WAV even when PipeWire returns its normal
interrupted-recording exit status. Silence, missing input devices, and model
errors are shown in the candidate panel. To select a particular PipeWire source,
set `IME_RECORD_TARGET` in the keyboard service environment; otherwise it uses
your default microphone. The recording/transcription completion protocol also
handles Fcitx reaping worker processes before the addon polls their exit status.

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

## WeChat on niri / Wayland

If WeChat's candidate window shakes or steals focus and only Latin letters reach
the editor, try an app-specific Qt compatibility launch. Fcitx5 also serves the
IBus protocol, so this still uses Shuangsheng without starting an ibus-daemon.
The [Fcitx Qt compatibility guidance](https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland/en#QT_IM_MODULE)
describes this combination for applications shipping their own Qt runtime.

For an official standalone executable or AppImage:

```bash
./scripts/wechat_compat.sh /absolute/path/to/WeChat.AppImage
```

Fully exit the previous WeChat instance first; a second launch may just activate
its existing process, whose environment cannot be changed by the launcher.

For the Portable-packaged `wechat` command, use the package's per-app environment
file instead. Portable does not forward arbitrary launcher environment variables
into its sandbox. For the standard `WeChat_Data` state directory, merge these
settings into `~/.local/share/WeChat_Data/portable.env`, preserving other entries:

```ini
QT_QPA_PLATFORM=xcb
QT_IM_MODULE=ibus
IBUS_USE_PORTAL=1
```

Then fully exit WeChat and reopen it from the usual launcher. This keeps its
Portable sandbox and data directory. Remove these entries to undo the override.
Do not set this compatibility mode globally for all desktop applications.
On the development installation (WeChat 4.1.13.9 / Portable 20.1), the filtered
IBus portal was verified to compose `nihao` into `你好` using Shuangsheng.

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
Whisper **large-v3** download is approximately 3 GB. Source checkouts store it
under `.cache/huggingface`; packaged apps use the user's writable cache directory
(`%LOCALAPPDATA%/shuangsheng/huggingface` on Windows,
`~/Library/Caches/shuangsheng/huggingface` on macOS, and
`$XDG_CACHE_HOME/shuangsheng/huggingface` or `~/.cache/shuangsheng/huggingface`
on Linux). `HF_HOME` can override that location.
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

The Linux Fcitx5 addon and the Windows/macOS Rime frontends provide system-wide
input in compatible applications. Rime uses its native pinyin engine, private
user dictionary, and sentence composition. The desktop and browser pads use
the project's smaller portable Python decoder. Windows TSF and macOS
InputMethodKit integration is supplied by Weasel and Squirrel respectively.

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
- `native/rime/`: portable native pinyin scheme, punctuation, and librime runtime test.
- `ime/desktop.py`: desktop pinyin/voice interface and explicit copy workflow.
- `ime/desktop_voice.py`: bounded, cancellable cross-platform microphone recording.
- `ime/native_voice.py`: bounded PipeWire recording and local transcription;
  temporary recordings are deleted on completion or normal cancellation.
- `scripts/install_native.py`: user installation and login-session startup.
- `scripts/install_rime.py`: Windows/macOS/Linux Rime configuration installation.
- `.github/workflows/release.yml`: tested platform archives and tag-triggered GitHub releases.

The portable pad's pinyin ranking uses basic dictionary weights and a small
authored common-word overlay; it does not reproduce the native engines' ranking
or learn your habits. The browser and Fcitx5 addon provide revisable live speech
previews; the desktop companion transcribes after recording stops. Whisper can make
mistakes, especially with accents, noise, code-switching, or short Cantonese
clips; review results before using them. The browser document exists only in the current tab
and is lost on reload unless copied or downloaded.

## Open-source foundations

| Project | Use here | License |
| --- | --- | --- |
| [Rime Luna Pinyin](https://github.com/rime/rime-luna-pinyin) | Bundled source dictionary and derived TSV | LGPL-3.0; source and terms in `ime/data/` |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Local inference using CTranslate2 | MIT |
| [libime](https://github.com/fcitx/libime) | Native pinyin dictionary, language model, and learning; installed as a system dependency | LGPL-2.1-or-later and component notices |
| [Whisper](https://github.com/openai/whisper) | Multilingual speech model | MIT |
| [OpenCC Python](https://github.com/yichen0831/opencc-python) | Simplified/traditional conversion | Apache-2.0 |
| [Rime / librime](https://github.com/rime/librime) | Windows/macOS native input through separately installed Weasel/Squirrel, and optional Linux Rime frontend | BSD-3-Clause; frontend licenses apply separately |

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
node --check static/capture.js
```

Unit and HTTP integration tests cover pinyin conversion/segmentation, binary
uploads, request validation, speech-language forwarding, concurrency, offline
model loading, and conversion failures. Speech unit tests use model doubles;
they do not establish real-world recognition accuracy. HTTP tests bind a
temporary localhost port. The optional browser smoke test is described in
`tests/browser_smoke.py` when Playwright and Chromium are installed.

A real
CPU large-v3 run transcribed the [public FunASR Mandarin sample](https://github.com/modelscope/FunASR/tree/main/examples)
as “欢迎大家来体验达摩院推出的语音识别模型。” in approximately 5.5 seconds,
including model loading on the development machine. The loaded model's `yue`
support and Cantonese request routing were verified; Cantonese recognition
accuracy has not yet been evaluated with a real Cantonese test corpus. Sample
audio and downloaded model weights are excluded from the repository.

CI tests Python on all three operating systems and builds release binaries on
Windows x64, macOS arm64/x64, and Linux x64. Frozen-app smoke tests verify bundled
data and runtime imports; the native Linux and Rime checks exercise actual
decoder libraries. These checks do not replace microphone and application
compatibility testing on each user's desktop. See [release checks](docs/releases.md).

The native version also passed real Fcitx D-Bus preedit/commit tests for pinyin,
number selection, script switching, syllable boundaries, empty/unknown input,
Escape cancellation, and password-field bypass. Run
`python3 native/fcitx5/smoke_runtime.py` to repeat them after installation.
These checks use a dedicated input context and do not record your microphone.
`python3 tests/native_speech_smoke.py /path/to/mandarin.wav` additionally tests
native recording hotkeys, actual PipeWire capture, Whisper inference, and text
commit using an isolated virtual audio source. It temporarily restarts the
keyboard service and restores its audio-target setting afterward.
Use `--repeat 10` for a longer recording, `--fail-after-preview` to verify
preview recovery after a worker crash, or `--cancel-after-preview` to verify
that Escape still discards provisional text. The long-recording fixture
(about 55 seconds) retained all ten repeated sentences; stopping took about
7.4 seconds on the development CPU. This is a fixture result, not a latency
or accuracy guarantee for arbitrary recordings.
