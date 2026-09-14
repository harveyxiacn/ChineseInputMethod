"""Small offline pinyin decoder; optionally extended by a converted Rime dictionary."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
import unicodedata

DATA = Path(__file__).parent / 'data'


def normalize(query: str) -> str:
    """Fold tones while preserving explicit syllable boundaries and ü (as v)."""
    query = unicodedata.normalize('NFC', query.lower()).replace('u:', 'v')
    query = ''.join('v' if c in 'üǖǘǚǜ' else c for c in query)
    query = ''.join(c for c in unicodedata.normalize('NFD', query)
                    if not unicodedata.combining(c))
    query = re.sub(r'[0-5]', ' ', query)
    query = re.sub(r"['’\s]+", ' ', query).strip()
    if not re.fullmatch(r'[a-z ]*', query):
        return ''
    return query


@dataclass(frozen=True)
class Entry:
    simplified: str
    traditional: str
    pinyin: str
    weight: float

    @property
    def key(self):
        return self.pinyin.replace(' ', '')

    @property
    def initials(self):
        return ''.join(s[0] for s in self.pinyin.split())


class PinyinEngine:
    def __init__(self, dictionary_path: str | Path | None = None):
        entries = {}
        paths = [DATA / 'starter.tsv']
        extended = Path(dictionary_path) if dictionary_path else DATA / 'rime.tsv'
        if extended.exists():
            paths.append(extended)
        elif dictionary_path:
            raise FileNotFoundError(extended)
        for path in paths:
            for line in path.read_text(encoding='utf-8').splitlines():
                if not line.strip() or line.startswith('#'):
                    continue
                simple, traditional, pinyin, weight = line.split('\t')
                entry = Entry(simple, traditional, normalize(pinyin), float(weight))
                identity = (simple, entry.pinyin)
                if identity not in entries or entry.weight > entries[identity].weight:
                    entries[identity] = entry
        self.entries = sorted(entries.values(), key=lambda e: -e.weight)
        self.dictionary = 'rime + starter' if len(paths) > 1 else 'starter'
        self.info = {'dictionary': self.dictionary, 'entries': len(self.entries),
                     'ranking': 'basic phrase weights; no personalized learning'}
        self.by_key = {}
        self.by_initials = {}
        for entry in self.entries:
            self.by_key.setdefault(entry.key, []).append(entry)
            self.by_initials.setdefault(entry.initials, []).append(entry)
        self.max_key = max(map(len, self.by_key), default=0)

    @staticmethod
    def _boundaries(pinyin):
        lengths = [len(s) for s in pinyin.split()]
        result, total = set(), 0
        for size in lengths[:-1]:
            total += size
            result.add(total)
        return result

    def candidates(self, query: str, limit: int = 9, script: str = 'simplified') -> list[dict[str, str]]:
        if script not in ('simplified', 'traditional'):
            raise ValueError('script must be simplified or traditional')
        normalized = normalize(query)
        key = normalized.replace(' ', '')
        if not key or len(key) > 128 or limit <= 0:
            return []
        limit = min(limit, 50)
        boundaries = self._boundaries(normalized)
        found = []
        def add(entry, priority):
            if boundaries <= self._boundaries(entry.pinyin):
                found.append((priority, -entry.weight, entry))
        for entry in self.by_key.get(key, []):
            add(entry, 0)
        if not boundaries:
            for entry in self.by_initials.get(key, []):
                add(entry, 2)
        # Prefix completions remain below exact and composed full-input matches.
        for entry in self.entries:
            if entry.key.startswith(key) and entry.key != key:
                add(entry, 3)
        if len(key) > 1:
            # Beam decoder: a full candidate must consume every input character.
            beams = {0: [(0.0, '', '', '')]}
            for start in range(len(key)):
                if start not in beams:
                    continue
                for end in range(start + 1, min(len(key), start + self.max_key) + 1):
                    for entry in self.by_key.get(key[start:end], [])[:5]:
                        allowed = {start + b for b in self._boundaries(entry.pinyin)}
                        if any(start < b < end and b not in allowed for b in boundaries):
                            continue
                        bucket = beams.setdefault(end, [])
                        for score, simple, traditional, pinyin in beams[start]:
                            bucket.append((score + math.log1p(entry.weight) - 12,
                                           simple + entry.simplified,
                                           traditional + entry.traditional,
                                           (pinyin + ' ' + entry.pinyin).strip()))
                        bucket.sort(key=lambda item: -item[0])
                        del bucket[max(limit, 8):]
            for score, simple, traditional, pinyin in beams.get(len(key), []):
                found.append((1, -score, Entry(simple, traditional, pinyin, 0)))
        found.sort(key=lambda item: (item[0], item[1]))
        result, seen = [], set()
        for _, _, entry in found:
            text = getattr(entry, script)
            if text not in seen:
                seen.add(text)
                result.append({'text': text, 'pinyin': entry.pinyin})
                if len(result) == limit:
                    break
        return result
