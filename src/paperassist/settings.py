"""User settings stored beside the library: contact email, API keys and source switches."""

import base64
import ctypes
from ctypes import wintypes
import json
import os
import re
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from . import paths

PROVIDER_NAMES = {
    'crossref': 'Crossref',
    'ads': 'NASA ADS',
    'arxiv': 'arXiv',
    'openalex': 'OpenAlex',
    'semantic_scholar': 'Semantic Scholar',
    'core': 'CORE',
    'unpaywall': 'Unpaywall',
}
SOURCES = tuple(PROVIDER_NAMES)
KEYS = ('ads', 'core', 'semantic_scholar', 'openalex')
# What a source cannot run without. OpenAlex and Semantic Scholar keys and the Crossref
# email only raise rate limits, so they are optional and not listed here.
NEEDS = {'ads': 'token', 'core': 'key', 'unpaywall': 'email'}
ENVIRONMENT = {'email': 'PAPERASSIST_EMAIL', 'openalex': 'OPENALEX_API_KEY'}
ZOOM_CHOICES = (0.8, 0.9, 1, 1.1, 1.25, 1.5)
_WRITE = threading.Lock()
_DPAPI_PREFIX = 'dpapi:'


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _dpapi_libs():
    return ctypes.windll.crypt32, ctypes.windll.kernel32


def _use_dpapi() -> bool:
    return sys.platform == 'win32'


def _dpapi_protect(data: bytes) -> bytes:
    crypt32, kernel32 = _dpapi_libs()
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data)) if data else ctypes.create_string_buffer(1)
    in_blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)):
        raise (ctypes.WinError() if hasattr(ctypes, 'WinError') else OSError('CryptProtectData failed'))
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    crypt32, kernel32 = _dpapi_libs()
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data)) if data else ctypes.create_string_buffer(1)
    in_blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)):
        raise (ctypes.WinError() if hasattr(ctypes, 'WinError') else OSError('CryptUnprotectData failed'))
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _protect_secret(plaintext: str) -> str:
    if not plaintext or not _use_dpapi():
        return plaintext
    return _DPAPI_PREFIX + base64.b64encode(_dpapi_protect(plaintext.encode('utf-8'))).decode('ascii')


def _unprotect_secret(stored: str) -> str:
    if not isinstance(stored, str) or not stored:
        return ''
    if not stored.startswith(_DPAPI_PREFIX):
        return stored
    if not _use_dpapi():
        return ''
    try:
        raw = base64.b64decode(stored[len(_DPAPI_PREFIX):], validate=True)
        return _dpapi_unprotect(raw).decode('utf-8')
    except Exception:
        return ''


def _decrypt_loaded(data):
    if not isinstance(data, dict):
        return None
    keys = data.get('keys')
    if not isinstance(keys, dict):
        return data
    plain = {}
    for name, value in keys.items():
        secret = _unprotect_secret(value)
        if secret:
            plain[name] = secret
    return {**data, 'keys': plain}


class Settings:
    """Stored values with environment variables as fallback for empty ones."""

    def __init__(self, data=None):
        self.data = data or {}

    @property
    def email(self):
        return (self.data.get('email') or os.environ.get(ENVIRONMENT['email'], '')).strip()

    @property
    def proxy(self):
        return self.data.get('proxy') or ''

    @property
    def inbox(self):
        return self.data.get('inbox') or ''

    def key(self, name):
        return ((self.data.get('keys') or {}).get(name) or os.environ.get(ENVIRONMENT.get(name, ''), '')).strip()

    def enabled(self, source):
        return (self.data.get('enabled') or {}).get(source, True)

    def configured(self, source):
        """Whether the source can run now; anonymous access counts."""
        return bool({'ads': self.key('ads'), 'core': self.key('core'), 'unpaywall': self.email}.get(source, True))

    def providers(self):
        return {name: {'configured': self.configured(name), 'enabled': self.enabled(name), 'needs': NEEDS.get(name)}
                for name in SOURCES}

    def view(self):
        """The public form: a key is never returned, only its last characters."""
        return {'email': self.data.get('email') or '', 'proxy': self.proxy, 'inbox': self.inbox,
                'enabled': {name: self.enabled(name) for name in SOURCES},
                'keys': {name: {'configured': bool(self.key(name)), 'hint': self.key(name)[-4:]} for name in KEYS}}


