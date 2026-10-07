"""Build the licensed Cantonese dictionary from verified, explicitly fetched inputs.

No downloads occur. Supply the checkout/files for the pinned upstream commit.
Only rows with real romanizations are imported; missing readings are never guessed.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '259f0e48bba840c3a2e0d117539e96937f3d89bc'
CHECKSUMS = {
    'jyut6ping3.chars.dict.yaml': '9b053a594c80eae76545bcdd83239884a49b1ba48b89ca0ab9e8daac79f33013',
    'jyut6ping3.words.dict.yaml': '54d174ad2bb997e4a678b7b076b84e4dc5914481b32467cdea3b43e88b7d5474',
}


def build(source: Path, output: Path):
    frequencies = {}
    essay = ROOT / 'native/rime/essay.txt'
    if essay.exists():
        for line in essay.read_text(encoding='utf-8').splitlines():
            word, count = line.split('\t')
            frequencies[word] = int(count)
    rows = {}
    for filename, expected in CHECKSUMS.items():
        data = (source / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f'{filename}: checksum does not match pinned commit {COMMIT}')
        body = False
        for line in data.decode('utf-8').splitlines():
            if line == '...':
                body = True
                continue
            if not body or not line or line.startswith('#'):
                continue
            fields = line.split('\t')
            if len(fields) < 2:
                continue
            text, code = fields[:2]
            if not re.fullmatch(r'[a-z]+[1-6](?: [a-z]+[1-6])*', code):
                continue
            ratio = 1.0
            if len(fields) > 2 and fields[2].endswith('%'):
                ratio = float(fields[2][:-1]) / 100
            weight = max(1.0, frequencies.get(text, 50) * ratio)
            rows[(text, code)] = max(rows.get((text, code), 0), weight)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# CanCLID rime-cantonese {COMMIT}; CC-BY-4.0; see LICENSE.cantonese',
             '# Traditional text<TAB>real Jyutping with tones<TAB>frequency weight']
    lines.extend(f'{text}\t{code}\t{weight:g}' for (text, code), weight in
                 sorted(rows.items(), key=lambda item: (-item[1], item[0])))
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'ime/data/jyutping.tsv')
    args = parser.parse_args()
    print(f'Wrote {build(args.source, args.output)} verified Jyutping entries to {args.output}')
