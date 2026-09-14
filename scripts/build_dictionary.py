#!/usr/bin/env python3
"""Convert Rime Luna Pinyin into our explicit simplified/traditional TSV.

Install opencc-python-reimplemented first. Source and upstream licenses remain
in ime/data alongside the converted data so its provenance is inspectable.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://raw.githubusercontent.com/rime/rime-luna-pinyin/master/'


def build(source: Path, output: Path):
    try:
        from opencc import OpenCC
    except ImportError:
        raise SystemExit('Install opencc-python-reimplemented before building the dictionary.')
    convert = OpenCC('t2s').convert
    records = {}
    in_entries = False
    for line in source.read_text(encoding='utf-8').splitlines():
        if line.strip() == '...':
            in_entries = True
            continue
        if not in_entries or not line or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) < 2 or not re.fullmatch(r'[a-z]+(?: [a-z]+)*', fields[1]):
            continue
        traditional, pinyin = fields[:2]
        simple = convert(traditional)
        # Rime's preset vocabulary supplies additional ranking in its own engine.
        # This standalone decoder uses supplied weights or modest default weights.
        weight = 50
        if len(fields) > 2:
            try:
                weight = max(1, min(1000, float(fields[2].rstrip('%'))))
            except ValueError:
                pass
        records[(simple, traditional, pinyin)] = weight
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8') as stream:
        stream.write('# Derived from Rime Luna Pinyin; see ATTRIBUTION.md and LICENSE.rime.\n')
        for (simple, traditional, pinyin), weight in records.items():
            stream.write(f'{simple}\t{traditional}\t{pinyin}\t{weight}\n')
    print(f'Wrote {len(records)} entries to {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Download current Rime source and license over HTTPS')
    parser.add_argument('--source', type=Path, default=ROOT / 'ime/data/luna_pinyin.dict.yaml')
    parser.add_argument('--output', type=Path, default=ROOT / 'ime/data/rime.tsv')
    args = parser.parse_args()
    if args.download:
        args.source.parent.mkdir(parents=True, exist_ok=True)
        for name, destination in [('luna_pinyin.dict.yaml', args.source), ('LICENSE', args.source.parent / 'LICENSE.rime')]:
            with urllib.request.urlopen(BASE + name, timeout=60) as response:
                destination.write_bytes(response.read())
    build(args.source, args.output)


if __name__ == '__main__':
    main()
