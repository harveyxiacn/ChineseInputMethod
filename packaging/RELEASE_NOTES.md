Shuangsheng 0.4.2 fixes packaged HTTPS certificate discovery, including Linux
systems whose certificate directory differs from the build machine. Every release
now checks real GitHub HTTPS from all four frozen applications. Release CI uses
a read-only API token to avoid shared-runner anonymous rate limits; ordinary
application updates remain anonymous. The unpublished 0.4.1 tag was blocked by
that rate limit and is superseded by this release.

The 0.4 series adds full pinyin, Xiaohe, tone-aware Jyutping, mixed English
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
If 0.4.0 reports a certificate-path error on Linux, run its updater once with
`SSL_CERT_FILE` pointing to the existing system CA bundle (for example
`/etc/ssl/cert.pem` on Arch), or extract this release. Certificate verification
remains enabled; this release discovers the OS trust store without that override.

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
  transport for installation and a separate real HTTPS check from the packaged
  updater. Actual GitHub download and native Linux activation are separate checks.
- Each native build passed a relocated executable smoke test for dictionary
  lookup, speech-library loading, WAV decode, OpenCC conversion, ONNX voice
  activity detection and the Rime installer dry run. A separate GUI smoke test
  creates the window, composes pinyin and exercises the clipboard. CI also runs
  Python, JavaScript syntax and Linux native Fcitx5/Rime C++ checks.
- CI does not establish microphone, global shortcut, native host integration,
  or real Mandarin/Cantonese recognition quality on every desktop. See the
  manual acceptance checklist in `source/docs/releases.md`.
