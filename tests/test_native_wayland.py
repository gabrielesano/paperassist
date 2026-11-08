"""Tests for Qt/Wayland text-input focus handling and the upstream warning filter."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from test_native_support import needs_qt
except ModuleNotFoundError:
    from tests.test_native_support import needs_qt

try:
    from PySide6.QtCore import QMessageLogContext, QtMsgType, qFormatLogMessage, qInstallMessageHandler
    from paperassist.native.wayland import (
        install_wayland_warning_filter,
        is_spurious_wayland_warning,
    )
except ImportError:
    pass


@needs_qt
class WaylandWarningFilterTests(unittest.TestCase):
    def test_spurious_warning_matching(self):
        ctx = QMessageLogContext(
            "qwaylandtextinputv3.cpp",
            214,
            "virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*)",
            "qt.qpa.wayland.textinput",
        )
        spurious_msg_null = (
            "virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) "
            "Trying to disable 0x55d1bcabd790 but 0x0 is focused"
        )
        spurious_msg_other = (
            "virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) "
            "Trying to disable 0x55d1bcabd790 but 0x5622e21867d0 is focused"
        )
        self.assertTrue(is_spurious_wayland_warning(QtMsgType.QtWarningMsg, ctx, spurious_msg_null))
        self.assertTrue(is_spurious_wayland_warning(QtMsgType.QtWarningMsg, ctx, spurious_msg_other))

    def test_other_warnings_in_category_not_filtered(self):
        ctx = QMessageLogContext("qwaylandtextinputv3.cpp", 100, "enableSurface", "qt.qpa.wayland.textinput")
        other_warning = "virtual void QtWaylandClient::QWaylandTextInputv3::enableSurface(wl_surface*) Failed to bind surface"
        self.assertFalse(is_spurious_wayland_warning(QtMsgType.QtWarningMsg, ctx, other_warning))

    def test_critical_and_fatal_errors_not_filtered(self):
        ctx = QMessageLogContext("qwaylandtextinputv3.cpp", 214, "disableSurface", "qt.qpa.wayland.textinput")
        spurious_msg = (
            "virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) "
            "Trying to disable 0x55d1bcabd790 but 0x0 is focused"
        )
        self.assertFalse(is_spurious_wayland_warning(QtMsgType.QtCriticalMsg, ctx, spurious_msg))
        self.assertFalse(is_spurious_wayland_warning(QtMsgType.QtFatalMsg, ctx, spurious_msg))

    def test_other_logging_categories_not_filtered(self):
        ctx = QMessageLogContext("other.cpp", 50, "func", "qt.qpa.wayland")
        spurious_msg = "Trying to disable 0x55d1bcabd790 but 0x0 is focused"
        self.assertFalse(is_spurious_wayland_warning(QtMsgType.QtWarningMsg, ctx, spurious_msg))

    def test_filter_drops_only_matching_warning(self):
        ctx_wayland = QMessageLogContext(
            "qwaylandtextinputv3.cpp",
            214,
            "disableSurface",
            "qt.qpa.wayland.textinput",
        )
        spurious_msg = (
            "virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) "
            "Trying to disable 0x55d1bcabd790 but 0x0 is focused"
        )
        genuine_warning = "qt.qpa.wayland.textinput: Connection lost to text input manager"

        captured = io.StringIO()
        with patch("sys.stderr", captured):
            handler = install_wayland_warning_filter()
            self.assertIsNotNone(handler)
            try:
                # Trigger spurious warning: should be dropped
                handler(QtMsgType.QtWarningMsg, ctx_wayland, spurious_msg)
                self.assertEqual(captured.getvalue(), "")

                # Trigger genuine warning: should be written to stderr
                handler(QtMsgType.QtWarningMsg, ctx_wayland, genuine_warning)
                self.assertIn("Connection lost to text input manager", captured.getvalue())
            finally:
                qInstallMessageHandler(None)

    def test_opt_out_environment_variable(self):
        with patch.dict(os.environ, {"PAPERASSIST_RAW_QT_LOGS": "1"}):
            handler = install_wayland_warning_filter()
            self.assertIsNone(handler)


E2E_RUNNER_SCRIPT = r"""
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from paperassist.native.about import AboutDialog
from paperassist.native.comments import NotesModel
from paperassist.native.library_dialog import LibraryDialog
from paperassist.native.metadata_dialog import MetadataDialog
from paperassist.native.positions import current_page
from paperassist.native.preferences_dialog import PreferencesDialog
from paperassist.native.renderer import RenderClient
from paperassist.native.source import FileSource
from paperassist.native.sources_dialog import SourcesDialog
from paperassist.native.wayland import install_wayland_warning_filter
from paperassist.native.window import MainWindow
from paperassist.workspace import Workspace

