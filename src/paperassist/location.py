"""Choosing the library folder: open another library, or move this one to an empty folder.

The old folder is never modified or deleted here; the user removes it once satisfied.
"""

import hashlib
import os
import shutil
from pathlib import Path

# Markers of a running server; the copy is a plain library that no server holds yet.
SKIP = {'.server.lock', '.server.json'}
ROOT = Path('.')
MISMATCH = 'The copy does not match the original, or the library changed while it was copied. Nothing was changed.'
NOT_A_FOLDER = 'Enter the full path of a folder.'


def _answer(action, message, **extra):
    return {'action': action, 'message': message, 'files': 0, 'bytes': 0, **extra}


def _tree(root):
    """Relative folders and {file: size} under root, without the server markers."""
    folders, files = [], {}
    for folder, names, filenames in os.walk(root):
        relative = Path(folder).relative_to(root)
        folders.extend(relative / name for name in names)
        for name in filenames:
            if relative != ROOT or name not in SKIP:
                files[relative / name] = (Path(folder) / name).stat().st_size
    return folders, files


def _size(count):
    for unit in ('bytes', 'KB', 'MB'):
        if count < 1000:
            return f'{count:.0f} {unit}'
        count /= 1000
    return f'{count:.1f} GB'


def classify(current, text):
    """Say what choosing `text` would do: open, move, current or refuse."""
    try:
        if not isinstance(text, str) or not text.strip() or not Path(text.strip()).expanduser().is_absolute():
            return _answer('refuse', NOT_A_FOLDER)
        current = Path(current).resolve()
        target = Path(text.strip()).expanduser().resolve()
        if target == current:
            return _answer('current', 'That is the library you are using now.')
        if current in target.parents:
            return _answer('refuse', 'Choose a folder outside the current library.')
        if target in current.parents:
            return _answer('refuse', 'Choose a folder that does not contain the current library.')
        if target.exists() and not target.is_dir():
            return _answer('refuse', f'{target} is a file, not a folder.')
        if (target / 'library.sqlite3').is_file():
            return _answer('open', f'Open the library in {target}? The current library stays where it is.')
        if target.exists() and any(target.iterdir()):
            return _answer('refuse', 'That folder is not empty and holds no PaperAssist library. Choose an empty or new folder.')
        _, files = _tree(current)
    except OSError as error:
        return _answer('refuse', f'That folder cannot be read: {error.strerror or error}.')
    except RuntimeError:
        return _answer('refuse', NOT_A_FOLDER)
    return _answer('move', f'Move the library to {target}? PaperAssist copies {len(files)} files ({_size(sum(files.values()))}), '
                   'checks the copy and then uses it. The current folder is kept; you can delete it yourself afterwards.',
                   files=len(files), bytes=sum(files.values()))


def _digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').digest()


def _verify(source, copy):
    """Same folders, files and contents; also catches the library changing while it was copied."""
    folders, files = _tree(source)
    copied_folders, copied_files = _tree(copy)
    if set(folders) != set(copied_folders) or files != copied_files:
        raise ValueError(MISMATCH)
    for name in files:
        if _digest(source / name) != _digest(copy / name):
            raise ValueError(MISMATCH)


def move(current, target):
    """Copy the library into an empty or missing folder and check it; undo only what this created."""
    current, target = Path(current), Path(target)
    created = target
    while created.parent != created and not created.parent.exists():
        created = created.parent
    created = None if target.exists() else created
    try:
        folders, files = _tree(current)
        target.mkdir(parents=True, exist_ok=True)
        for folder in folders:
            (target / folder).mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(current / name, target / name)
        _verify(current, target)
    except BaseException as error:
        if created:
            shutil.rmtree(created, ignore_errors=True)
        else:
            for child in target.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        if isinstance(error, OSError):
            raise ValueError(f'Copying failed: {error.strerror or error}. Nothing was changed.') from error
        raise
