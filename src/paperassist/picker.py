"""Open the operating system's folder chooser on the machine running the server.

A web page cannot learn the absolute path of a folder, so the server asks the desktop.
"""
import shutil
import subprocess
import sys
from pathlib import Path

# Long enough never to cut off a dialog someone is still using.
TIMEOUT = 15 * 60

# Tk must own its main thread and the server is threaded, so it runs in its own process.
# Some Tk versions return an empty tuple instead of '' on cancel, hence `or ''`.
TK_SCRIPT = """
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes('-topmost', True)
print(filedialog.askdirectory(parent=root, initialdir=sys.argv[1], title=sys.argv[2], mustexist=True) or '')
"""


class PickerUnavailable(RuntimeError):
    """No folder chooser could be opened, or it failed."""


def choose_folder(start=None, title='Folder for manual downloads'):
    """Return the chosen absolute path, or None if the user cancelled."""
    folder = Path(start).expanduser() if start else None
    start = str(folder if folder and folder.is_dir() else Path.home())
    if sys.platform == 'darwin':
        return _macos(start, title)
    if sys.platform != 'win32':
        if shutil.which('zenity'):
            return _run(['zenity', '--file-selection', '--directory', f'--title={title}', f'--filename={start.rstrip("/")}/'], cancel_exit=True)
        if shutil.which('kdialog'):
            return _run(['kdialog', '--getexistingdirectory', start, '--title', title], cancel_exit=True)
    return _run([sys.executable, '-c', TK_SCRIPT, start, title])


def _macos(start, title):
    def quote(text):  # AppleScript string literal
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    script = f'POSIX path of (choose folder with prompt {quote(title)} default location POSIX file {quote(start)})'
    result = _call(['osascript', '-e', script])
    if result.returncode == 1 and '-128' in result.stderr:
        return None
    path = _path(result)
    return path.rstrip('/') or path if path else None


def _run(command, cancel_exit=False):
    """zenity and kdialog exit 1 when cancelled; Tk exits 0 and prints nothing."""
    result = _call(command)
    return None if cancel_exit and result.returncode == 1 else _path(result)


def _call(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PickerUnavailable(f'The folder window could not be opened: {error}') from error


def _path(result):
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        raise PickerUnavailable('The folder window failed' + (f': {detail[-1][:200]}' if detail else '.'))
    return result.stdout.strip() or None
