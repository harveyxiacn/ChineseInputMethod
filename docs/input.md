# Local input and personalization

The desktop and web candidate engine accepts full pinyin, Xiaohe double pinyin,
and Jyutping. Chinese output can use simplified or traditional characters.
Explicit pinyin syllable boundaries remain meaningful: `xi'an` produces 西安,
without suggesting 先. Accents, tone digits, `ü`, `v`, and `u:` work in full
pinyin. Xiaohe examples are `nihc` → 你好 and `vsgo` → 中国.

Jyutping uses 137,439 verified character and word readings from CanCLID's
CC BY 4.0 dictionary. Both `nei5hou2` and `nei hou` compose 你好; typed tones
remain significant. This is dictionary composition with frequency ranking.
It does not include a Cantonese statistical sentence model. Missing readings
are never inferred from Mandarin. Provenance, checksums, the pinned source
commit, and the license are in `ime/data/ATTRIBUTION.md`,
`scripts/build_jyutping.py`, and `ime/data/LICENSE.cantonese`.

On Linux, the optional libime bridge uses the system Chinese dictionary and
statistical language model for full Mandarin sentences. For example,
`wojintianxiangquchaoshimaidongxi` yields 我今天想去超市买东西.
One lazy background process loads the model and serves synchronized requests.
A failed or timed-out process is discarded; the next request starts a fresh
one. Without the bridge, the Python decoder uses the bundled character
vocabulary, common authored phrases, and a full-input beam decoder. Its
sentence ranking is less capable, especially with ambiguous pronunciations.
No model downloads occur when entering text.

Build the bridge separately without installing or changing user configuration:

```sh
cmake -S native/pinyin_bridge -B .cache/pinyin-bridge
cmake --build .cache/pinyin-bridge
```

This requires libime development headers, its `sc.dict` Chinese dictionary,
its `zh_CN.lm` language model, CMake, and a C++20 compiler. The native installer
also builds and installs the bridge. The Python engine discovers an installed
`shuangsheng-pinyin-bridge`, the source build above, or `~/.local/bin`.
`IME_PINYIN_BRIDGE` selects an explicit executable path. Other operating
systems retain the offline Python fallback; Rime frontends provide their own
native decoding.

Mixed Chinese/English strings, URLs, email addresses, paths, version strings,
and identifiers preserve the case, punctuation, and digits that were typed.
A small English vocabulary supplies completions. Unknown plain lowercase
pinyin does not automatically become a literal candidate. The editable
composition field still lets users enter other text directly.

User terms support explicit pinyin, pinned choices, and named shortcuts.
Shortcut templates can contain line breaks and tabs. Candidate retrieval does
not train the engine. Explicitly committed choices update local counts and
exact-context predictions; negated contexts are kept separate. With the local
libime bridge, next-phrase suggestions also use its statistical language model,
so common committed words can have useful continuations before any learning.
Personalized matches come first; repeated candidates and the context itself
are removed. Prediction works best when the caller supplies the last committed
word or phrase. These records
retain the committed query/text and up to 120 characters of supplied context.
Callers should supply previously committed context, never uncommitted drafts.
Audio is never stored in the lexicon. Draft saving is disabled by default and
is controlled separately by the `save_draft` preference.

Built-in expansions require explicit shortcuts:

| Shortcut | Expansion |
| --- | --- |
| `/date` | Local date, `YYYY-MM-DD` |
| `/time` | Local time, `HH:MM` |
| `/datetime` | Local date and time |
| `/degree`, `/celsius` | °, ℃ |
| `/percent`, `/sqm` | %, m² |
| `/arrow`, `/check`, `/ellipsis` | →, ✓, … |

Fuzzy lookup is opt-in. Its configurable pairs default to zh/z, ch/c, sh/s,
n/l, ang/an, eng/en, and ing/in. Exact matches retain priority, and explicit
syllable boundaries remain respected. It changes one syllable per variant,
avoiding uncontrolled combinations of spelling changes.

Preferences use `SettingsStore` and default to
`~/.config/shuangsheng/settings.json` on Linux (`XDG_CONFIG_HOME` is honored),
`~/Library/Application Support/shuangsheng/settings.json` on macOS, and
`%APPDATA%/shuangsheng/settings.json` on Windows.
`IME_SETTINGS_PATH` overrides the file. User terms and commit records use the
same directory's `lexicon.json`; `IME_LEXICON_PATH` overrides it.

Both stores validate input, reload on reads, and merge writes under a shared
advisory lock before atomically replacing the JSON file. Concurrent desktop
and web processes therefore observe changes without losing writes.
Malformed JSON and invalid individual fields fall back to defaults or are
ignored. Preferences do not accept raw audio or draft content. Defaults retain
Whisper `large-v3` on CPU, with automatic insertion and the local AI assistant
disabled. The assistant URL accepts an explicit HTTP loopback port and an
optional `/v1` base path.

`app_profiles` maps stable application identifiers to validated preference
overrides, including `input_mode: chinese` or `english`. Profile identifiers
can be Windows executable paths or macOS bundle identifiers. Profile overrides
cannot contain another `app_profiles` mapping.

Optional Rime schemes can be installed alongside the existing full-pinyin schema:

```sh
python scripts/install_rime.py --schemes pinyin shuangpin jyutping --dry-run
python scripts/install_rime.py --schemes pinyin shuangpin jyutping
```

Run the frontend's Redeploy command, then select 双声拼音, 双声小鹤双拼,
or 双声粤拼 from its schema menu. In Jyutping, plain digits 1–6 enter tones;
Alt+1 through Alt+9 select candidates and Space commits the first candidate. The default installer still installs full
pinyin only. Double pinyin shares the Mandarin dictionary and essay preset;
Jyutping installs its own verified reading dictionary and license notice.
The authored optional schemas can be regenerated reproducibly with
`python scripts/build_rime_schemes.py`; it transforms dictionary full-pinyin
syllables into protected Xiaohe codes before removing the protection.

Existing schema lists, preferences, and dictionaries are preserved, with
backups before replacing changed Shuangsheng files. Optional payloads are
validated before any file is written. These schemes use the frontend's Rime
learning, independently of the desktop/web JSON commit history.
