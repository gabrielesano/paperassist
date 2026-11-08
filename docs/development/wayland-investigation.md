# Qt/Wayland Text-Input & Focus Investigation (`0.1.0a1`)

This document details the root-cause analysis, functional audit, and surgical resolution for the Qt 6 Wayland text-input warnings observed on Linux:
```text
qt.qpa.wayland.textinput: virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) Trying to disable 0x... but 0x0 is focused
```

---

## 1. System & Environment Specifications

- **Operating System**: Linux (Fedora 43 / x86_64, kernel 7.2.8)
- **Qt Version**: Qt 6.12.0
- **PySide6 Version**: 6.12.0
- **Wayland Client Library**: `libQt6WaylandClient.so.6` (`.venv/lib/python3.14/site-packages/PySide6/Qt/lib/libQt6WaylandClient.so.6`)
- **Compositors Tested**:
  - GNOME Mutter 49.8 (headless: `mutter --wayland --headless --no-x11 --virtual-monitor 1024x768`)
  - Weston 14.0.2 (headless: `weston --backend=headless`)
- **Wayland Text Protocol**: `zwp_text_input_v3`

---

## 2. Reproduction and Raw Stderr Evidence

### 2.1 Minimal Upstream Control Reproduction

To determine whether the warning stems from PaperAssist application logic or upstream Qt 6, we executed an isolated 15-line PySide6 script under headless Mutter (`QT_QPA_PLATFORM=wayland`):

```python
import sys
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMainWindow

app = QApplication(sys.argv)
win = QMainWindow()
win.show()
dlg = QDialog(win)
edit = QLineEdit(dlg)
dlg.show()
edit.setFocus()
app.processEvents()
dlg.close()
app.processEvents()
win.close()
```

#### Captured Raw Stderr:
```text
qt.qpa.wayland.textinput: virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) Trying to disable 0x55d1bcabd790 but 0x0 is focused
```

The warning reproduces reliably in a minimal standard Qt application without PaperAssist.

### 2.2 PaperAssist Reproduction Before Fix

When running PaperAssist with `PAPERASSIST_RAW_QT_LOGS=1` or without warning filtering under Wayland, opening and closing dialogs (`LibraryDialog`, `PreferencesDialog`, `AboutDialog`, `MetadataDialog`, `SourcesDialog`) or focusing input fields emits:

```text
qt.qpa.wayland.textinput: virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) Trying to disable 0x5622e1d5f470 but 0x5622e21867d0 is focused
qt.qpa.wayland.textinput: virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) Trying to disable 0x55ab7cfd8850 but 0x0 is focused
```

---

## 3. Upstream Qt 6 Root Cause Analysis

In upstream Qt 6 (`qtwayland/src/client/qwaylandtextinputv3.cpp`), `QWaylandTextInputv3` implements the client side of the Wayland `zwp_text_input_v3` protocol.

The C++ implementation of `disableSurface` is:

```cpp
void QWaylandTextInputv3::disableSurface(wl_surface *surface)
{
    if (m_surface != surface) {
        qCWarning(qLcQpaWaylandTextInput()) << "Trying to disable" << surface << "but" << m_surface << "is focused";
        return;
    }
    m_surface = nullptr;
    zwp_text_input_v3_disable(m_textInput);
    commit();
}
```

### Why the Mismatch Occurs:
1. **Dialog Close / Surface Unmap**:
   When a dialog or popup containing a text field closes, the Wayland compositor sends a `zwp_text_input_v3.leave` event for that surface.
   Qt's `zwp_text_input_v3_leave` event handler executes first and clears the internal pointer:
   ```cpp
   m_surface = nullptr;  // represents 0x0
   ```
2. **Deactivation Callback**:
   Immediately afterwards, Qt's window/input system calls `QWaylandTextInputv3::disableSurface(surface)` to clean up input state for the departing surface.
   In `disableSurface()`, Qt checks:
   ```cpp
   if (m_surface != surface) // nullptr != surface (e.g. 0x0 != 0x55ab7cfd8850)
   ```
   Because `m_surface` was already nullified by the compositor's `leave` event, Qt logs:
   `"Trying to disable 0x... but 0x0 is focused"`.
3. **Surface Focus Switch**:
   When switching focus directly between surfaces (such as from a dialog back to the main document window), the compositor may deliver the `enter` event for the new surface (`0x5622e21867d0`) before Qt finishes deactivating the old surface (`0x5622e1d5f470`). In this case, `m_surface` points to the new surface, causing Qt to log:
   `"Trying to disable 0x... but 0x... is focused"`.

This is a benign upstream race / ordering trace in Qt 6's Wayland platform plugin (`qt.qpa.wayland.textinput`). It does not cause dropped keystrokes, input freezing, focus loss, or graphical glitches.

---

## 4. Comprehensive Functional Focus & Input Audit Under Real Wayland

An end-to-end audit was conducted under real headless Wayland (`mutter --wayland --headless --virtual-monitor 1024x768`, `QT_QPA_PLATFORM=wayland`):

