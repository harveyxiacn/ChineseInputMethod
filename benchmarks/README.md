These are small authored regression examples, not a representative quality benchmark.
The evaluator runs locally, never downloads weights, and never uploads audio.

Run input examples without loading a speech model:

```sh
python scripts/evaluate_input.py --output /tmp/input-evaluation.json
```

The report records first-candidate and top-k accuracy, first/warm query durations,
dictionary/backend information, and factual Python/platform/CPU context. Input
evaluation uses an empty temporary lexicon; it neither reads nor changes personal
terms or learning. The optional native libime bridge and installed dictionaries
can affect ranking and are recorded in the report. Jyutping cases require the
licensed local dictionary already installed in `ime/data/jyutping.tsv`.

For speech, create a manifest of recordings you own or may evaluate. Paths resolve
relative to the manifest directory; absolute local paths are also accepted:

```json
{
  "schema_version": 1,
  "cases": [{
    "id": "my-mandarin-test",
    "path": "recording.wav",
    "reference": "张小明说不要改成42",
    "language": "zh",
    "script": "original",
    "terms": ["张小明", "不要", "42"],
    "live": true
  }]
}
```

```sh
python scripts/evaluate_input.py --audio /path/to/manifest.json --model large-v3 --device cpu --output /tmp/speech-evaluation.json
```

Supplying `--audio` explicitly enables two transcriptions per case, plus optional
live replay. Base speech dependencies and a complete cached model are required.
No recordings are bundled or fetched. The default report omits queries, candidate
text, references, hypotheses, terms and recording paths. `--include-text` opts into
text fields and verbose failure details. Avoid that option for private recordings
if you intend to share a report. Python model-library progress and diagnostics are
suppressed by default; native library failures can still emit low-level stderr.
The JSON report is the supported artifact.

CER uses Unicode NFC and removes whitespace; it retains case, punctuation,
Chinese script, negation and numeric characters. NFKC is deliberately excluded,
so full-width digits do not silently become ASCII digits. English-token WER
scores case-sensitive ASCII English words (including apostrophes) and signed
numeric tokens. Chinese text is covered by CER rather than the English metric.
Terms use exact substring matching for Chinese and word boundaries for ASCII
terms; `John` does not match `Johnson`, and `42` does not match `420`. All required
terms must occur in the reference. Empty-reference rates are `null`; errors and
reference lengths remain visible. Aggregate rates divide summed edit counts by
summed reference counts, including insertions from empty-reference cases.

Cold speech time includes loading, decoding and inference after releasing the
process's model; filesystem caches are retained. Warm time repeats the same clip
with the model loaded. Cold/warm agreement is also reported. These timings are
machine-specific; CPU architecture/core count and the actual selected model/device
are recorded, while GPU identity is explicitly unprobed.

For `live: true`, 16 kHz mono PCM16 WAV snapshots are replayed every three seconds
of audio, without realtime waits. The report gives compute time until a nonempty
preview and compute time for finalization after the last snapshot. These are not
microphone capture, UI, insertion or end-to-end latency measurements. Other
formats report live timing as unavailable. SenseVoice may be selected with
`--backend sensevoice --sensevoice-model /complete/local/model`; it requires
optional FunASR dependencies and does not support prompt/hotword hints.
