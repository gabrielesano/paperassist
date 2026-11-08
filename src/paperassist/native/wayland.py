"""Wayland platform integration and upstream Qt 6 text-input warning filter."""
import os
import sys

from PySide6.QtCore import QtMsgType, qFormatLogMessage, qInstallMessageHandler


def is_spurious_wayland_warning(msg_type, context, message):
    """Check if the log message is the known upstream Qt 6 Wayland text-input race warning.

    In Qt 6 (e.g. Qt 6.12 on Linux Wayland compositors implementing zwp_text_input_v3
    such as Mutter or Weston), closing a modal/modeless dialog or deactivating a surface
    with text input results in the compositor sending a zwp_text_input_v3.leave event.
    Qt's zwp_text_input_v3_leave handler clears m_surface to nullptr (0x0) or switches
    to the newly focused surface. Immediately afterwards, Qt's input context deactivates
    the departing surface and calls QWaylandTextInputv3::disableSurface(surface).
    disableSurface checks if m_surface == surface; because m_surface is now 0x0 or the
    new surface, Qt logs a spurious QtWarningMsg:
    'virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) Trying to disable ... but ... is focused'
    This warning is benign upstream noise and does not affect text input or focus.
    """
    if msg_type != QtMsgType.QtWarningMsg:
        return False
    category = getattr(context, "category", "") or ""
    if category != "qt.qpa.wayland.textinput":
        return False
    return "disableSurface" in message and "is focused" in message


def install_wayland_warning_filter():
    """Install a surgical Qt message filter for the upstream Wayland text-input warning.

    Suppresses only QtWarningMsg from category 'qt.qpa.wayland.textinput' matching
    'disableSurface' and '0x0 is focused'. All other warnings, critical errors,
    fatal messages, and warnings from any other category pass through untouched.

    Set PAPERASSIST_RAW_QT_LOGS=1 to disable this filter and retain raw logs.
    """
    if os.environ.get("PAPERASSIST_RAW_QT_LOGS") == "1":
        return None

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
