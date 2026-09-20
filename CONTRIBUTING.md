# Contributing

Bug reports and small, focused pull requests are welcome. For input bugs,
include the pinyin spelling, expected candidates, character style, and observed
result. For speech bugs, include the model, selected language, operating system,
and error message. Share only audio you have permission to publish.

Install `requirements-rime.txt`, then run `python -m unittest discover -v`,
`node --check static/app.js` and `node --check static/capture.js` before
submitting. HTTP tests need permission to bind a temporary localhost port.
Optional UI tests: install Playwright and Chromium, then run
`python tests/browser_smoke.py`. Tests do not download model weights.

Keep dictionary changes reproducible through `scripts/build_dictionary.py` and
preserve upstream source and licensing notices. Do not commit model weights,
virtual environments, credentials, or private recordings.

Native keyboard changes should build with `cmake -S native/fcitx5 -B
.cache/native-build && cmake --build .cache/native-build`. After installing,
run `python3 native/fcitx5/smoke_runtime.py` inside a desktop session with
PyGObject installed. This exercises a dedicated Fcitx input context and never
uses the microphone. Keep focus-change cancellation and sensitive-field bypass
working when changing dictation behavior.

Desktop changes should pass `python -m ime.desktop --gui-smoke-test` in a
graphical session (or under `xvfb-run` on Linux). This checks real Tk widgets
and clipboard operations without recording or registering a global hotkey.
Unit tests cover cancellation during capture/inference and microphone errors;
native GUI tests skip when no display is available.

For Windows/macOS system-pinyin changes, run the real librime deployment test
in [docs/rime.md](docs/rime.md). Retain existing user schemas and backups during
installation. Release changes must pass all four relocated frozen-binary builds
before publishing; see [docs/releases.md](docs/releases.md). Do not replace
published assets under an existing tag.

Original contributions use the project's MIT license. Third-party dictionary
changes retain the applicable upstream license. Please discuss native OS
adapters, alternate recognizers, and large architecture changes in an issue
before implementation.