def simulate_typing(widget, text):
    for ch in text:
        key = getattr(Qt, f"Key_{ch.upper()}", Qt.Key_A)
        QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier, ch))
        QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.KeyRelease, key, Qt.NoModifier, ch))
    QApplication.processEvents()

def simulate_escape(widget):
    QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.KeyRelease, Qt.Key_Escape, Qt.NoModifier))
    QApplication.processEvents()

def simulate_key(widget, key, text=""):
    QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier, text))
    QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.KeyRelease, key, Qt.NoModifier, text))
    QApplication.processEvents()

def main():
    install_filter = os.environ.get("TEST_INSTALL_FILTER", "1") == "1"
    if install_filter:
        install_wayland_warning_filter()

    app = QApplication(sys.argv)
    app.setApplicationName("PaperAssist")
    app.setDesktopFileName("paperassist")

    pdf_path = Path(sys.argv[1]).resolve()
    tmp_dir = tempfile.TemporaryDirectory()
    workspace = Workspace(tmp_dir.name)
    client = RenderClient()
    try:
        window = MainWindow(client, workspace)
        window.show()
        for _ in range(10):
            QApplication.processEvents()
        window.open_source(FileSource(pdf_path))
        for _ in range(10):
            QApplication.processEvents()

        tab = window.tab()
        view = tab.main.view
        initial_page = current_page(view)

        # 1. FindBar
        pane = window.pane()
        pane.find_bar.open()
        for _ in range(5):
            QApplication.processEvents()
        assert pane.find_bar.field.hasFocus(), f"FindBar field must have focus (focusWidget={QApplication.focusWidget()})"
        simulate_typing(pane.find_bar.field, "jknp")
        assert pane.find_bar.field.text() == "jknp", "Typing must succeed"
        assert current_page(view) == initial_page, "Shortcuts must not fire during typing"
        simulate_escape(pane.find_bar.field)
        for _ in range(5):
            QApplication.processEvents()
        assert pane.find_bar.isHidden(), "FindBar must hide on Esc"
        assert view.hasFocus(), "Focus must return to PdfView on FindBar Esc"

        # 2. PageField
        window.shortcuts.focus_page()
        for _ in range(5):
            QApplication.processEvents()
        page_edit = window.shortcuts.page_field.edit
        assert page_edit.hasFocus(), "PageField edit must have focus"
        simulate_typing(page_edit, "9")
        simulate_escape(page_edit)
        for _ in range(5):
            QApplication.processEvents()
        assert view.hasFocus(), "Focus must return to PdfView on PageField Esc"

        # 3. ReferencesPanel filter
        window.shortcuts.focus_filter()
        for _ in range(5):
            QApplication.processEvents()
        ref_filter = window.details.filter
        assert ref_filter.hasFocus(), "Filter must have focus"
        simulate_typing(ref_filter, "query")
        simulate_escape(ref_filter)  # clears
        assert ref_filter.text() == "", "First Esc must clear filter"
        simulate_escape(ref_filter)  # leaves
        for _ in range(5):
            QApplication.processEvents()
        assert view.hasFocus(), "Focus must return to PdfView on second Esc"

        # 4. Comments composer
        imported = workspace.library.import_paths([pdf_path])
        doc_id = imported.get("documents", imported.get("imported", []))[0]["id"]
        view.notes_model = NotesModel(workspace.library, doc_id, view)
        window.set_columns(details=True)
        window.comments.show_mode("comments")
        window.comments.compose()
        for _ in range(5):
            QApplication.processEvents()
        composer = window.comments.panel.text
        assert composer.hasFocus(), "Composer text must have focus"
        simulate_typing(composer, "Comment j k n p")
        assert "Comment j k n p" in composer.toPlainText(), "Composer must receive text"
        assert current_page(view) == initial_page, "Shortcuts must not fire in composer"
        window.comments.discard_draft(pane)
        for _ in range(5):
            QApplication.processEvents()

        # 5. LibraryDialog
        lib = LibraryDialog(workspace, window)
        lib.show()
        lib.activateWindow()
        QTest.qWait(40)
        lib.search.setFocus()
        QTest.qWait(40)
        simulate_typing(lib.search, "search test")
        QTest.qWait(20)
        lib.close()
        QTest.qWait(30)
        lib.deleteLater()
        for _ in range(5):
            QApplication.processEvents()

        # 6. PreferencesDialog
        pref = PreferencesDialog(window)
        pref.show()
        for _ in range(5):
            QApplication.processEvents()
        pref.close()
        for _ in range(5):
            QApplication.processEvents()
        pref.deleteLater()
        for _ in range(5):
            QApplication.processEvents()

        # 7. SourcesDialog
        src = SourcesDialog(workspace, window)
        src.show()
        for _ in range(5):
            QApplication.processEvents()
        src.close()
        for _ in range(5):
            QApplication.processEvents()
        src.deleteLater()
        for _ in range(5):
            QApplication.processEvents()

        # 8. AboutDialog
        about = AboutDialog(window)
        about.show()
        for _ in range(5):
            QApplication.processEvents()
        about.close()
        for _ in range(5):
            QApplication.processEvents()
        about.deleteLater()
        for _ in range(5):
            QApplication.processEvents()

        # 9. MetadataDialog
        doc = workspace.library.get_document(doc_id)
        meta = MetadataDialog(workspace.library, doc, window)
        meta.show()
        for _ in range(5):
            QApplication.processEvents()
        meta.close()
        for _ in range(5):
            QApplication.processEvents()
        meta.deleteLater()
        for _ in range(5):
            QApplication.processEvents()

        # 10. Document keyboard navigation
        view.setFocus()
        for _ in range(5):
            QApplication.processEvents()
        assert view.hasFocus(), "View must regain focus"
        QTest.keyClick(window, Qt.Key_J)
        for _ in range(5):
            QApplication.processEvents()
        assert current_page(view) == 1, f"Page down via 'j' shortcut (got {current_page(view)})"
        QTest.keyClick(window, Qt.Key_K)
        for _ in range(5):
            QApplication.processEvents()
        assert current_page(view) == 0, f"Page up via 'k' shortcut (got {current_page(view)})"
        QTest.keyClick(view, Qt.Key_PageDown)
        for _ in range(5):
            QApplication.processEvents()
        assert current_page(view) == 1, f"Page down via PageDown (got {current_page(view)})"
        QTest.keyClick(view, Qt.Key_PageUp)
        for _ in range(5):
            QApplication.processEvents()
        assert current_page(view) == 0, f"Page up via PageUp (got {current_page(view)})"

        window.close()
        for _ in range(5):
            QApplication.processEvents()
        tmp_dir.cleanup()
        print("E2E_WAYLAND_PASS", flush=True)
    finally:
        client.shutdown()
    sys.exit(0)

