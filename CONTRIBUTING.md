# Contributing

Bug reports and small, focused pull requests are welcome. For input bugs,
include the pinyin spelling, expected candidates, character style, and observed
result. For speech bugs, include the model, selected language, operating system,
and error message. Share only audio you have permission to publish.

Run `python -m unittest discover -v` and `node --check static/app.js` before
submitting. HTTP tests need permission to bind a temporary localhost port.
Optional UI tests: install Playwright and Chromium, then run
`python tests/browser_smoke.py`. Tests do not download model weights.

Keep dictionary changes reproducible through `scripts/build_dictionary.py` and
preserve upstream source and licensing notices. Do not commit model weights,
virtual environments, credentials, or private recordings.

Original contributions use the project's MIT license. Third-party dictionary
changes retain the applicable upstream license. Please discuss native OS
adapters, alternate recognizers, and large architecture changes in an issue
before implementation.
