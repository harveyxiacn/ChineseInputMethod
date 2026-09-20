Cross-platform Shuangsheng: system-wide Chinese pinyin using Linux Fcitx5 or
Rime's Windows Weasel / macOS Squirrel hosts, plus a standalone desktop input
pad and local Mandarin/Cantonese dictation.

Download the archive matching your system and extract it completely. Python,
speech libraries, pinyin dictionaries, source code and license notices are
included. Whisper model weights and Weasel/Squirrel are installed separately.
Read `QUICKSTART.md` in the archive before starting.

- Windows x64, macOS Apple Silicon, macOS Intel, Linux x64.
- Builds are unsigned by a verified publisher; macOS uses an ad-hoc signature
  and is not notarized. Microphone and global-hotkey permissions may be needed.
- `SHA256SUMS` covers all four downloads.
- Each native build passed a relocated executable smoke test for dictionary
  lookup, speech-library loading, WAV decode, OpenCC conversion, ONNX voice
  activity detection and the Rime installer dry run. A separate GUI smoke test
  creates the window, composes pinyin and exercises the clipboard. CI also runs
  Python, JavaScript syntax and Linux native Fcitx5/Rime C++ checks.
- CI does not establish microphone, global shortcut, native host integration,
  or real Mandarin/Cantonese recognition quality on every desktop. See the
  manual acceptance checklist in `source/docs/releases.md`.