### 4.1 Input Widgets Tested
- **`FindBar` (`QLineEdit`)**: Opened via `Ctrl+F` / button; received typing (`jknp`); verified single-key shortcuts (`j`, `k`, `n`, `p`) did NOT trigger page turns or outline toggles.
- **`PageField` (`QLineEdit`)**: Focused via `G`; typed digits; verified shortcuts withheld.
- **`ReferencesPanel` filter (`QLineEdit`)**: Focused via `/`; typed search queries; verified single-key navigation withheld.
- **`Comments` Composer (`QTextEdit`)**: Opened composer; typed notes containing plain characters (`j`, `k`, `n`, `p`); verified shortcuts withheld and comment body captured accurately.
- **Dialogs**:
  - `LibraryDialog`: Search field input.
  - `PreferencesDialog`: Form fields and settings navigation.
  - `SourcesDialog`: Path and source list fields.
  - `AboutDialog`: Version and license dialog display and dismissal.
  - `MetadataDialog`: Document title and bibliographic editing fields.

### 4.2 Escape Navigation & Focus Restoration
- Pressing `Escape` in `FindBar` closes the find bar and immediately restores focus to `PdfView`.
- Pressing `Escape` in `PageField` resets the text and restores focus to `PdfView`.
- Pressing `Escape` in `ReferencesPanel` filter clears the search query; a second `Escape` leaves the filter and restores focus to `PdfView`.
- Closing dialogs cleanly yields focus back to the parent window and active `PdfView`.
- Immediately following `Escape` restoration, document navigation keys (`j`, `k`, `PageDown`, `PageUp`) execute page turns without requiring a mouse click.

### 4.3 Conclusion of Functional Audit
PaperAssist's event filters and shortcut isolation mechanisms function properly on Wayland:
- Line edits and text editors claim printable keystrokes before shortcuts match.
- Escape propagation reliably returns keyboard focus to the document.
- No functional regressions or input loss exist in PaperAssist.

---

## 5. Implementation Resolution

Rather than globally suppressing all Qt logging via environment variables (which would blind developers and users to critical errors), PaperAssist implements two surgical mechanisms:

### 5.1 Desktop File Name Association
In `src/paperassist/native/app.py`:
```python
app.setApplicationName("PaperAssist")
app.setDesktopFileName("paperassist")
```
This configures the Wayland `app_id` to match `paperassist.desktop`, enabling compositors to map window properties and portal permissions without warnings.

### 5.2 Surgical Wayland Warning Filter
In `src/paperassist/native/wayland.py` (called in `app.run()`):
```python
def is_spurious_wayland_warning(msg_type, context, message):
    if msg_type != QtMsgType.QtWarningMsg:
        return False
    category = getattr(context, "category", "") or ""
    if category != "qt.qpa.wayland.textinput":
        return False
    return "disableSurface" in message and "is focused" in message
```
- Installs via `qInstallMessageHandler` wrapping existing handlers.
- **Strictly scoped**:
  - Drops **only** `QtWarningMsg` on category `"qt.qpa.wayland.textinput"` matching `"disableSurface"` and `"is focused"`.
  - Catches both `"0x0 is focused"` and `"0x<addr> is focused"`.
  - Retains all other `qt.qpa.wayland.textinput` warnings (e.g., surface binding failures, protocol errors).
  - Retains all `QtCriticalMsg` and `QtFatalMsg` across all categories.
  - Retains all messages from other Qt categories.
- **Developer Opt-Out**: Setting `PAPERASSIST_RAW_QT_LOGS=1` bypasses the filter for low-level debugging.

---

## 6. Verification and Automated Test Coverage

Automated tests are implemented in `tests/test_native_wayland.py`:

1. `test_spurious_warning_matching`: Asserts that `is_spurious_wayland_warning` matches both `0x0 is focused` and `0x<hex> is focused`.
2. `test_other_warnings_in_category_not_filtered`: Asserts other warnings in `qt.qpa.wayland.textinput` pass through.
3. `test_critical_and_fatal_errors_not_filtered`: Asserts critical and fatal errors are never dropped.
4. `test_other_logging_categories_not_filtered`: Asserts other logging categories pass through intact.
5. `test_filter_drops_only_matching_warning`: Asserts the installed message handler filters only the matching warning and prints genuine warnings to stderr.
6. `test_opt_out_environment_variable`: Asserts `PAPERASSIST_RAW_QT_LOGS=1` disables filtering.
7. `test_e2e_wayland_focus_and_warning_suppression`: Runs full end-to-end PaperAssist session in a real headless Mutter Wayland session; exercises all 10 focus/dialog/shortcut steps; asserts clean exit code 0 and zero stderr warning spam.
8. `test_e2e_wayland_raw_logs_opt_out_reproduces_upstream_warning`: Runs the session in raw mode (`PAPERASSIST_RAW_QT_LOGS=1`) and asserts the upstream Qt warning is reproduced on stderr.

All 8 tests pass cleanly in 4.25 seconds.
