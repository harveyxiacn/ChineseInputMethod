# Application updates

Version 0.4.0 introduces **检查更新… / Check updates…** in the desktop toolbar.
Version 0.4.2 fixes frozen HTTPS trust-store discovery across Linux distributions
and adds an actual GitHub HTTPS check to each packaged release gate.
Checking is explicit; launching the app does not fetch releases or model files.
The updater selects the latest stable release from
`harveyxiacn/ChineseInputMethod` for Linux x64, Windows x64, macOS Apple Silicon
or Intel. Development/prerelease builds are not offered.

Version 0.3.0 did not contain an update client. Its first upgrade therefore
requires downloading/extracting 0.4.2 or launching the new updater from source.
It cannot acquire an in-app update button retroactively.
`python scripts/desktop_entry.py --update install
--bootstrap` performs that first managed installation from source. This explicit
mode permits installing the same published version as the updater, but rejects
a published version older than the updater itself.

## Install and restart

1. Open **Check updates…** and check the displayed current/new versions.
2. Download the update. It must match the release's exact archive size and
   `SHA256SUMS` hash. Links outside the fixed GitHub repository/release hosts
   are rejected. Checksums detect corruption; releases are not publisher-signed.
3. The updater validates archive entries and extracts into a separate version
   directory. It rejects traversal, external links and oversized extraction,
   then runs the new executable's offline runtime smoke test.
4. Save edited text and confirm installation. The active-version pointer changes
   atomically. A new process must confirm its window has initialized before the
   old window closes. Failed startup restores the prior pointer.

Running executables are never overwritten, including on Windows. Retained
versions are under `%LOCALAPPDATA%/shuangsheng/updates`,
`~/Library/Application Support/shuangsheng/updates`, or
`${XDG_DATA_HOME:-~/.local/share}/shuangsheng/updates`. Settings, personal terms,
drafts and speech model caches remain in their existing separate locations.
Do not delete a retained version while its application/native addon is running
or a desktop shortcut still points to its original launcher.

For the first **source-to-package** installation, source speech weights default
to `<checkout>/.cache/huggingface`; packaged applications use
`~/.cache/shuangsheng/huggingface` on Linux (respecting `XDG_CACHE_HOME`),
`~/Library/Caches/shuangsheng/huggingface` on macOS, or
`%LOCALAPPDATA%/shuangsheng/huggingface` on Windows. Before switching, copy the
required `hub/models--…` directory into the packaged cache's `hub` directory,
preserving its internal snapshot links, and verify the copied files. Keep the
original cache. This copies model data only; Hugging Face credentials are not
needed. An explicitly configured `HF_HOME` takes precedence in both modes.
Subsequent packaged updates share the same cache and require no migration.

Original 0.4.0+ packaged launchers follow the active-version pointer on the next
normal launch. Administrative commands such as `--version` and `--smoke-test`
always run that particular executable. Use `--no-update-redirect` to explicitly
start a retained package without following the pointer. Source development
launches using `python -m ime.desktop` stay tied to their source checkout.

CLI equivalents (use the packaged executable in place of Python when frozen):

```sh
python scripts/desktop_entry.py --update check
python scripts/desktop_entry.py --update download
python scripts/desktop_entry.py --update install
python scripts/desktop_entry.py --update status
python scripts/desktop_entry.py --update rollback
```

HTTPS uses the operating system's trusted certificates, with bundled Mozilla
roots when Python has no default store. Explicit `SSL_CERT_FILE`/`SSL_CERT_DIR`
settings are honored. Certificate and hostname verification are always enabled.
On a Linux distribution affected by the 0.4.0 certificate-path bug, launch its
updater once with `SSL_CERT_FILE` pointing to the installed system CA bundle
(for example `/etc/ssl/cert.pem` on Arch Linux) to acquire 0.4.2. This selects
trusted certificates; it does not disable verification. Version 0.4.2 discovers
these OS paths itself.

Rollback switches to the preceding **managed** version. On the first managed
upgrade, the original unpacked installation remains available at its old path;
there is no managed previous version yet. Nothing deletes that original copy.

## Linux native addon

If the known per-user Fcitx5 addon is already installed, application installation
also builds its new native addon and pinyin bridge from the verified release's
source snapshot. This requires the existing compiler, CMake and Fcitx5/libime,
OpenCC, Boost and json-c development dependencies. Failed builds leave the running
input method unchanged. No root package installation happens automatically.

Both native launch modes are supported: source installs use their configured
Python interpreter; OTA installs use the new package's frozen voice executable.
The new addon reads its own retained source dictionaries, without depending on a
developer checkout or `.venv`. Speech workers use versioned sockets.
Managed launches disable the companion's global keyboard listener when the Linux
native addon is installed, leaving the shared dictation shortcut to the addon;
the companion's recording button remains available.

Finish native dictation before updating. Successful activation atomically
switches the two user-level native binaries and briefly restarts
`shuangsheng.service`; configuration, profiles and user dictionaries are preserved.
The updater checks that the new addon is actually mapped by Fcitx. Failed
activation restores the old binaries and restarts the old installation. Managed
rollback also rebuilds/reactivates the previous native version when applicable.

