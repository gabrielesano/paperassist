"""Keep Windows installers from replacing files while a reader is running."""

import sys

INSTALLATION_MUTEX = r"Local\PaperAssist.Native.Reader"
_mutex_handle = None


def hold_installation_mutex():
    global _mutex_handle
    if sys.platform != "win32" or _mutex_handle is not None:
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, INSTALLATION_MUTEX)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    # Let Windows close it at process exit, after DLLs finish unloading.
    _mutex_handle = handle
