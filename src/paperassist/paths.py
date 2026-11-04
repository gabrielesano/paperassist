"""Where the library lives when the user does not say."""

import os
import sys
from pathlib import Path


def default_data_dir():
    """Environment override first, then the platform's per-user data directory."""
    if configured := os.environ.get('PAPERASSIST_DATA_DIR'):
        return Path(configured).expanduser()
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming'
        return Path(base) / 'PaperAssist'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'PaperAssist'
    base = os.environ.get('XDG_DATA_HOME')
    # The XDG spec ignores relative values.
    return (Path(base) if base and os.path.isabs(base) else Path.home() / '.local' / 'share') / 'paperassist'


def resolve_data_dir(option=None):
    return Path(option).expanduser() if option else default_data_dir()
