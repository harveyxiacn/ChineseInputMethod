"""Offline pinyin/Xiaohe/Jyutping candidates and explicit local personalization."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
import unicodedata
from datetime import datetime

from .conversion import convert_text, ConversionError
from .native_pinyin import decode, bridge_path, predict as statistical_predict
from .lexicon import LexiconStore
from .settings import safe_string, SettingsStore

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
    def __init__(self, dictionary_path: str | Path | None = None, *, lexicon=None, fuzzy_pairs=None):
        self.lexicon = lexicon if lexicon is not None else LexiconStore()
        self.fuzzy_pairs = fuzzy_pairs if fuzzy_pairs is not None else SettingsStore().get('fuzzy_pairs')
        entries = {}
        paths = [DATA / 'starter.tsv', DATA / 'common.tsv']
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
        self.dictionary = 'rime + starter + common phrases' if extended.exists() else 'starter + common phrases'
        self.info = {'dictionary': self.dictionary, 'entries': len(self.entries),
                     'ranking': 'local libime language model with phrase fallback and explicit commit learning',
                     'backend': 'libime' if bridge_path() else 'phrase fallback',
                     'shuangpin': 'Xiaohe', 'jyutping': False}
        self.by_key = {}
        self.by_initials = {}
        for entry in self.entries:
            self.by_key.setdefault(entry.key, []).append(entry)
            self.by_initials.setdefault(entry.initials, []).append(entry)
        self.max_key = max(map(len, self.by_key), default=0)
        self._shuangpin = {}
        for entry in self.entries:
            code = ' '.join(shuangpin_code(s) for s in entry.pinyin.split())
            if code and all(len(s) == 2 for s in code.split()):
                self._shuangpin.setdefault(code.replace(' ', ''), []).append(entry)
        # Real Jyutping data is optional and never inferred from Mandarin.
        self._jyutping = []
        jyutping = DATA / 'jyutping.tsv'
        if jyutping.exists():
            for line in jyutping.read_text(encoding='utf-8').splitlines():
                if not line or line.startswith('#'):
                    continue
                text, romanization, weight = line.split('\t')
                self._jyutping.append((text, romanization, float(weight)))
            self._jyutping.sort(key=lambda item: -item[2])
        self.info['jyutping'] = bool(self._jyutping)
        self._jyut_keys, self._jyut_plain = {}, {}
        for text, romanization, weight in self._jyutping:
            key = romanization.replace(' ', '')
            self._jyut_keys.setdefault(key, []).append((text, romanization, weight))
            self._jyut_plain.setdefault(re.sub('[1-6]', '', key), []).append((text, romanization, weight))



    @staticmethod
    def _boundaries(pinyin):
        lengths = [len(s) for s in pinyin.split()]
        result, total = set(), 0
        for size in lengths[:-1]:
            total += size
            result.add(total)
        return result

    def candidates(self, query: str, limit: int = 9, script: str = 'simplified', *,
                   context: str = '', scheme: str = 'pinyin', fuzzy: bool = False) -> list[dict[str, str]]:
        if not isinstance(query, str) or not isinstance(context, str):
            raise ValueError('query and context must be strings')
        if type(limit) is not int:
            raise ValueError('limit must be an integer')
        if scheme not in ('pinyin', 'shuangpin', 'jyutping'):
            raise ValueError('unknown input scheme')
        if type(fuzzy) is not bool:
            raise ValueError('fuzzy must be a boolean')

        if script not in ('simplified', 'traditional'):
            raise ValueError('script must be simplified or traditional')
        if not safe_string(query, 256) or not query or limit <= 0:
            return []
        limit = min(limit, 50)
        specials = self._special(query, context, script)
        if scheme == 'jyutping':
            if not self._jyutping:
                raise ValueError('Jyutping dictionary is unavailable; install the optional licensed Cantonese dictionary')
            return self._cantonese(query, limit, script, specials)
        normalized = normalize(query)
        key = normalized.replace(' ', '')
        if not key or len(key) > 128:
            return specials[:limit]
        if scheme == 'shuangpin':
            return self._double_pinyin(normalized, key, limit, script, context, specials)

        limit = min(limit, 50)
        boundaries = self._boundaries(normalized)
        found = []
        def add(entry, priority):
            if boundaries <= self._boundaries(entry.pinyin):
                found.append((priority, -entry.weight, entry))
        for entry in self.by_key.get(key, []):
            add(entry, 0)
        if fuzzy:
            variants = fuzzy_keys(normalized, self.fuzzy_pairs)
            for variant in variants:
                for entry in self.by_key.get(variant.replace(' ', ''), []):
                    # Fuzzy pairs can alter character count; explicit syllables
                    # still must align with the entered syllable count.
                    if not boundaries or len(entry.pinyin.split()) == len(normalized.split()):
                        found.append((0.25, -entry.weight, entry))
        native = decode(normalized, context=context, limit=max(limit, 20)) if any(c in key for c in 'aeiouv') or key in self.by_initials else []
        for candidate in native:
            entry = Entry(candidate['text'], self._convert(candidate['text'], 'traditional'),
                          candidate['pinyin'], 0)
            add(entry, 0.5)

        if not boundaries:
            for entry in self.by_initials.get(key, []):
                add(entry, 0.4)
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
        for candidate in specials:
            if candidate['text'] not in seen:
                result.append(candidate)
                seen.add(candidate['text'])
                if len(result) == limit:
                    return result
        for _, _, entry in found:
            text = getattr(entry, script)
            if text not in seen:
                seen.add(text)
                result.append({'text': text, 'pinyin': entry.pinyin})
                if len(result) == limit:
                    break
        return result

    @staticmethod
    def _convert(text, script):
        try:
            return convert_text(text, script)
        except ConversionError:
            return text

    def _special(self, query, context, script):
        result = []
        for entry in self.lexicon.entries():
            pinyin = normalize(entry['pinyin'])
            matches = query == entry['shortcut'] if entry['shortcut'] else False
            normalized_query = normalize(query)
            matches |= bool(pinyin and normalized_query.replace(' ', '') == pinyin.replace(' ', '')
                            and self._boundaries(normalized_query) <= self._boundaries(pinyin))
            if matches:
                result.append((0 if entry['pinned'] else 2, {'text': self._convert(entry['text'], script), 'pinyin': entry['pinyin']}))
        for record in self.lexicon.learned(self._learning_query(query), context):
            result.append((1, {'text': self._convert(record['text'], script), 'pinyin': record['pinyin']}))
        shortcuts = {'/date': datetime.now().strftime('%Y-%m-%d'),
                     '/time': datetime.now().strftime('%H:%M'),
                     '/datetime': datetime.now().strftime('%Y-%m-%d %H:%M'),
                     '/degree': '°', '/celsius': '℃', '/percent': '%',
                     '/sqm': 'm²', '/arrow': '→', '/check': '✓', '/ellipsis': '…'}
        if query in shortcuts:
            result.append((3, {'text': shortcuts[query], 'pinyin': ''}))
        if meaningful_literal(query, phonetic=normalize(query).replace(' ', '') in self.by_key):
            result.append((4, {'text': query, 'pinyin': ''}))
        if re.fullmatch('[A-Za-z]{2,32}', query):
            for word in ENGLISH:
                if word.startswith(query.lower()) and word != query.lower():
                    completed = word.upper() if query.isupper() else word.capitalize() if query[0].isupper() else word
                    result.append((5, {'text': completed, 'pinyin': ''}))
        return [c for _, c in sorted(result, key=lambda item: item[0])]

    def _double_pinyin(self, normalized, key, limit, script, context, specials):
        result = list(specials)
        boundaries = self._boundaries(normalized)
        for entry in self._shuangpin.get(key, []):
            if not boundaries <= set(range(2, len(key), 2)): continue
            result.append({'text': getattr(entry, script), 'pinyin': entry.pinyin})
        for candidate in decode(normalized, scheme='shuangpin', context=context, limit=limit):
            result.append({'text': self._convert(candidate['text'], script), 'pinyin': candidate['pinyin']})
        if len(key) % 2:
            for code, entries in self._shuangpin.items():
                if code.startswith(key):
                    result.extend({'text': getattr(e, script), 'pinyin': e.pinyin} for e in entries[:1])
                    if len(result) >= limit * 2:
                        break
        return unique_candidates(result, limit)

    def _cantonese(self, query, limit, script, specials):
        # Tones 1..6 are part of Jyutping; a toneless query remains usable.
        if not re.fullmatch(r"[A-Za-z1-6 '\s]+", query):
            return specials[:limit]
        normalized = re.sub(r"[ '\s]+", ' ', query.lower()).strip()
        key = normalized.replace(' ', '')
        if not key or len(key) > 128:
            return []
        boundaries = self._boundaries(normalized)
        toneless = not any(c.isdigit() for c in key)
        mapping = self._jyut_plain if toneless else self._jyut_keys
        result = [c for c in specials if c['text'] != query]
        def candidate(row):
            return {'text': self._convert(row[0], script), 'pinyin': row[1]}
        def allowed(code, start=0, end=None):
            if toneless: code = re.sub('[1-6]', '', code)
            permitted = {start + b for b in self._boundaries(code)}
            end = len(key) if end is None else end
            return all(b in permitted for b in boundaries if start < b < end)
        result.extend(candidate(row) for row in mapping.get(key, []) if allowed(row[1]))
        # Compose only real dictionary readings; every input character is consumed.
        beams = {0: [(0.0, '', '')]}
        for start in range(len(key)):
            if start not in beams:
                continue
            for end in range(start + 1, min(len(key), start + 40) + 1):
                for text, code, weight in mapping.get(key[start:end], [])[:5]:
                    if not allowed(code, start, end): continue
                    bucket = beams.setdefault(end, [])
                    for score, previous, phonetic in beams[start]:
                        bucket.append((score + math.log1p(weight) - 12, previous + text,
                                       (phonetic + ' ' + code).strip()))
                    bucket.sort(key=lambda row: -row[0]); del bucket[max(limit, 8):]
        result.extend({'text': self._convert(text, script), 'pinyin': code}
                      for _, text, code in beams.get(len(key), []))
        for comparison, rows in mapping.items():
            if comparison.startswith(key) and comparison != key:
                result.extend(candidate(row) for row in rows[:1] if allowed(row[1]))
                if len(result) >= limit * 3:
                    break
        return unique_candidates(result, limit)

    def _learning_query(self, query):
        if not isinstance(query, str):
            raise ValueError('query must be a string')
        phonetic = normalize(query).replace(' ', '') in self.by_key
        return query if meaningful_literal(query, phonetic=phonetic) else normalize(query) or query

    def learn(self, query, text, context=''):
        self.lexicon.learn(self._learning_query(query), text, context)

    def predict(self, context, limit=5, script='simplified'):
        if script not in ('simplified', 'traditional'):
            raise ValueError('script must be simplified or traditional')
        personal = self.lexicon.predict(context, limit)
        statistical = statistical_predict(context, limit) if context and limit else []
        candidates = [{'text': self._convert(c['text'], script), 'pinyin': c['pinyin']}
                      for c in personal + statistical if c['text'] != context]
        return unique_candidates(candidates, limit)


ENGLISH = ('hello', 'help', 'welcome', 'world', 'python', 'project', 'program',
           'meeting', 'message', 'thanks', 'thank', 'tomorrow', 'today', 'version',
           'email', 'input', 'computer', 'configuration', 'settings', 'software')


def meaningful_literal(query, *, phonetic=False):
    if not query or len(query) > 256 or any(ord(c) < 32 for c in query):
        return False
    if any(c in '<>' for c in query):
        return False
    if phonetic and re.fullmatch(r'(?:[A-Za-züÜ:]+[0-5])+', query):
        return False
    if re.fullmatch('[A-Za-z]*u:', query, re.IGNORECASE):
        return False
    if query.lower() in ENGLISH:
        return True
    # Explicit Latin case, mixed scripts, and structured identifiers preserve
    # exactly what was typed. Pure unknown lowercase pinyin stays invalid.
    if re.fullmatch('[A-Za-z0-9_]+', query) and re.search('[A-Z]', query):
        return True
    if re.search('[A-Za-z]', query) and re.search('[\u3400-\u9fff]', query):
        return True
    if re.fullmatch('[0-9]+(?:[.,][0-9]+)*', query):
        return True
    if re.search('[A-Za-z0-9]', query) and re.search(r'[@/\\._:=+-]', query):
        return True
    return bool(re.fullmatch('[A-Za-z_][A-Za-z_0-9]*', query) and
                re.search('[0-9]', query))


def unique_candidates(candidates, limit):
    result, seen = [], set()
    for candidate in candidates:
        if candidate['text'] not in seen:
            seen.add(candidate['text'])
            result.append(candidate)
            if len(result) >= limit:
                break
    return result


def shuangpin_code(syllable):
    # Xiaohe double-pinyin profile, matching libime's built-in Xiaohe table.
    if syllable in {'a', 'o', 'e'}:
        return syllable * 2
    if syllable in {'ai', 'an', 'ao', 'ei', 'en', 'er', 'ou'}:
        return syllable
    if syllable == 'ang':
        return 'ah'
    if syllable == 'eng':
        return 'eg'
    initial = next((i for i in ('zh', 'ch', 'sh') if syllable.startswith(i)), syllable[:1])
    final = syllable[len(initial):]
    initials = {'zh': 'v', 'ch': 'i', 'sh': 'u'}
    finals = {'ai':'d','an':'j','ang':'h','ao':'c','ei':'w','en':'f','eng':'g',
              'ia':'x','ian':'m','iang':'l','iao':'n','ie':'p','in':'b','ing':'k',
              'iong':'s','iu':'q','ong':'s','ou':'z','ua':'x','uai':'k','uan':'r',
              'uang':'l','ue':'t','ve':'t','ui':'v','un':'y','uo':'o'}
    return initials.get(initial, initial) + finals.get(final, final)


def fuzzy_keys(query, pairs=None):
    variants = set()
    # One changed syllable per variant avoids combinatorial typo expansion.
    syllables = query.split()
    pairs = pairs if pairs is not None else [('zh','z'), ('ch','c'), ('sh','s'), ('n','l'), ('ang','an'), ('eng','en'), ('ing','in')]
    for i, syllable in enumerate(syllables):
        for left, right in pairs:
            final = left in {'ang','an','eng','en','ing','in'} or right in {'ang','an','eng','en','ing','in'}
            for a, b in ((left,right), (right,left)):
                parts = syllables.copy()
                if final and syllable.endswith(a):
                    parts[i] = syllable[:-len(a)] + b
                elif not final and syllable.startswith(a) and not (len(a) == 1 and syllable.startswith(a + 'h')):
                    parts[i] = b + syllable[len(a):]
                else:
                    continue
                variants.add(' '.join(parts))
    return variants
