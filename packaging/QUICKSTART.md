# 双声 / Shuangsheng desktop

Extract the **whole archive** before starting the app. Keep its libraries beside
it. Python is included. Model weights and the system input-method host are
installed separately; no cloud speech service is used.

| Package | Start |
| --- | --- |
| Windows x64 | `Shuangsheng\Shuangsheng.exe` |
| macOS Apple Silicon / Intel | Move `Shuangsheng.app` to Applications, then open it |
| Linux x64 | `./Shuangsheng/Shuangsheng` |

macOS builds target macOS 13 or later; CI runs on macOS 15. Linux builds target
Ubuntu 22.04 or newer compatible glibc systems and require a desktop session.
Windows builds target Windows 10/11 x64. ARM Windows and musl/Alpine Linux are
not packaged. macOS Intel and Apple Silicon have separate downloads.

These builds have no Windows publisher certificate or Apple Developer ID /
notarization. macOS apps have an ad-hoc signature. Your OS may display an
unverified developer warning. Check the release's `SHA256SUMS` and source before
opening. On macOS, use System Settings → Privacy & Security → Open Anyway for
an app you trust; do not disable Gatekeeper globally. Windows users can inspect
the publisher warning before choosing to open a trusted download.

Pinyin works immediately. Use the app's model download control to explicitly
download Whisper weights, then select Mandarin or Cantonese for offline
recognition. `large-v3` requires about 3 GB of downloads and several GB of RAM.
CPU inference is the default; model weights are kept in your user cache.
No Python or separate FFmpeg installation is needed for the desktop app.

macOS asks for microphone permission. Global keyboard shortcuts/paste can need
Accessibility and Input Monitoring permission under Privacy & Security. Grant
these to Shuangsheng if you use that feature, then restart the app. On Windows,
allow desktop apps to access the microphone. Test dictation in a plain editor
and review the recognized text. Global hotkeys may not work in elevated apps,
secure fields, or Linux Wayland sessions; use the app's record/copy controls.

For system-wide pinyin on Windows or macOS, install Rime's official **Weasel**
or **Squirrel** host, then run the supplied installer command below and deploy
in the host. The host is an external dependency; it is not embedded in this
archive. See `source/docs/rime.md` for installation and removal details.
Linux users can use the source Fcitx5 adapter in `source/native/fcitx5` with
`source/scripts/install_native.py` and the system development packages listed
in `source/README.md`.

From a terminal inside the extracted directory:

```powershell
# Windows PowerShell
.\Shuangsheng\Shuangsheng.exe --install-rime
.\Shuangsheng\Shuangsheng.exe --download-model --model large-v3
.\Shuangsheng\Shuangsheng.exe --serve
```

```bash
# macOS (adjust if you moved the app to /Applications)
./Shuangsheng.app/Contents/MacOS/Shuangsheng --install-rime
./Shuangsheng.app/Contents/MacOS/Shuangsheng --download-model --model large-v3
./Shuangsheng.app/Contents/MacOS/Shuangsheng --serve

# Linux
./Shuangsheng/Shuangsheng --serve
```

`--serve` starts the browser pad at http://localhost:8765. It requires no Rime
host. `--install-rime --dry-run` previews configuration changes. A Windows
console window stays open for CLI diagnostics while the desktop app runs.
`--smoke-test` checks bundled assets/libraries without a microphone or download.

Application source, dictionary source/terms, and dependency license notices are
included in `source/` and `release-metadata/`. See `source/docs/releases.md` for
build instructions and verification limits.
