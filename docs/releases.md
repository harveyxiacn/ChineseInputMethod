# Cross-platform builds and releases

Git tags named `vMAJOR.MINOR.PATCH` trigger `.github/workflows/release.yml`.
The release builds these native applications with Python 3.12 and PyInstaller:

| Archive target | Build runner | Intended environment |
| --- | --- | --- |
| `windows-x64.zip` | `windows-2022` | Windows 10/11 x64 |
| `macos-arm64.zip` | `macos-15` | Apple Silicon, macOS 14+ |
| `macos-x64.zip` | `macos-15-intel` | Intel, macOS 14+ |
| `linux-x64.tar.gz` | `ubuntu-22.04` | glibc Linux with a desktop, Ubuntu 22.04+ |

Runner labels follow [GitHub's supported runner list](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
These are separate builds on their host OS, not cross-compiled binaries. macOS
minimums describe the build target; the automatic runtime checks run on macOS
15, so validate older supported systems before claiming tested compatibility.
The Linux executable includes PortAudio and Tk dependencies collected by the
freezer, and still needs the usual glibc/X11 desktop libraries. Windows may need
the [Microsoft Visual C++ runtime](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist),
which is a [CTranslate2 requirement](https://opennmt.net/CTranslate2/installation.html).

## What is shipped

Each archive contains a desktop application, a quick-start guide, a source
snapshot including the Linux Fcitx5 adapter and Rime installer/configuration,
bundled dictionary source and licenses, resolved build dependency versions and
available dependency license/notice files. `third-party-sources/` contains the
exact pinned PyAV FFmpeg recipe, its native dependency source archives/patches,
PyAV and pynput sources, with verified download hashes. The build records
FFmpeg's reported license/configuration, shared-library hashes, Python,
Tcl/Tk and PortAudio runtime versions; see `THIRD_PARTY.md` for component terms.
The source tree is deliberately
copied from a small allowlist; user caches, microphone recordings and model
weights are excluded. The commit SHA is recorded in `build-manifest.json`.

No Python installation is required to run the desktop application. Whisper
model weights are downloaded only by an explicit user command or UI action.
The Rime Weasel/Squirrel host and Linux Fcitx5 development packages are external
dependencies. The Linux native adapter is built from the included source on the
user's machine because its configuration points to that installed source tree.

macOS bundles declare `NSMicrophoneUsageDescription` through the [PyInstaller
bundle specification](https://pyinstaller.org/en/stable/spec-files.html).
These initial packages are not publisher-signed or notarized. PyInstaller adds
an ad-hoc macOS signature. Adding a trusted signing identity/notarization later
requires repository secrets, and must preserve checks on the final signed
archive. See `packaging/QUICKSTART.md` for permissions and startup.

## Build locally

Use a clean Python 3.12 virtual environment on the target OS. On Linux install
Tk and PortAudio first (`sudo apt install python3-tk libportaudio2` on Ubuntu).

```bash
python -m pip install -r requirements-desktop.txt -r requirements-rime.txt -r packaging/requirements-build.txt
python scripts/build_release.py --version 0.3.0
```

The script creates `dist/release/Shuangsheng-0.3.0-<target>.<archive>`. It builds
in `.cache/release/`, assembles the source and notices, then extracts the
archive into an unrelated temporary directory and executes its smoke test.
Errors stop the build. `--target` optionally asserts that the host is the
expected architecture. PyInstaller and wheel versions are recorded; only the
build tool and PyAV wheel are pinned, so other dependency updates can change
the binaries. PyAV/FFmpeg version changes require reviewing the source manifest
before the build can proceed. Build-time source downloads require internet;
their verified cache is `.cache/third-party-sources/`. Retain the manifest when
investigating regressions.

Use the Actions **Cross-platform release → Run workflow** button for validation
without publishing. It uploads all four build artifacts for 14 days. Running
manually on a version tag also publishes after the same gates pass.
Manual branch builds use the source version and record the exact commit in their
manifest; they also run OTA acceptance but do not create a GitHub release.

## Publishing

```bash
git tag v0.3.0
git push origin v0.3.0
```

Publishing waits for the full Python/JavaScript/native test workflow and all
four builds. A checksums step rejects missing, empty, extra or mixed-version
archives. The release is created as a draft, all four archives and `SHA256SUMS`
are uploaded, then the draft is published. A failed upload leaves a draft, not
a public partial release. Re-running a failed draft is supported; an already
published version is never silently overwritten. Use a new tag for fixes.

Verify downloads with `sha256sum -c SHA256SUMS` on Linux, `shasum -a 256` on
macOS, or `Get-FileHash <archive> -Algorithm SHA256` in PowerShell. Checksums
detect corruption; they are not a publisher signature.

## Automatic and interactive verification

Stable release builds also run `tests/ota_release_smoke.py` against each actual
archive before upload. It verifies checksum failure handling, extraction,
runtime checks, activation and acknowledged restart, restoring the previous
pointer and retaining configuration. See [application updates](updates.md) for
the updater's trust boundary, Linux native activation and 0.3.0 bootstrap.

The frozen smoke test runs outside the source checkout with offline model
flags. It exercises pinyin dictionary lookup, speech-service status, Tk and
audio/inference imports, actual PyAV WAV decode, bundled OpenCC dictionaries,
ONNX voice activity detection on silence, required Rime assets and installer
`--dry-run`. It requires no display, microphone or Whisper model. The native
C++ test runs against system Fcitx5/libime on Ubuntu 24.04. Portable Python tests
run on Windows, both macOS architectures, and Linux. Platform-specific process
and signal behavior is isolated with test doubles where appropriate. Each
release also runs the frozen app's `--gui-smoke-test`: create a real Tk window,
compose and select pinyin, exercise the clipboard and verify recording controls
without opening a microphone. Linux uses Xvfb for this check.

Before promoting support for a new OS version, perform these interactive
checks on a machine running that system:

1. Extract the download and launch it through the normal desktop UI. Check
   pinyin selection, simplified/traditional output and copying text.
2. Download a multilingual v3 model explicitly, disable the network, grant
   microphone permission and transcribe one Mandarin and one Cantonese clip.
   Test cancellation, missing model, silence and microphone denial.
3. If using global shortcuts, grant the platform permissions, check start/stop
   in a plain text editor, and confirm that result review/copy remains usable
   when the target app blocks automated pasting.
4. Install the Rime host (Windows/macOS), apply the packaged configuration,
   redeploy, and compose `nihao` in a native text field. Check existing Rime
   schemas remain selectable and installer backups can be restored. For the
   Linux Fcitx5 adapter, run the native runtime smoke procedure from README.
5. Check application startup after moving the extracted directory and verify
   that caches are written to the user's directory, not inside the bundle.

CI smoke tests do not measure real speech recognition accuracy or verify OS
privacy dialogs, microphones, global shortcuts and native text-field behavior.
Keep those limits explicit in release notes until manual results are recorded.