def load(data_dir):
    if data_dir:
        try:
            data = json.loads((Path(data_dir) / 'settings.json').read_text(encoding='utf-8'))
            return Settings(_decrypt_loaded(data))
        except (OSError, ValueError):
            pass
    return Settings()


def _email(value):
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value) or len(value) > 254:
        raise ValueError('Email must look like name@example.org')
    return value


def _proxy(value):
    parts = urlsplit(value.replace('{url}', 'x'))
    if parts.scheme != 'https' or not parts.hostname or re.search(r'\s', value) or len(value) > 500:
        raise ValueError('Library proxy must be an https URL, optionally containing {url}')
    return value


def _inbox(value, data_dir):
    try:
        path = Path(value).expanduser()
    except RuntimeError:  # "~" with no known home folder
        raise ValueError('The folder for manual downloads must be an absolute path') from None
    if not path.is_absolute():
        raise ValueError('The folder for manual downloads must be an absolute path')
    path = path.resolve()
    if not path.is_dir():
        raise ValueError('The folder for manual downloads does not exist')
    library = Path(data_dir).resolve()
    if path == library or library in path.parents:
        raise ValueError('The folder for manual downloads cannot be inside the library')
    return str(path)


def _key(value):
    if not re.fullmatch(r'\S{8,256}', value):
        raise ValueError('A key must be 8 to 256 characters without spaces')
    return value


def _merge(stored, changes, data_dir):
    for name in ('enabled', 'keys'):
        if name in changes and not isinstance(changes[name], (dict, type(None))):
            raise ValueError(f'{name} must be an object')
    unknown = set(changes) - {'email', 'proxy', 'inbox', 'enabled', 'keys'}
    if unknown:
        raise ValueError('Unknown setting: ' + ', '.join(sorted(map(str, unknown))))
    data = {**stored, 'enabled': dict(stored.get('enabled') or {}), 'keys': dict(stored.get('keys') or {})}

    def put(target, name, value, check):
        if value is not None and not isinstance(value, str):
            raise ValueError(f'{name} must be text or null')
        value = (value or '').strip()
        if value:
            target[name] = check(value)
        else:
            target.pop(name, None)

    for name, check in (('email', _email), ('proxy', _proxy)):
        if name in changes:
            put(data, name, changes[name], check)
    if 'inbox' in changes:
        put(data, 'inbox', changes['inbox'], lambda value: _inbox(value, data_dir))
    for name, value in (changes.get('enabled') or {}).items():
        if name not in SOURCES or not isinstance(value, bool):
            raise ValueError('enabled takes true or false for known sources')
        data['enabled'][name] = value
    for name, value in (changes.get('keys') or {}).items():
        if name not in KEYS:
            raise ValueError(f'Unknown key: {name}')
        put(data['keys'], name, value, _key)
    return data


def update(data_dir, changes):
    """Validate, merge and save atomically; the file holds secrets, so only the owner may read it."""
    path = Path(data_dir) / 'settings.json'
    with _WRITE:
        data = _merge(load(data_dir).data, changes, data_dir)
        stored = dict(data)
        if stored.get('keys'):
            stored['keys'] = {name: _protect_secret(value) for name, value in stored['keys'].items()}
        paths.write_json_atomic(path, stored, mode=0o600)
    return Settings(data)


def preferences():
    """Interface choices follow the user across libraries."""
    value = paths.load_config().get('preferences')
    return value if isinstance(value, dict) else {}


def update_preferences(changes):
    checks = {'ui_zoom': lambda value: type(value) in (int, float) and value in ZOOM_CHOICES,
              'quick_view_layout': lambda value: value in ('stacked', 'side'),
              'outline_expanded': lambda value: isinstance(value, bool),
              'details_expanded': lambda value: isinstance(value, bool),
              'restore_session': lambda value: isinstance(value, bool)}
    if not isinstance(changes, dict) or set(changes) - checks.keys():
        raise ValueError('Unknown interface preference')
    if any(not checks[name](value) for name, value in changes.items()):
        raise ValueError('Invalid interface preference')

    def merge(data):
        current = data.get('preferences')
        if not isinstance(current, dict):
            current = {}
        return {**data, 'preferences': {**current, **changes}}

    return paths.update_config(merge)['preferences']