Windows/macOS Rime host applications are separate installations and are not
updated by this updater. Updated Shuangsheng schemes can be deployed with the
existing **Install selected system scheme…** action; existing Rime learning is
preserved by that installer.

## Verification

Each stable-tag or manual validation CI build runs `tests/ota_release_smoke.py` against its actual
archive. A test transport supplies the same archive through the updater's fixed
GitHub URLs; production has no alternate feed or HTTP bypass. The harness checks
bad checksums, real extraction, the new binary's smoke test, activation,
acknowledged child startup, restoring the old pointer and unchanged configuration.
This verifies all four package formats/runtimes without publishing a partial
release. Since 0.4.2 the release gate additionally invokes the actual frozen
executable's `--update check --report <file>` against GitHub. A nonzero exit or
missing/invalid report fails the build, including certificate, HTTP and network
errors. The offline smoke check remains offline.
Actual GitHub downloading and installed Linux activation are checked
separately during release acceptance.

### v0.4.0 release acceptance — 2026-10-07

- [Public release](https://github.com/harveyxiacn/ChineseInputMethod/releases/tag/v0.4.0)
  is built from `8804554faa7586ea941d5501f5d21cc281f1bda5`. All four platform
  archives and `SHA256SUMS` were published after every gate passed.
- [Release workflow](https://github.com/harveyxiacn/ChineseInputMethod/actions/runs/37573132808):
  all 14 jobs passed, including the real archive OTA harness on Linux x64,
  Windows x64, macOS arm64 and macOS x64.
  [Main tests](https://github.com/harveyxiacn/ChineseInputMethod/actions/runs/37572757229):
  all 9 jobs passed.
- A real Linux first-install bootstrap fetched the public GitHub release through
  the production updater, verified and extracted the archive, built and tested
  the native addon, activated it, and received the new desktop window's startup
  acknowledgement. `/proc/<fcitx-pid>/maps` confirmed the addon came from the
  retained `v0.4.0-linux-x64` directory. The service remained active; the existing
  Fcitx profile's SHA-256 was unchanged. A subsequent check offered no update.
- Installed executable `--version` returned `0.4.0`; its offline `--smoke-test`
  passed audio/VAD checks and reported the libime backend. The copied Whisper
  cache passed file hashes and snapshot-link checks; packaged speech status was
  `cached: true`, `ready: true`, `loaded: false`. No microphone was opened.
- Linux archive SHA-256:
  `b32a4485dae518e1b52152aadc5505be1247be58cde04aa260c60b1205002075`.

This is a completed Linux installation and CI verification of all four packaged
updaters. Interactive Windows/macOS use, external application insertion and Rime
host upgrades were not part of this acceptance.

A subsequent direct check from the installed 0.4.0 executable exposed the Linux
certificate-path problem above: the source updater had working OS roots, while
the frozen OpenSSL default pointed at a build-host path absent on this machine.
Selecting the existing OS CA bundle restored verified HTTPS in 0.4.0; the 0.4.2
fix and live frozen HTTPS release gate address this gap.

### v0.4.2 release and installed OTA acceptance — 2026-10-07

- [Public release](https://github.com/harveyxiacn/ChineseInputMethod/releases/tag/v0.4.2),
  commit `2d46210225c9ca398fdd28408c8d317ed68a4c41`:
  [all 14 release jobs passed](https://github.com/harveyxiacn/ChineseInputMethod/actions/runs/37575317994),
  including real frozen HTTPS checks and the installation harness on all four
  platforms. The unreleased 0.4.1 tag encountered CI shared-IP API rate limits;
  the CI-only read-only token avoids that limit without changing TLS checks.
  Normal application updates need no token. The optional
  `SHUANGSHENG_GITHUB_TOKEN` is sent only to the fixed latest-release API URL and
  is removed on every redirect; it is never sent to archive download hosts.
- The **installed 0.4.0 frozen executable** performed a real GitHub OTA download
  and installation of 0.4.2 on Linux. Its one-time `SSL_CERT_FILE` setting selected
  the existing OS roots. The active record now contains current `v0.4.2` and
  previous `v0.4.0`; both managed packages remain present.
- Fcitx restarted successfully and mapped the native addon from the retained
  0.4.2 directory. The existing profile's hash was unchanged. The updated desktop
  confirmed startup, and the installed executable reported `0.4.2`.
- The new frozen executable checked real GitHub with both `SSL_CERT_FILE` and
  `SSL_CERT_DIR` overrides removed and correctly reported no newer version.
  Its offline runtime smoke passed, with the existing Whisper cache ready and
  no model loaded by the smoke check. No microphone recording was required.
- Linux archive SHA-256:
  `edef9e15742586445a3b39ece16e8fb7e54d827f68e518d486b83b6133818787`.

Windows/macOS runtime and OTA checks ran on their matching CI systems;
interactive desktop permissions and third-party Rime host upgrades still require
their separate platform acceptance.
