Shuangsheng 0.4.0 adds full pinyin, Xiaohe, tone-aware Jyutping, mixed English
input, shared personal vocabulary and predictions, persistent preferences,
revisable dictation, model reuse, and optional local AI editing with previews.

The desktop app now includes **Check updates…**: explicit GitHub release checks,
SHA-256 verification, staged installation, runtime checks, acknowledged restart,
retained versions and managed rollback. Existing Linux native addons update from
the same verified release and use its packaged voice runtime. Windows/macOS Rime
hosts remain separate; schemes can be installed with the existing scheme action.

Version 0.3.0 had no update client. Download and extract this release once to
bootstrap in-app updates. See `source/docs/updates.md` for upgrade/rollback
details. No update check, model download or AI request runs automatically.

Download the archive matching your system and extract it completely. Python,
speech libraries, pinyin dictionaries, source code and license notices are
included. Whisper model weights and Weasel/Squirrel are installed separately.
Read `QUICKSTART.md` and `THIRD_PARTY.md` in the archive before starting.
The application source retains MIT terms; bundled third-party libraries and
codecs retain their own licenses. Matching PyAV/FFmpeg build inputs and pynput
source are included under `third-party-sources/`, with exact hashes and recipes.

- Windows x64, macOS Apple Silicon, macOS Intel, Linux x64.
- Builds are unsigned by a verified publisher; macOS uses an ad-hoc signature
  and is not notarized. Microphone and global-hotkey permissions may be needed.
- `SHA256SUMS` covers all four downloads.
- All four builds exercise their actual archive through the updater, including
  corrupt-checksum rejection, extraction, runtime checks, activation, acknowledged
  restart, old-pointer restoration and preserved settings. CI uses a fixture
  transport; actual GitHub download and native Linux activation are separate checks.
- Each native build passed a relocated executable smoke test for dictionary
  lookup, speech-library loading, WAV decode, OpenCC conversion, ONNX voice
  activity detection and the Rime installer dry run. A separate GUI smoke test
  creates the window, composes pinyin and exercises the clipboard. CI also runs
  Python, JavaScript syntax and Linux native Fcitx5/Rime C++ checks.
- CI does not establish microphone, global shortcut, native host integration,
  or real Mandarin/Cantonese recognition quality on every desktop. See the
  manual acceptance checklist in `source/docs/releases.md`.
