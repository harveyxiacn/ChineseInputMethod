"""Validated local preferences, with atomic writes and process-safe merges."""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit

DEFAULTS = {
    'language': 'zh', 'script': 'simplified', 'model': 'large-v3', 'device': 'cpu',
    'microphone': '', 'hotkey': 'Ctrl+Alt+Space', 'compact': False, 'theme': 'system',
    'font_size': 16, 'candidate_count': 9, 'input_scheme': 'pinyin', 'fuzzy': False,
    'fuzzy_pairs': [['zh', 'z'], ['ch', 'c'], ['sh', 's'], ['n', 'l'], ['ang', 'an'], ['eng', 'en'], ['ing', 'in']],
    'save_draft': False, 'auto_insert': False, 'push_to_talk': False, 'overlay': False,
    'locale': 'zh', 'input_mode': 'chinese', 'app_profiles': {}, 'backend': 'whisper', 'sensevoice_model': '',
    'initial_prompt': '', 'hotwords': [], 'llm_enabled': False,
    'llm_url': 'http://127.0.0.1:8080', 'llm_model': '',
}
VALID_VALUES = {
    'language': {'zh', 'yue', 'en', 'auto'}, 'script': {'simplified', 'traditional'},
    'device': {'auto', 'cpu', 'cuda'}, 'input_scheme': {'pinyin', 'shuangpin', 'jyutping'},
    'theme': {'system', 'light', 'dark', 'contrast'}, 'input_mode': {'chinese', 'english'}, 'locale': {'zh', 'en'},
    'backend': {'whisper', 'sensevoice'},
}
BOOL_KEYS = {key for key, value in DEFAULTS.items() if type(value) is bool}
INT_RANGES = {'font_size': (8, 48), 'candidate_count': (1, 50)}
STRING_MAX = {'model': 4096, 'microphone': 256, 'hotkey': 80, 'sensevoice_model': 4096,
              'initial_prompt': 2000, 'llm_url': 256, 'llm_model': 128}


def config_directory() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming') / 'shuangsheng'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/shuangsheng'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'shuangsheng'


def default_settings_path() -> Path:
    return config_directory() / 'settings.json'


def safe_string(value, maximum, *, empty=True, multiline=False):
    return (isinstance(value, str) and (empty or bool(value.strip())) and len(value) <= maximum
            and not any((ord(c) < 32 and (not multiline or c not in '\n\t')) or
                        0xD800 <= ord(c) <= 0xDFFF or ord(c) == 127 for c in value))


def validate_settings(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError('settings must be an object')
    for key, value in data.items():
        if key not in DEFAULTS:
            raise ValueError(f'unknown setting: {key}')
        valid = True
        if key in VALID_VALUES:
            valid = isinstance(value, str) and value in VALID_VALUES[key]
        elif key in BOOL_KEYS:
            valid = type(value) is bool
        elif key in INT_RANGES:
            low, high = INT_RANGES[key]
            valid = type(value) is int and low <= value <= high
        elif key in STRING_MAX:
            valid = safe_string(value, STRING_MAX[key], empty=key not in {'model', 'hotkey'},
                                multiline=key == 'initial_prompt')
            if key == 'llm_url' and valid:
                try:
                    url = urlsplit(value)
                    valid = (url.scheme == 'http' and url.hostname in {'127.0.0.1', 'localhost', '::1'}
                             and url.port is not None and 0 < url.port <= 65535
                             and not url.username and not url.password and not url.query
                             and not url.fragment and url.path in {'', '/', '/v1', '/v1/'})
                except ValueError:
                    valid = False
        elif key == 'fuzzy_pairs':
            valid = isinstance(value, list) and len(value) <= 16 and all(
                isinstance(pair, list) and len(pair) == 2 and all(
                    isinstance(part, str) and part.isascii() and part.isalpha() and part.islower() and len(part) <= 4
                    for part in pair) and pair[0] != pair[1] for pair in value)
        elif key == 'hotwords':
            valid = isinstance(value, list) and len(value) <= 100 and all(
                safe_string(term, 128, empty=False) for term in value)
        elif key == 'app_profiles':
            valid = isinstance(value, dict) and len(value) <= 100
            if valid:
                for name, profile in value.items():
                    if (not safe_string(name, 4096, empty=False) or not isinstance(profile, dict)
                            or 'app_profiles' in profile):
                        valid = False
                        break
                    validate_settings(profile)
        if not valid:
            raise ValueError(f'invalid setting: {key}')


@contextmanager
def file_lock(path):
    """Advisory lock shared by cooperating desktop/server/native writers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.lock').open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0); handle.write(b'\0'); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('IME_SETTINGS_PATH') or default_settings_path()).expanduser()
        self._lock = threading.RLock()

    def _load(self):
        settings = deepcopy(DEFAULTS)
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                for key, value in data.items():
                    try: validate_settings({key: value})
                    except ValueError: continue
                    settings[key] = value
        except (OSError, ValueError, UnicodeError):
            pass
        return settings

    def snapshot(self):
        with self._lock:
            return self._load()

    def get(self, key, default=None):
        return self.snapshot().get(key, default)

    def update(self, changes):
        validate_settings(changes)
        with self._lock, file_lock(self.path):
            settings = self._load()
            settings.update(deepcopy(changes))
            atomic_write_json(self.path, settings)
            return deepcopy(settings)
