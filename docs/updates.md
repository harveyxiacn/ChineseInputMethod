# Application updates

Version 0.4.0 introduces **检查更新… / Check updates…** in the desktop toolbar.
Checking is explicit; launching the app does not fetch releases or model files.
The updater selects the latest stable release from
`harveyxiacn/ChineseInputMethod` for Linux x64, Windows x64, macOS Apple Silicon
or Intel. Development/prerelease builds are not offered.

Version 0.3.0 did not contain an update client. Its first upgrade therefore
requires downloading/extracting 0.4.0 or launching the new updater from source.
It cannot acquire an in-app update button retroactively.
After 0.4.0 is published, `python scripts/desktop_entry.py --update install
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
Do not delete a retained version while its application/native addon is running.

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
release. Actual GitHub downloading and installed Linux activation are checked
separately during release acceptance.
