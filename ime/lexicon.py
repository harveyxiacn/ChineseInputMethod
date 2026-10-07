"""Private user terms and explicit commit history; no audio or draft retention."""
from __future__ import annotations
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import uuid

from .settings import atomic_write_json, config_directory, file_lock, safe_string

MAX_LEARN_RECORDS = 2000
CONTEXT_MAX = 120


def default_lexicon_path():
    return config_directory() / 'lexicon.json'


def validate_entry(text, pinyin='', shortcut='', pinned=False):
    if not safe_string(text, 512, empty=False, multiline=True):
        raise ValueError('text must be a nonempty safe string of at most 512 characters')
    if not safe_string(pinyin, 256):
        raise ValueError('pinyin must be a safe string of at most 256 characters')
    if not safe_string(shortcut, 64) or any(c.isspace() for c in shortcut):
        raise ValueError('shortcut must be a safe string without whitespace')
    if type(pinned) is not bool:
        raise ValueError('pinned must be a boolean')


def validate_record(query, text, context):
    validate_entry(text)
    if not safe_string(query, 256, empty=False):
        raise ValueError('query must be a nonempty safe string of at most 256 characters')
    if not safe_string(context, 8192, multiline=True):
        raise ValueError('context must be a safe string of at most 8192 characters')


class LexiconStore:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('IME_LEXICON_PATH') or default_lexicon_path()).expanduser()
        self._lock = threading.RLock()

    def _load(self):
        entries, history = [], []
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return entries, history
            seen_ids, seen_texts = set(), set()
            for row in data.get('entries', []) if isinstance(data.get('entries'), list) else []:
                try:
                    if not isinstance(row, dict): continue
                    entry = {key: row.get(key, default) for key, default in
                             [('id',''), ('text',''), ('pinyin',''), ('shortcut',''), ('pinned',False)]}
                    validate_entry(entry['text'], entry['pinyin'], entry['shortcut'], entry['pinned'])
                    if not safe_string(entry['id'], 128, empty=False): continue
                    if entry['id'] in seen_ids or entry['text'] in seen_texts: continue
                    seen_ids.add(entry['id']); seen_texts.add(entry['text']); entries.append(entry)
                except ValueError: continue
            for row in data.get('learn', []) if isinstance(data.get('learn'), list) else []:
                try:
                    if not isinstance(row, dict): continue
                    validate_record(row.get('query'), row.get('text'), row.get('context', ''))
                    count = row.get('count', 1)
                    if type(count) is not int or not 0 < count <= 10**9: continue
                    history.append({'query': row['query'], 'text': row['text'],
                                    'context': row.get('context', '')[-CONTEXT_MAX:], 'count': count})
                except ValueError: continue
        except (OSError, ValueError, UnicodeError):
            pass
        return entries, history[-MAX_LEARN_RECORDS:]

    def _save(self, entries, history):
        atomic_write_json(self.path, {'entries': entries, 'learn': history[-MAX_LEARN_RECORDS:]})

    def entries(self):
        with self._lock:
            return deepcopy(self._load()[0])

    def upsert(self, text, pinyin='', shortcut='', pinned=False, id=None):
        validate_entry(text, pinyin, shortcut, pinned)
        if id is not None and not safe_string(id, 128, empty=False):
            raise ValueError('id must be a nonempty safe string')
        with self._lock, file_lock(self.path):
            entries, history = self._load()
            existing = next((e for e in entries if e['id'] == id), None) if id is not None else next(
                (e for e in entries if e['text'] == text), None)
            if id is not None and existing is None:
                raise ValueError('unknown lexicon id')
            if existing is not None and any(e is not existing and e['text'] == text for e in entries):
                raise ValueError('text already belongs to another entry')
            entry = {'id': existing['id'] if existing else str(uuid.uuid4()),
                     'text': text, 'pinyin': pinyin, 'shortcut': shortcut, 'pinned': pinned}
            if existing:
                entries[entries.index(existing)] = entry
            else:
                if len(entries) >= 10000: raise ValueError('lexicon entry limit reached')
                entries.append(entry)
            self._save(entries, history)
            return deepcopy(entry)

    def delete(self, id):
        if not safe_string(id, 128, empty=False): raise ValueError('invalid lexicon id')
        with self._lock, file_lock(self.path):
            entries, history = self._load()
            filtered = [e for e in entries if e['id'] != id]
            if len(filtered) == len(entries): return False
            self._save(filtered, history)
            return True

    def learn(self, query, text, context=''):
        validate_record(query, text, context)
        context = context[-CONTEXT_MAX:]
        with self._lock, file_lock(self.path):
            entries, history = self._load()
            record = next((r for r in history if (r['query'], r['text'], r['context']) ==
                           (query, text, context)), None)
            if record:
                history.remove(record)
                record['count'] = min(record['count'] + 1, 10**9)
            else:
                record = {'query': query, 'text': text, 'context': context, 'count': 1}
            history.append(record)
            self._save(entries, history)

    def learned(self, query, context=''):
        if not safe_string(query, 256) or not safe_string(context, 8192, multiline=True):
            raise ValueError('invalid query or context')
        context = context[-CONTEXT_MAX:]
        with self._lock:
            grouped = {}
            for record in self._load()[1]:
                if record['query'] != query: continue
                row = grouped.setdefault(record['text'], {'text': record['text'], 'pinyin': query,
                                                         'count': 0, 'context_count': 0})
                row['count'] += record['count']
                if record['context'] == context: row['context_count'] += record['count']
            return sorted(grouped.values(), key=lambda row: (-row['context_count'], -row['count'], row['text']))

    def predict(self, context, limit=5):
        if type(limit) is not int or not 0 <= limit <= 50: raise ValueError('limit must be an integer from 0 to 50')
        if not safe_string(context, 8192, multiline=True): raise ValueError('invalid context')
        if limit == 0: return []
        context = context[-CONTEXT_MAX:]
        with self._lock:
            entries, history = self._load()
            if not context:
                return [{'text': e['text'], 'pinyin': e['pinyin']} for e in entries if e['pinned']][:limit]
            grouped = {}
            for record in history:
                # Exact contexts prevent negated/contradictory phrases from
                # accidentally inheriting positive intent through suffix overlap.
                if record['context'] != context: continue
                row = grouped.setdefault(record['text'], {'text': record['text'], 'pinyin': record['query'], 'count': 0})
                row['count'] += record['count']
            ranked = sorted(grouped.values(), key=lambda row: (-row['count'], row['text']))[:limit]
            return [{'text': row['text'], 'pinyin': row['pinyin']} for row in ranked]
