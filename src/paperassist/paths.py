"""Where the library lives: the user's explicit choice, then the saved one, then the platform default."""

import ctypes
from ctypes import wintypes
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


def format_size(count):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if count < 1024 or unit == 'GB':
            return f'{int(count)} bytes' if unit == 'bytes' else f'{count:.1f} {unit}'
        count /= 1024


def _is_windows() -> bool:
    return os.name == 'nt'


def _win32_libs():
    return ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('advapi32', use_last_error=True)


def _restrict_windows_acl(path: Path) -> None:
    """Apply an owner-only protected DACL on a file using numeric SIDs (locale-independent)."""
    kernel32, advapi32 = _win32_libs()
    TOKEN_QUERY = 0x0008
    TokenUser = 1
    SDDL_REVISION_1 = 1
    DACL_SECURITY_INFORMATION = 0x00000004
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', wintypes.DWORD)]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [('User', _SID_AND_ATTRIBUTES)]

    def _fail():
        err = ctypes.get_last_error()
        raise (ctypes.WinError(err) if err and hasattr(ctypes, 'WinError') else OSError('Windows ACL update failed'))

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        _fail()
    sid_str = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    try:
        needed = wintypes.DWORD(0)
        if advapi32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(needed)) or not needed.value:
            _fail()
        buf = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(token, TokenUser, buf, needed.value, ctypes.byref(needed)):
            _fail()
        user_sid = ctypes.cast(buf, ctypes.POINTER(_TOKEN_USER)).contents.User.Sid
        if not advapi32.ConvertSidToStringSidW(user_sid, ctypes.byref(sid_str)):
            _fail()
        sddl = f'D:P(A;;FA;;;{ctypes.wstring_at(sid_str.value)})'
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, SDDL_REVISION_1, ctypes.byref(sd), None
        ):
            _fail()
        if not advapi32.SetFileSecurityW(
            str(path), DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION, sd
        ):
            _fail()
    finally:
        if sd.value:
            kernel32.LocalFree(sd.value)
        if sid_str.value:
            kernel32.LocalFree(sid_str.value)
        if token.value:
            kernel32.CloseHandle(token)


def write_json_atomic(path, data, mode=None, **dump_kwargs):
    """Write JSON through a sibling temporary file so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_kwargs.setdefault('indent', 2)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(data, file, **dump_kwargs)
        if mode is not None:
            if not _is_windows():
                os.chmod(temp, mode)
            elif mode == 0o600:
                _restrict_windows_acl(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def saved_library():
    library = load_config().get('library')
    return Path(library) if isinstance(library, str) and os.path.isabs(library) else None


def save_library(directory):
    update_config(lambda data: {**data, 'library': str(directory)})


def update_config(change):
    with _CONFIG_WRITE:
        data = change(load_config())
        write_json_atomic(config_file(), data)
        return data


def locate(option=None):
    """Return the library directory and how it was chosen: option, environment, settings or default."""
    if option:
        return Path(option).expanduser(), 'option'
    if configured := os.environ.get('PAPERASSIST_DATA_DIR'):
        return Path(configured).expanduser(), 'environment'
    if saved := saved_library():
        return saved, 'settings'
    return platform_data_dir(), 'default'


def locate_existing(option=None):
    """locate(), but a library chosen in the app must still exist.

    A chosen library always has its catalog; without it the folder is gone (say, an
    unplugged drive), and silently starting an empty library there would hide that.
    """
    directory, source = locate(option)
    if source == 'settings' and not (directory / 'library.sqlite3').is_file():
        raise FileNotFoundError(f'The library folder chosen in the app is missing: {directory}\n'
                                'Reconnect its drive, or start with --data-dir to use another folder.')
    return directory, source
