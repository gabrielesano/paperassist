"""Where the library lives: the user's explicit choice, then the saved one, then the platform default."""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

# Why the controls in the app are fixed when the user already decided at start-up.
FIXED = {
    'option': 'Set by --data-dir when PaperAssist starts.',
    'environment': 'Set by PAPERASSIST_DATA_DIR.',
}
_CONFIG_WRITE = threading.Lock()


def _xdg(variable, fallback):
    base = os.environ.get(variable)
    # The XDG spec ignores relative values.
    return (Path(base) if base and os.path.isabs(base) else Path.home().joinpath(*fallback)) / 'paperassist'


def platform_data_dir():
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming'
        return Path(base) / 'PaperAssist'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'PaperAssist'
    return _xdg('XDG_DATA_HOME', ('.local', 'share'))


def config_file():
    """Kept outside every library so moving or deleting one never loses the choice."""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local'
        return Path(base) / 'PaperAssist' / 'config.json'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Preferences' / 'PaperAssist' / 'config.json'
    return _xdg('XDG_CONFIG_HOME', ('.config',)) / 'config.json'


def load_config():
    try:
        data = json.loads(config_file().read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def saved_library():
    try:
        library = load_config()['library']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return Path(library) if isinstance(library, str) and os.path.isabs(library) else None


def save_library(directory):
    update_config(lambda data: {**data, 'library': str(directory)})


def update_config(change):
    with _CONFIG_WRITE:
        data = change(load_config())
        _save_config(data)
        return data


def _save_config(data):
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace atomically so a crash never leaves half a file that hides the choice.
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='config.', suffix='.tmp')
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=2)
    os.replace(name, path)


def locate(option=None):
    """Return the library directory and how it was chosen: option, environment, settings or default."""
    if option:
        return Path(option).expanduser(), 'option'
    if configured := os.environ.get('PAPERASSIST_DATA_DIR'):
        return Path(configured).expanduser(), 'environment'
    if saved := saved_library():
        return saved, 'settings'
    return platform_data_dir(), 'default'
