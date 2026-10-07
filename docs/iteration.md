# Input experience update

The source tree contains the keyboard, speech, personalization and UI changes.
Existing installed or frozen releases do not change until rebuilt. The Linux
addon can be built and tested without replacing the current running input method:

```sh
cmake -S native/fcitx5 -B .cache/native-build
cmake --build .cache/native-build
ctest --test-dir .cache/native-build --output-on-failure
/usr/bin/python3 tests/native_isolated_smoke.py
```

The isolated smoke requires PyGObject, Fcitx5 and dbus-run-session. It creates a
private D-Bus session, synthetic input context and temporary configuration. It
never activates a real application's input field or opens a microphone.
Run `python scripts/install_native.py` when ready to install the rebuilt addon;
that command updates the user installation and restarts its input method service.
The native build now requires json-c development headers.

## Capabilities and boundaries

| Capability | Desktop / browser | Linux native |
| --- | --- | --- |
| Full pinyin and Xiaohe | Local libime bridge when available; dictionary fallback otherwise | libime |
| Jyutping | Genuine licensed dictionary with tone-aware composition | Same dictionary with tone-aware composition |
| Personal terms / templates | Shared term editor; pin, delete, import, export | Reads shared terms; explicit selections merge into shared history |
| Next phrase | Explicit-commit history plus statistical libime predictions when available | Shared history plus libime predictions in candidate panel |
| English / URL / path literals | Original case and punctuation; Enter submits spelling | Literal preedit and original spelling; per-app English bypass |
| Persistent preferences | Language, script, engine, voice, appearance and shortcuts | Language, script, engine, fuzzy options, page size, voice hotkey, hold mode and app profile |
| Appearance | Theme, high contrast, font size and compact editor | Candidate theme/font remain controlled by Fcitx's frontend |
| Speech | Incremental preview, cancel, input level; desktop optional overlay | Existing preedit preview and focused-context commit, reusable model |
| Local AI | Explicit preview then accept, undo preserved | Use companion editor for model-assisted editing |

With Jyutping, numeric keys enter tones; Alt+number chooses a candidate. Full
pinyin uses digits for candidate selection; uppercase/structured English strings
keep their digits as literal text. For ambiguous lowercase strings, Enter always
commits the spelling, and English mode provides explicit control. Shortcuts such
as `/date` are deliberately prefixed so normal text does not expand unexpectedly.
Native raw text is editable with arrows, Home/End, Backspace and Delete.

Native preferences and shared terminology use the JSON paths documented in
[input.md](input.md). Native per-app profiles are keyed by the application name
reported by Fcitx (`InputContext::program()`); desktop Windows profiles use the
executable path, and macOS uses the bundle identifier. These identifiers differ
across platforms. Unrecognized applications use default preferences. Native
learning writes are merged under the shared file lock after a short debounce;
normal shutdown flushes pending selections.

## Reusable native speech worker

`ime.speech_daemon` is Linux-only. A private Unix socket verifies the peer UID;
its directory is mode 0700 and socket mode 0600. The worker starts lazily when
native dictation needs inference, reuses the loaded model, and unloads it after
300 idle seconds. The lightweight worker can remain running. `IME_SPEECH_RUNTIME`
selects a private runtime directory; by default it uses XDG_RUNTIME_DIR (or /tmp)
with the user's UID. No request can provide executable or arbitrary output paths.
The default socket directory also contains the application version, so an
updated native addon does not connect to a worker running previous-version code.

Each native recording still has its own capture worker. Escape, focus loss and
sensitive-field changes discard its result. An already running model inference
may finish in the reusable service after cancellation; its result is discarded.
Other inference requests can receive a busy response until it completes. A final
failure retains the already displayed preview with an incomplete-result warning.

Live window boundaries use short overlap only at forced cuts and conservative
exact-text deduplication. This is an accuracy/latency tradeoff, not a guarantee of
perfect stitching or true streaming recognition. Silence splits remain separate.
Desktop and browser input meters describe microphone level, not confidence.

## Speech engines and local AI

Whisper remains the default, with `large-v3` and CPU execution unchanged. Persisted
model/device preferences are used unless an explicit environment override exists.
Warmup checks local model readiness. Complete local Whisper directories need
weights, config and tokenizer data; partial directories are rejected without
attempting a fallback tokenizer download.

SenseVoice is an optional source installation:

```sh
python -m pip install -r requirements-sensevoice.txt
```

Supply a local SenseVoiceSmall directory in settings, containing `config.yaml`,
`model.pt`, one BPE tokenizer model, and any required frontend normalization file.
This app does not download those weights. Standard frozen releases exclude the
optional Torch/FunASR runtime. SenseVoice does not accept Whisper prompt/hotword
options. The adapter removes model metadata tags and preserves lexical text;
accuracy and device performance require evaluation with your recordings.

The optional assistant connects only to a user-configured HTTP loopback service
with `/v1/models` and `/v1/chat/completions`. It defaults to port 8080 and is off
until enabled. Polishing, translation and completion are user-triggered, run off
the typing path, and present editable suggestions before changing the document.
No automatic per-keystroke language-model reranking is enabled. Pinyin context
ranking uses the statistical engine and explicit local learning.

## Validation

Run Python unit tests, JavaScript syntax checks, native CTest, the isolated native
smoke, and browser smoke (`tests/browser_smoke.py`). Tk window tests require a real
or virtual display and a working matching Tcl/Tk installation. Browser and IPC
tests need permission to open local sockets. Tests use temporary preferences,
mock speech where appropriate, and synthetic input contexts.

The [evaluation harness](../benchmarks/README.md) accepts a fixed input suite and
an optional manifest of local audio with reference transcripts. Its small bundled
examples are regression fixtures, not a representative accuracy claim. Windows
and macOS external insertion adapters have mocked platform tests; actual
permissions, app widgets and packaged releases still need target-OS acceptance.

### Local verification, 2026-10-07

- Python discovery: 295 tests, successful with one Tk display-dependent skip.
  A separate Python 3.12 run with working Tcl/Tk and a display passed all 68
  desktop, voice, overlay and insertion tests without skips.
- Chromium: 20 interaction scenarios, including incremental audio retries,
  cancellation, hold-to-talk, stale AI results, draft recovery and mobile layout.
- Native addon: CTest 2/2 plus an isolated Fcitx/D-Bus runtime smoke passed.
  Real librime 1.17 deployed and exercised full pinyin, Xiaohe and toned Jyutping,
  including Alt+number selection, in temporary user directories.
- Eight authored input fixtures passed first-candidate selection. These small
  regression cases do not establish general input accuracy.
- One existing 5.55-second Mandarin sample with cached large-v3 on CPU took
  6.27 seconds on first use and 3.78 seconds with the reusable worker already
  loaded. Results agreed, and idle unloading succeeded. This is a single-sample
  timing check, not a representative speech benchmark.
- One synthetic proofreading request reached the existing local model service
  in 3.54 seconds and preserved the test sentence's numbers. AI output still
  requires explicit acceptance.

These feature checks preceded release and did not replace the running input
method, record a microphone, or exercise actual Windows/macOS application
insertion. The subsequent [v0.4.0 release acceptance](updates.md#v040-release-acceptance--2026-10-07)
passed all four packaged OTA workflows and upgraded the installed Linux input
method through the public GitHub release. SenseVoice model quality and interactive
Windows/macOS application insertion remain outside this verification.
