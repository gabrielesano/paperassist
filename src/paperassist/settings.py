"""User settings stored beside the library: contact email, API keys and source switches."""

import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from . import paths

SOURCES = ('crossref', 'ads', 'arxiv', 'openalex', 'semantic_scholar', 'core', 'unpaywall')
KEYS = ('ads', 'core', 'semantic_scholar', 'openalex')
# What a source cannot run without. OpenAlex and Semantic Scholar keys and the Crossref
# email only raise rate limits, so they are optional and not listed here.
NEEDS = {'ads': 'token', 'core': 'key', 'unpaywall': 'email'}
ENVIRONMENT = {'email': 'PAPERASSIST_EMAIL', 'openalex': 'OPENALEX_API_KEY'}
_WRITE = threading.Lock()


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
            return Settings(data if isinstance(data, dict) else None)
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
    for name in ('enabled', 'keys'):
        if name in changes and not isinstance(changes[name], (dict, type(None))):
            raise ValueError(f'{name} must be an object')
    return data


def update(data_dir, changes):
    """Validate, merge and save atomically; the file holds secrets, so only the owner may read it."""
    path = Path(data_dir) / 'settings.json'
    with _WRITE:
        data = _merge(load(data_dir).data, changes, data_dir)
        temporary = path.with_name(f'.settings.{os.getpid()}.tmp')
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=2)
        os.replace(temporary, path)
    return Settings(data)


def preferences():
    """Interface choices follow the user across libraries; missing keys allow browser migration."""
    value = paths.load_config().get('preferences')
    return value if isinstance(value, dict) else {}


def update_preferences(changes, only_if_missing=False):
    checks = {'ui_zoom': lambda value: type(value) in (int, float) and value in (.8, .9, 1, 1.1, 1.25, 1.5),
              'quick_view_layout': lambda value: value in ('stacked', 'side'),
              'outline_expanded': lambda value: isinstance(value, bool),
              'details_expanded': lambda value: isinstance(value, bool)}
    if not isinstance(changes, dict) or set(changes) - checks.keys():
        raise ValueError('Unknown interface preference')
    if any(not checks[name](value) for name, value in changes.items()):
        raise ValueError('Invalid interface preference')

    def merge(data):
        current = data.get('preferences')
        if not isinstance(current, dict):
            current = {}
        updated = {**changes, **current} if only_if_missing else {**current, **changes}
        return {**data, 'preferences': updated}

    return paths.update_config(merge)['preferences']
