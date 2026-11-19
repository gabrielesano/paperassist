"""Platform-specific runtime helpers: heap trimming, Windows installer mutex, Wayland log filter."""
import ctypes
import os
import sys

INSTALLATION_MUTEX = r"Local\PaperAssist.Native.Reader"
_mutex_handle = None


def trim_heap():
    """Hand freed heap pages back to the OS: glibc keeps them after large pixmaps are released."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # not glibc: nothing to do


def hold_installation_mutex():
    """Keep Windows installers from replacing files while a reader is running."""
    global _mutex_handle
    if sys.platform != "win32" or _mutex_handle is not None:
        return
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, INSTALLATION_MUTEX)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    # Let Windows close it at process exit, after DLLs finish unloading.
    _mutex_handle = handle


def is_spurious_wayland_warning(msg_type, context, message):
    """Check if the log message is the known upstream Qt 6 Wayland text-input race warning.

    On Wayland compositors implementing zwp_text_input_v3, closing a dialog or deactivating
    a surface triggers a benign QtWarningMsg from 'qt.qpa.wayland.textinput' containing
    'disableSurface' and 'is focused'.
    """
    from PySide6.QtCore import QtMsgType

    if msg_type != QtMsgType.QtWarningMsg:
        return False
    category = getattr(context, "category", "") or ""
    if category != "qt.qpa.wayland.textinput":
        return False
    return "disableSurface" in message and "is focused" in message


def install_wayland_warning_filter():
    """Install a surgical Qt message filter for the upstream Wayland text-input warning.

    Set PAPERASSIST_RAW_QT_LOGS=1 to disable this filter and retain raw logs.
    """
    if os.environ.get("PAPERASSIST_RAW_QT_LOGS") == "1":
        return None
    from PySide6.QtCore import qFormatLogMessage, qInstallMessageHandler

    previous = qInstallMessageHandler(None)

    def filter_handler(msg_type, context, message):
        if is_spurious_wayland_warning(msg_type, context, message):
            return
        if previous is not None:
            previous(msg_type, context, message)
        elif sys.stderr is not None:
            formatted = qFormatLogMessage(msg_type, context, message)
            sys.stderr.write(f"{formatted}\n")
            sys.stderr.flush()

    qInstallMessageHandler(filter_handler)
    return filter_handler