if __name__ == "__main__":
    main()
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "Wayland compositor tests require Linux")
@needs_qt
class WaylandCompositorEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compositor = None
        if shutil.which("mutter") and shutil.which("dbus-launch"):
            cls.compositor = "mutter"
        elif shutil.which("weston"):
            cls.compositor = "weston"

    def setUp(self):
        if not self.compositor:
            self.skipTest("No headless Wayland compositor (mutter/weston) found on system")
        self.tmp_script = Path(tempfile.gettempdir()) / "wayland_e2e_runner.py"
        self.tmp_script.write_text(E2E_RUNNER_SCRIPT, encoding="utf-8")
        self.addCleanup(lambda: self.tmp_script.unlink(missing_ok=True))
        self.fixtures_pdf = Path(__file__).parent / "fixtures" / "pdfjs-annotations.pdf"

    def _run_under_wayland(self, install_filter=True):
        xdg_runtime = tempfile.mkdtemp(prefix="xdg-wayland-test-")
        os.chmod(xdg_runtime, 0o700)
        env = {
            **os.environ,
            "XDG_RUNTIME_DIR": xdg_runtime,
            "XDG_CURRENT_DESKTOP": "",
            "NO_AT_BRIDGE": "1",
            "QT_NO_PORTAL": "1",
            "QT_QPA_PLATFORM": "wayland",
            "TEST_INSTALL_FILTER": "1" if install_filter else "0",
        }
        if not install_filter:
            env["PAPERASSIST_RAW_QT_LOGS"] = "1"

        out_file = tempfile.NamedTemporaryFile(delete=False)
        err_file = tempfile.NamedTemporaryFile(delete=False)
        out_path = Path(out_file.name)
        err_path = Path(err_file.name)
        out_file.close()
        err_file.close()

        try:
            if self.compositor == "mutter":
                cmd = (
                    "eval $(dbus-launch --sh-syntax); "
                    f"mutter --wayland --headless --no-x11 --virtual-monitor 1024x768 >/dev/null 2>&1 & "
                    f"MPID=$!; sleep 1.2; "
                    f"WAYLAND_DISPLAY=wayland-0 {sys.executable} {self.tmp_script} {self.fixtures_pdf} > {out_path} 2> {err_path}; "
                    f"STATUS=$?; kill -9 $MPID 2>/dev/null || true; "
                    f"kill -9 $DBUS_SESSION_BUS_PID 2>/dev/null || true; exit $STATUS"
                )
            else:
                socket_name = "wayland-test-e2e"
                cmd = (
                    f"weston --backend=headless --socket={socket_name} >/dev/null 2>&1 & "
                    f"WPID=$!; sleep 0.8; "
                    f"WAYLAND_DISPLAY={socket_name} {sys.executable} {self.tmp_script} {self.fixtures_pdf} > {out_path} 2> {err_path}; "
                    f"STATUS=$?; kill -9 $WPID 2>/dev/null || true; exit $STATUS"
                )
            proc = subprocess.run(
                ["bash", "-c", cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=20,
            )
            stdout = out_path.read_text(encoding="utf-8", errors="replace")
            stderr = err_path.read_text(encoding="utf-8", errors="replace")
            return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
        finally:
            out_path.unlink(missing_ok=True)
            err_path.unlink(missing_ok=True)
            shutil.rmtree(xdg_runtime, ignore_errors=True)

    def test_e2e_wayland_focus_and_warning_suppression(self):
        proc = self._run_under_wayland(install_filter=True)
        self.assertEqual(
            proc.returncode,
            0,
            f"Wayland E2E test failed with code {proc.returncode}.\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )
        self.assertIn("E2E_WAYLAND_PASS", proc.stdout)
        # Assert zero QWaylandTextInputv3::disableSurface stderr warning spam
        self.assertNotIn("QWaylandTextInputv3::disableSurface", proc.stderr)
        self.assertNotIn("is focused", proc.stderr)

    def test_e2e_wayland_raw_logs_opt_out_reproduces_upstream_warning(self):
        # Under Mutter compositor, deactivating surfaces with zwp_text_input_v3 emits the warning
        if self.compositor != "mutter":
            self.skipTest("Raw warning verification specifically uses mutter with zwp_text_input_v3")
        proc = self._run_under_wayland(install_filter=False)
        self.assertEqual(proc.returncode, 0, f"Raw run failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        self.assertIn("E2E_WAYLAND_PASS", proc.stdout)
        # In raw mode without filter, the upstream Qt warning is present
        self.assertIn("disableSurface", proc.stderr)
        self.assertIn("is focused", proc.stderr)
