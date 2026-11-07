"""Tests for the native brand menu, interface size preferences, and About dialog."""
from collections import Counter
from importlib import metadata
import io
import tempfile
import time
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

from test_native_support import make_pdf, needs_qt, pdf_available

if pdf_available:
    import paperassist
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QAction, QFont, QShortcut
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QApplication, QDialog, QFileDialog, QMessageBox,
        QPushButton, QTextBrowser, QToolBar, QToolButton,
    )

    from paperassist import paths, references, settings
    from paperassist.native import app as native_app, app_menu, preferences_dialog, shortcuts
    from paperassist.native.engine import Rect
    from paperassist.native.layout import MARGIN
    from paperassist.native.renderer import RenderClient
    from paperassist.native.source import FileSource
    from paperassist.native.sources_dialog import SourcesDialog
    from paperassist.native.window import MainWindow
    from paperassist.workspace import LibraryInUse, Workspace, library_lock


def _installed_version_and_license(distributions):
    for name in distributions:
        try:
            meta = metadata.metadata(name)
            break
        except metadata.PackageNotFoundError:
            continue
    else:
        raise AssertionError(f"Expected one of {distributions} to be installed")
    lic = (meta.get("License-Expression") or meta.get("License") or "").strip().splitlines()[0].strip()
    return meta["Version"], lic


@needs_qt
class NativePreferencesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name).resolve()
        config = patch("paperassist.paths.config_file", return_value=self.dir / "config.json")
        config.start()
        self.addCleanup(config.stop)

    def assert_buttons_fit(self, root, context):
        checked = 0
        for cls in (QPushButton, QToolButton):
            for btn in root.findChildren(cls):
                if not btn.isVisible() or not btn.text():
                    continue
                self.assertLessEqual(
                    btn.sizeHint().width(), btn.width(),
                    f"{context}: button {btn.text()!r} ({btn.objectName()}) sizeHint {btn.sizeHint().width()} > width {btn.width()}",
                )
                checked += 1
        return checked

    def make_window(self, workspace=None, install_menu=True, size=(1200, 800)):
        window = MainWindow(self.client, workspace)
        if install_menu:
            app_menu.install(window)
        window.resize(*size)
        window.show()
        window.activateWindow()
        self.addCleanup(self.close_window, window)
        self.pump()
        return window

    def close_window(self, window):
        if getattr(window, "app_menu", None):
            window.app_menu.close()
        for dialog in window.findChildren(QDialog):
            dialog.reject()
        for pane in window.all_panes():
            pane.release()
        window.hide()
        window.deleteLater()
        self.app.processEvents()

    def pump(self, until=None, seconds=0.15):
        end = time.perf_counter() + (10 if until else seconds)
        while time.perf_counter() < end and not (until and until()):
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(until() if until else True, "timed out")

    def open_pdf(self, window, name="paper", **kwargs):
        path = make_pdf(self.dir / f"{self.id()}-{name}.pdf", **kwargs)
        tab = window.open_source(FileSource(path))
        self.pump(lambda: tab.main.view.has_page(0))
        return tab

    def main_toolbar(self, window):
        return next(bar for bar in window.findChildren(QToolBar) if bar.windowTitle() == "Main")

    # ---- brand menu, entries and shortcuts ------------------------------------------------
    def test_single_install_wires_web_menu_entries_and_has_no_shortcut_conflicts(self):
        workspace = Workspace(self.dir / "menu-library")
        self.addCleanup(workspace.close)
        window = self.make_window(workspace=workspace, install_menu=False)
        shortcuts.install(window)
        app_menu.install(window)
        self.pump()

        bar = self.main_toolbar(window)
        first_widget = bar.widgetForAction(bar.actions()[0])
        self.assertIs(first_widget, window.app_menu_button)
        self.assertEqual(first_widget.text(), "PaperAssist")

        raw_labels = ["---" if a.isSeparator() else a.text() for a in window.app_menu.actions()]
        self.assertEqual(
            raw_labels,
            ["Preferences…", "Sources…", "Keyboard shortcuts", "---", "About PaperAssist"],
        )
        self.assertEqual(window.preferences_action.shortcut().toString(), "Ctrl+,")

        sequences = [seq.toString() for action in window.findChildren(QAction) for seq in action.shortcuts()]
        sequences += [sc.key().toString() for sc in window.findChildren(QShortcut)]
        self.assertEqual([seq for seq, count in Counter(sequences).items() if count > 1], [])

        # "Sources…" opens SourcesDialog for a library window, and an info box for loose files.
        window.sources_action.trigger()
        self.pump()
        sources_dlg = window.findChild(SourcesDialog)
        self.assertIsNotNone(sources_dlg)
        self.assertTrue(sources_dlg.isVisible())
        sources_dlg.reject()
        self.pump()

        # "Keyboard shortcuts" opens the help dialog and lists Preferences… under Toolbar and menu.
        window.shortcuts_action.trigger()
        self.pump()
        help_dlg = window.findChild(QDialog, "shortcuts-help")
        self.assertIsNotNone(help_dlg)
        self.assertTrue(help_dlg.isVisible())
        self.assertIn("Preferences…", help_dlg.findChild(QTextBrowser).toPlainText())
        help_dlg.reject()
        self.pump()

        loose = self.make_window(workspace=None)
        loose.sources_action.trigger()
        self.pump()
        box = loose.findChild(QMessageBox)
        self.assertIsNotNone(box)
        self.assertTrue(box.isVisible())
        box.reject()

    def test_menu_keyboard_navigation_and_custom_key_bindings(self):
        window = self.make_window()
        view = self.open_pdf(window).main.view
        view.setFocus()
        self.pump()
        self.assertIs(self.app.focusWidget(), view)

        # Mouse click opens menu without stealing focus from view; QMenu Up/Down + AppMenu Home/End + Escape.
        QTest.mouseClick(window.app_menu_button, Qt.LeftButton)
        self.pump()
        menu = window.app_menu
        self.assertTrue(menu.isVisible())
        self.assertEqual(menu.activeAction().text(), "Preferences…")

        QTest.keyClick(menu, Qt.Key_Down)
        self.assertEqual(menu.activeAction().text(), "Sources…")
        QTest.keyClick(menu, Qt.Key_Up)
        self.assertEqual(menu.activeAction().text(), "Preferences…")
        QTest.keyClick(menu, Qt.Key_End)
        self.assertEqual(menu.activeAction().text(), "About PaperAssist")
        QTest.keyClick(menu, Qt.Key_Home)
        self.assertEqual(menu.activeAction().text(), "Preferences…")

        QTest.keyClick(menu, Qt.Key_Escape)
        self.pump()
        self.assertFalse(menu.isVisible())
        self.assertIs(self.app.focusWidget(), view)

        # BrandButton opens on Down, Return, and native Space; Tab and Backtab dismiss and move focus.
        window.app_menu_button.setFocus()
        self.pump()
        QTest.keyClick(window.app_menu_button, Qt.Key_Space)
        self.pump()
        self.assertTrue(menu.isVisible())
        QTest.keyClick(menu, Qt.Key_Tab)
        self.pump()
        self.assertFalse(menu.isVisible())
        self.assertIs(self.app.focusWidget(), view)

        window.app_menu_button.setFocus()
        self.pump()
        QTest.keyClick(window.app_menu_button, Qt.Key_Return)
        self.pump()
        self.assertTrue(menu.isVisible())
        QTest.keyClick(menu, Qt.Key_Backtab)
        self.pump()
        self.assertFalse(menu.isVisible())
        self.assertIsNot(self.app.focusWidget(), window.app_menu_button)

        # Open with Down from BrandButton, jump to About with End, activate with Return, Escape restores focus.
        window.app_menu_button.setFocus()
        self.pump()
        QTest.keyClick(window.app_menu_button, Qt.Key_Down)
        self.pump()
        self.assertTrue(menu.isVisible())
        QTest.keyClick(menu, Qt.Key_End)
        QTest.keyClick(menu, Qt.Key_Return)
        self.pump()
        self.assertFalse(menu.isVisible())
        self.assertTrue(window.about_dialog.isVisible())

        QTest.keyClick(window.about_dialog, Qt.Key_Escape)
        self.pump()
        self.assertFalse(window.about_dialog.isVisible())
        self.assertIs(self.app.focusWidget(), window.app_menu_button)

    def test_ctrl_comma_opens_preferences_from_document_and_escape_returns_focus(self):
        window = self.make_window()
        view = self.open_pdf(window).main.view
        view.setFocus()
        self.pump()

        QTest.keyClick(view, Qt.Key_Comma, Qt.ControlModifier)
        self.pump()
        dialog = window.preferences_dialog
        self.assertTrue(dialog.isVisible())
        self.assertIs(self.app.focusWidget(), dialog.size_combo)
        labels = [dialog.size_combo.itemText(i) for i in range(dialog.size_combo.count())]
        self.assertEqual(labels, ["80%", "90%", "100%", "110%", "125%", "150%"])

        QTest.keyClick(dialog, Qt.Key_Escape)
        self.pump()
        self.assertFalse(dialog.isVisible())
        self.assertIs(self.app.focusWidget(), view)

    # ---- interface size persistence, stylesheet preservation, and round trip --------------
    def test_interface_size_applies_immediately_persists_ui_zoom_and_applies_at_startup(self):
        window = self.make_window()
        bar = self.main_toolbar(window)
        initial_height = bar.height()
        initial_font = window.app_menu_button.font().pointSizeF()

        window.show_preferences()
        self.pump()
        window.preferences_dialog.size_combo.setCurrentIndex(4)  # 125%
        self.pump()

        self.assertEqual(settings.preferences(), {"ui_zoom": 1.25})
        self.assertGreater(bar.height(), initial_height)
        self.assertAlmostEqual(window.app_menu_button.font().pointSizeF(), initial_font * 1.25, places=3)

        restarted = self.make_window()
        self.assertEqual(restarted.preferences_dialog.zoom_value, 1.25)
        self.assertEqual(restarted.preferences_dialog.size_combo.currentData(), 1.25)
        self.assertAlmostEqual(restarted.app_menu_button.font().pointSizeF(), initial_font * 1.25, places=3)

    def test_parse_zoom_rejects_unlisted_and_boolean_values(self):
        for valid in (0.8, 0.9, 1, 1.0, 1.1, 1.25, 1.5):
            self.assertEqual(preferences_dialog.parse_zoom(valid), valid)
        for invalid in (None, "", "1.25", True, False, 0, -1, 1.3, 2.0):
            self.assertEqual(preferences_dialog.parse_zoom(invalid), 1)

    def test_stylesheet_changes_made_after_install_survive_size_change_and_return_to_100(self):
        window = self.make_window()
        base = window.styleSheet()
        after_install = "\n#added-after-install { border-bottom: 1px solid #123456; }"
        window.setStyleSheet(base + after_install)

        window.show_preferences()
        self.pump()
        dialog = window.preferences_dialog
        dialog.size_combo.setCurrentIndex(5)  # 150%
        self.pump()
        self.assertIn(after_install, window.styleSheet())

        while_scaled = "\n#added-while-scaled { color: #654321; }"
        window.setStyleSheet(window.styleSheet() + while_scaled)

        dialog.size_combo.setCurrentIndex(2)  # 100%
        self.pump()
        self.assertEqual(window.styleSheet(), base + after_install + while_scaled)

    def test_round_trip_150_to_100_restores_exact_original_fonts_and_sizes(self):
        window = self.make_window()
        self.open_pdf(window)
        window.show_preferences()
        self.pump()

        bar = self.main_toolbar(window)
        dialog = window.preferences_dialog

        def snapshot():
            return {
                "bar_height": bar.height(),
                "brand_font": (window.app_menu_button.font().pointSizeF(), window.app_menu_button.font().pixelSize()),
                "brand_size": window.app_menu_button.size(),
                "tab_font": (window.tabs.tabBar().font().pointSizeF(), window.tabs.tabBar().font().pixelSize()),
                "tab_rect": window.tabs.tabBar().tabRect(0).size(),
                "tab_height": window.tabs.tabBar().height(),
                "outline_font": (window.outline.font().pointSizeF(), window.outline.font().pixelSize()),
                # The column's own hint is floored at 160 px, which would hide how its content scales.
                "outline_hint": window.outline_column.layout().minimumSize(),
                "details_hint": window.details_column.layout().minimumSize(),
                "dialog_font": (dialog.size_combo.font().pointSizeF(), dialog.size_combo.font().pixelSize()),
                "dialog_size": dialog.size(),
            }

        original = snapshot()

        dialog.size_combo.setCurrentIndex(5)  # 150%
        self.pump()
        at_150 = snapshot()
        self.assertGreater(at_150["bar_height"], original["bar_height"])
        self.assertGreater(at_150["brand_size"].width(), original["brand_size"].width())
        self.assertGreater(at_150["tab_rect"].width(), original["tab_rect"].width())
        self.assertGreater(at_150["outline_hint"].width(), original["outline_hint"].width())
        self.assertGreater(at_150["details_hint"].width(), original["details_hint"].width())
        self.assertGreater(at_150["dialog_size"].width(), original["dialog_size"].width())

        # Visit another size and return to 150% to verify sizes never compound.
        dialog.size_combo.setCurrentIndex(0)  # 80%
        self.pump()
        dialog.size_combo.setCurrentIndex(5)  # 150%
        self.pump()
        self.assertEqual(snapshot(), at_150)

        # Returning to 100% restores the exact original fonts and widget sizes.
        dialog.size_combo.setCurrentIndex(2)  # 100%
        self.pump()
        self.assertEqual(snapshot(), original)
        self.assertEqual(settings.preferences()["ui_zoom"], 1)

    # ---- PDF fit-width vs explicit zoom ---------------------------------------------------
    def test_fit_width_view_refits_to_viewport_while_explicit_zoom_stays_unchanged(self):
        # Size a 1-page view so 150% chrome growth triggers its vertical scrollbar and narrows viewport width.
        window = self.make_window(size=(980, 620))
        tab = self.open_pdf(window, "main", pages=1)
        window.side_by_side = True
        quick = window.open_quick(FileSource(make_pdf(self.dir / "quick.pdf", pages=1)))
        self.pump(lambda: tab.main.view.has_page(0) and quick.view.has_page(0))

        main_view = tab.main.view
        quick_view = quick.view
        self.assertTrue(main_view.fit)
        quick_view.zoom_by(1.25)
        self.pump(lambda: quick_view.has_page(0))
        self.assertFalse(quick_view.fit)

        widest = max(w for w, _ in main_view.doc.sizes)
        explicit_before = (
            quick_view.zoom,
            quick_view.fit,
            quick_view.pages.rect(0)[2:],
            quick_view.cache.get(0)[2].size(),
            quick_view.font().pointSizeF(),
            quick_view.font().pixelSize(),
        )

        window.show_preferences()
        self.pump()
        for index in (5, 0, 4, 2, 5):  # 150%, 80%, 125%, 100%, 150%
            window.preferences_dialog.size_combo.setCurrentIndex(index)
            self.pump(lambda: main_view.has_page(0) and quick_view.has_page(0), seconds=0.2)
            self.assertTrue(main_view.fit)
            self.assertAlmostEqual(
                main_view.zoom, (main_view.viewport().width() - 2 * MARGIN) / widest, places=5
            )
            self.assertEqual(main_view.horizontalScrollBar().maximum(), 0)
            self.assertEqual(
                (
                    quick_view.zoom,
                    quick_view.fit,
                    quick_view.pages.rect(0)[2:],
                    quick_view.cache.get(0)[2].size(),
                    quick_view.font().pointSizeF(),
                    quick_view.font().pixelSize(),
                ),
                explicit_before,
            )

        # Resizing at 150% refits the fit-width view to its new width while keeping explicit zoom unchanged.
        before_resize_width = main_view.viewport().width()
        window.resize(1240, 760)
        self.pump(lambda: main_view.has_page(0) and quick_view.has_page(0), seconds=0.2)
        self.assertGreater(main_view.viewport().width(), before_resize_width)
        self.assertTrue(main_view.fit)
        self.assertAlmostEqual(
            main_view.zoom, (main_view.viewport().width() - 2 * MARGIN) / widest, places=5
        )
        self.assertEqual(main_view.horizontalScrollBar().maximum(), 0)
        self.assertEqual((quick_view.zoom, quick_view.fit), explicit_before[:2])

    # ---- About dialog ---------------------------------------------------------------------
    def test_about_dialog_with_loose_files_and_with_open_library(self):
        pyside_ver, pyside_lic = _installed_version_and_license(("PySide6", "PySide6-Essentials"))
        pdfium_ver, pdfium_lic = _installed_version_and_license(("pypdfium2",))
        pypdf_ver, pypdf_lic = _installed_version_and_license(("pypdf",))

        loose_window = self.make_window(workspace=None)
        loose_window.about_action.trigger()
        self.pump()
        self.assertTrue(loose_window.about_dialog.isVisible())
        loose_text = loose_window.about_dialog.text_content()
        self.assertIn(paperassist.__version__, loose_text)
        self.assertIn("lightweight local reader and shared library", loose_text)
        self.assertIn("GPL-3.0-or-later", loose_text)
        self.assertIn("Library: No library open", loose_text)
        for expected in (pyside_ver, pyside_lic, pdfium_ver, pdfium_lic, pypdf_ver, pypdf_lic):
            self.assertIn(expected, loose_text)

        workspace = Workspace(self.dir / "test-library")
        self.addCleanup(workspace.close)
        lib_window = self.make_window(workspace=workspace)
        lib_window.about_action.trigger()
        self.pump()
        self.assertTrue(lib_window.about_dialog.isVisible())
        lib_text = lib_window.about_dialog.text_content()
        self.assertIn(f"Library: {workspace.data_dir}", lib_text)
        self.assertNotIn("No library open", lib_text)

    def test_preferences_fields_and_focused_buttons_handle_keyboard(self):
        workspace = Workspace(self.dir / "keyboard-library")
        self.addCleanup(workspace.close)
        window = self.make_window(workspace)
        window.show_preferences()
        dialog = window.preferences_dialog
        inbox = self.dir / "downloads"
        inbox.mkdir()
        dialog.inbox_edit.setText(str(inbox))
        dialog.inbox_edit.setFocus()
        QTest.keyClick(dialog.inbox_edit, Qt.Key_Return)
        self.assertEqual(workspace.get_settings().inbox, str(inbox))
        self.assertTrue(dialog.isVisible())
        dialog.library_edit.setText(str(self.dir / "new-library"))
        dialog.library_edit.setFocus()
        QTest.keyClick(dialog.library_edit, Qt.Key_Return)
        self.assertTrue(dialog.confirm_box.isVisible())
        self.assertIsNone(window.switch_target)
        for key in (Qt.Key_Return, Qt.Key_Space):
            dialog.confirm_no.setFocus()
            QTest.keyClick(dialog.confirm_no, key)
            self.assertFalse(dialog.confirm_box.isVisible())
            dialog.library_check.click()
        loose = self.make_window()
        loose.show_preferences("inbox")
        self.assertFalse(loose.preferences_dialog.library_section.isVisible())

    # ---- A1: downloads folder, library location, and in-process switch --------------------
    def test_downloads_folder_browse_does_not_save_save_and_clear_persist_and_invalid_keeps_text(self):
        workspace = Workspace(self.dir / "inbox-library")
        self.addCleanup(workspace.close)
        inbox_dir = self.dir / "manual-downloads"
        inbox_dir.mkdir()
        inside_lib = workspace.data_dir / "nested"
        inside_lib.mkdir(parents=True)

        window = self.make_window(workspace=workspace)
        window.show_preferences()
        self.pump()
        dialog = window.preferences_dialog
        self.assertEqual(dialog.inbox_edit.text(), "")

        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(inbox_dir)):
            dialog.inbox_browse.click()
        self.assertEqual(dialog.inbox_edit.text(), str(inbox_dir))
        self.assertEqual(workspace.get_settings().inbox, "")
        self.assertIn("Save to keep it", dialog.message.text())

        dialog.inbox_save.click()
        self.assertEqual(workspace.get_settings().inbox, str(inbox_dir))
        self.assertEqual(dialog.message.text(), "Saved.")
        self.assertNotIn("inbox", paths.load_config())

        for bad_text, snippet in (
            (str(self.dir / "does-not-exist"), "does not exist"),
            ("relative/downloads", "absolute path"),
            (str(inside_lib), "inside the library"),
        ):
            dialog.inbox_edit.setText(bad_text)
            dialog.inbox_save.click()
            self.assertEqual(dialog.inbox_edit.text(), bad_text)
            self.assertTrue(dialog.message.isVisible())
            self.assertIn(snippet, dialog.message.text())
            self.assertEqual(workspace.get_settings().inbox, str(inbox_dir))

        dialog.inbox_clear.click()
        self.assertEqual(dialog.inbox_edit.text(), "")
        self.assertEqual(workspace.get_settings().inbox, "")
        self.assertEqual(dialog.message.text(), "Cleared.")

    def import_pdf(self, workspace, name="paper", pages=2):
        path = make_pdf(self.dir / f"{self.id()}-{name}.pdf", pages=pages)
        return workspace.library.import_stream(io.BytesIO(path.read_bytes()), path.name)

    def test_missing_downloads_folder_opens_preferences_with_inbox_focused(self):
        workspace = Workspace(self.dir / "focus-library")
        self.addCleanup(workspace.close)
        doc = self.import_pdf(workspace, "focus", pages=1)
        window = self.make_window(workspace=workspace)

        window.acquisition.check_folder(doc["id"], {"id": "1"})
        self.pump()
        dialog = window.preferences_dialog
        self.assertTrue(dialog.isVisible())
        self.assertIs(self.app.focusWidget(), dialog.inbox_edit)

    def test_fixed_library_location_is_read_only_with_explanation(self):
        for source, expected in paths.FIXED.items():
            workspace = Workspace(self.dir / f"fixed-{source}", source=source)
            self.addCleanup(workspace.close)
            window = self.make_window(workspace=workspace)
            window.show_preferences()
            self.pump()
            dialog = window.preferences_dialog
            self.assertTrue(dialog.library_edit.isReadOnly())
            self.assertFalse(dialog.library_browse.isEnabled())
            self.assertFalse(dialog.library_check.isEnabled())
            self.assertEqual(dialog.library_note.text(), expected)
            self.assertEqual(dialog.library_edit.text(), str(workspace.data_dir))

    def test_library_switch_refused_with_unsaved_edits_draft_or_unsaved_inbox(self):
        workspace = Workspace(self.dir / "dirty-library")
        self.addCleanup(workspace.close)
        doc = self.import_pdf(workspace, "dirty", pages=1)
        other = Workspace(self.dir / "other-library")
        other.close()

        window = self.make_window(workspace=workspace)
        tab = window.open_library_document(doc["id"])
        pane = tab.main
        self.pump(lambda: pane.view.has_page(0))

        # 1. Unsaved PDF highlight refuses inspect and never calls prepare_switch.
        pane.view.doc.add_highlight(0, [Rect(40, 60, 180, 74)])
        pane.view.edited.emit()
        window.show_preferences()
        self.pump()
        dialog = window.preferences_dialog
        dialog.library_edit.setText(str(other.data_dir))
        with patch.object(workspace, "prepare_switch") as mocked_switch:
            dialog.library_check.click()
            self.assertFalse(dialog.confirm_box.isVisible())
            self.assertEqual(dialog.message.text(), "Save or discard changes in open papers first.")
            mocked_switch.assert_not_called()

        window.save()
        self.assertFalse(pane.modified)

        # 2. Unsaved inbox edit in Preferences refuses inspect.
        dialog.inbox_edit.setText(str(self.dir))
        with patch.object(workspace, "prepare_switch") as mocked_switch:
            dialog.library_check.click()
            self.assertFalse(dialog.confirm_box.isVisible())
            self.assertEqual(dialog.message.text(), "Save the other changes in this dialog first.")
            mocked_switch.assert_not_called()
        dialog.inbox_edit.clear()

        # 3. Inspect succeeds when clean, then a nonempty comment draft added before confirm refuses prepare_switch.
        dialog.library_check.click()
        self.assertTrue(dialog.confirm_box.isVisible())
        self.assertEqual(dialog.confirm_yes.text(), "Open library")
        window.comments.compose()
        window.comments.panel.text.setPlainText("Unsaved comment draft")
        self.pump()
        with patch.object(workspace, "prepare_switch") as mocked_switch:
            dialog.confirm_yes.click()
            self.assertFalse(dialog.confirm_box.isVisible())
            self.assertEqual(dialog.message.text(), "Save or discard changes in open papers first.")
            mocked_switch.assert_not_called()
        window.comments.discard_draft(pane)

    def test_library_move_retains_old_files_and_in_process_switch_reopens_and_frees_old_lock(self):
        # First test Move library: copies files into a new folder while keeping the old library intact.
        source_ws = Workspace(self.dir / "move-source")
        doc = self.import_pdf(source_ws, "paper-a", pages=1)
        move_target = self.dir / "moved-library"
        window = self.make_window(workspace=source_ws)
        window.show_preferences()
        self.pump()
        dialog = window.preferences_dialog
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(move_target)):
            dialog.library_browse.click()
        self.assertFalse(move_target.exists())
        dialog.library_check.click()
        self.assertTrue(dialog.confirm_box.isVisible())
        self.assertEqual(dialog.confirm_yes.text(), "Move library")
        dialog.confirm_yes.click()
        self.pump()
        self.assertEqual(window.switch_target, move_target.resolve())
        self.assertTrue((source_ws.data_dir / "library.sqlite3").is_file())
        self.assertTrue((move_target / "library.sqlite3").is_file())
        source_ws.close()

        # Now test WorkspaceConflict inline when target is locked, followed by full native_app.run([]) reopen.
        other_ws = Workspace(self.dir / "library-b")
        doc_b = self.import_pdf(other_ws, "paper-b", pages=3)
        other_ws.close()
        paths.save_library(move_target)

        seen = {}

        def drive_first_window():
            active = next(w for w in self.app.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible())
            seen["first_window"] = weakref.ref(active)
            seen["first_workspace"] = active.workspace
            seen["first_dir"] = active.workspace.data_dir
            self.assertEqual(active.workspace.data_dir, move_target.resolve())
            active.show_preferences()
            self.app.processEvents()
            dlg = active.preferences_dialog
            dlg.library_edit.setText(str(other_ws.data_dir))
            # While locked by another process, confirming shows WorkspaceConflict inline and stays open.
            with library_lock(other_ws.data_dir):
                dlg.library_check.click()
                dlg.confirm_yes.click()
                self.assertTrue(active.isVisible())
                self.assertIn("open in another PaperAssist", dlg.message.text())
            # Once free, inspect -> confirm closes the first window and triggers the reopen loop.
            dlg.library_check.click()
            self.assertEqual(dlg.confirm_yes.text(), "Open library")
            QTimer.singleShot(40, drive_second_window)
            dlg.confirm_yes.click()

        def drive_second_window():
            windows = [w for w in self.app.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible()]
            if not windows or windows[0].workspace.data_dir != other_ws.data_dir.resolve():
                QTimer.singleShot(20, drive_second_window)
                return
            reopened = windows[0]
            seen["old_window_gone"] = seen["first_window"]() is None
            try:
                seen["first_workspace"].executor.submit(lambda: None)
            except RuntimeError:
                seen["old_workspace_closed"] = True
            seen["second_dir"] = reopened.workspace.data_dir
            seen["second_source"] = reopened.workspace.source
            seen["second_files"] = [d["filename"] for d in reopened.workspace.library.list_documents()]
            reopened.show_preferences()
            self.app.processEvents()
            seen["second_readonly"] = reopened.preferences_dialog.library_edit.isReadOnly()
            # Old lock must be free while the reopened window holds the new library's lock.
            with library_lock(move_target):
                seen["old_lock_free"] = True
            with self.assertRaises(LibraryInUse), library_lock(other_ws.data_dir):
                pass
            reopened.close()

        QTimer.singleShot(40, drive_first_window)
        exit_code = native_app.run([])
        self.assertEqual(exit_code, 0)
        self.assertTrue(seen["old_window_gone"])
        self.assertTrue(seen["old_workspace_closed"])
        self.assertEqual(seen["first_dir"], move_target.resolve())
        self.assertEqual(seen["second_dir"], other_ws.data_dir.resolve())
        self.assertEqual(seen["second_source"], "settings")
        self.assertFalse(seen["second_readonly"])
        self.assertEqual(seen["second_files"], [doc_b["filename"]])
        self.assertNotEqual(doc["filename"], doc_b["filename"])
        self.assertTrue(seen["old_lock_free"])

    # ---- A2: button widths fit sizeHint at 150% -------------------------------------------
    def test_buttons_are_not_truncated_at_150_percent_across_window_and_dialogs(self):
        from test_library_window import cited_pdf

        workspace = Workspace(self.dir / "zoom150-library")
        self.addCleanup(workspace.close)
        workspace.update_settings({"proxy": "https://login.proxy.example.edu/login?url="})
        p_main = cited_pdf(self.dir / "main150.pdf", pages=3)
        p_cited = cited_pdf(self.dir / "cited150.pdf", pages=4)
        main_doc = workspace.library.import_stream(io.BytesIO(p_main.read_bytes()), p_main.name)
        cited_doc = workspace.library.import_stream(io.BytesIO(p_cited.read_bytes()), p_cited.name)
        analyzed = references.analyze(workspace.library, main_doc["id"])
        references.link(workspace.library, main_doc["id"], analyzed["references"][1]["id"], cited_doc["id"])

        window = self.make_window(workspace=workspace, size=(1400, 900))
        window.centralWidget().setSizes([230, 840, 330])
        tab = window.open_library_document(main_doc["id"])
        quick = window.open_library_document_in_quick_view(cited_doc["id"])
        self.pump(lambda: tab.main.view.has_page(0) and quick.view.has_page(0) and window.details.list.count() == 2)
        for dlg in list(window.acquisition.dialogs.values()):
            dlg.close()

        window.preferences_dialog.apply_zoom(1.5)
        self.pump()

        bar = self.main_toolbar(window)
        ext = bar.findChild(QToolButton, "qt_toolbar_ext_button")
        self.assertGreater(self.assert_buttons_fit(bar, "Main toolbar"), 0)
        if ext and ext.isVisible():
            self.assertTrue(ext.isEnabled())
        self.assert_buttons_fit(quick.bar, "Quick view bar")

        # References column: top actions + expanded unlinked card with browser links + expanded linked card.
        self.assertEqual(window.details.acquire.text(), "Acquire references")
        self.assertEqual(window.details.repair.text(), "Repair downloads")
        window.details._expand(window.details.list.item(0))
        card0 = window.details.list.itemWidget(window.details.list.item(0))
        for btn in card0.findChildren(QPushButton):
            if btn.isCheckable() and "Browser searches" in btn.text():
                btn.setChecked(True)
        self.pump()
        self.assert_buttons_fit(window.details_column, "References panel (unlinked card)")
        window.details._expand(window.details.list.item(1))
        self.pump()
        self.assert_buttons_fit(window.details_column, "References panel (linked card)")

        # Comments column: Add comment, composer buttons, and saved comment row.
        window.comments.show_mode("comments")
        self.pump()
        self.assert_buttons_fit(window.details_column, "Comments panel (idle)")
        window.comments.compose()
        window.comments.panel.text.setPlainText("Check 150% composer buttons")
        self.pump()
        self.assert_buttons_fit(window.details_column, "Comments panel (composer)")
        window.comments.save_draft(window.pane())
        self.pump()
        self.assert_buttons_fit(window.details_column, "Comments panel (with comment)")

        # Library dialog (including visible Cancel button).
        window.show_library()
        window.library_dialog.cancel_button.show()
        self.pump()
        self.assert_buttons_fit(window.library_dialog, "Library dialog")
        window.library_dialog.hide()

        # Acquire references dialog.
        window.details.acquire.click()
        self.pump()
        acq_dialog = window.acquisition.dialogs[main_doc["id"]]
        self.assert_buttons_fit(acq_dialog, "Acquire references dialog")
        acq_dialog.close()

        # Sources dialog.
        window.sources_action.trigger()
        self.pump()
        sources_dlg = window.findChild(SourcesDialog)
        self.assert_buttons_fit(sources_dlg, "Sources dialog")
        sources_dlg.reject()

        # Preferences dialog (including confirmation plan box).
        window.show_preferences()
        other = self.dir / "empty-target"
        window.preferences_dialog.library_edit.setText(str(other))
        window.preferences_dialog.library_check.click()
        self.pump()
        self.assert_buttons_fit(window.preferences_dialog, "Preferences dialog")
        window.preferences_dialog.reject()

    def test_side_column_and_expanded_cards_widen_cleanly_for_wide_fonts_at_150_percent(self):
        from test_library_window import cited_pdf

        workspace = Workspace(self.dir / "wide-fonts-library")
        self.addCleanup(workspace.close)
        workspace.update_settings({"proxy": "https://login.proxy.example.edu/login?url="})
        p_main = cited_pdf(self.dir / "main-wide.pdf", pages=3)
        p_cited = cited_pdf(self.dir / "cited-wide.pdf", pages=4)
        main_doc = workspace.library.import_stream(io.BytesIO(p_main.read_bytes()), p_main.name)
        cited_doc = workspace.library.import_stream(io.BytesIO(p_cited.read_bytes()), p_cited.name)
        analyzed = references.analyze(workspace.library, main_doc["id"])
        references.link(workspace.library, main_doc["id"], analyzed["references"][1]["id"], cited_doc["id"])

        window = self.make_window(workspace=workspace, size=(1400, 900))
        window.centralWidget().setSizes([230, 840, 330])
        tab = window.open_library_document(main_doc["id"])
        quick = window.open_library_document_in_quick_view(cited_doc["id"])
        self.pump(lambda: tab.main.view.has_page(0) and quick.view.has_page(0) and window.details.list.count() == 2)
        for dlg in list(window.acquisition.dialogs.values()):
            dlg.close()

        # Wide font metrics where 'Acquire references' asks for >= 338 px at 150%
        pt = 15
        window.preferences_dialog.base_font = QFont("DejaVu Sans Mono", pt)
        window.preferences_dialog.apply_zoom(1.5)
        while window.details.acquire.sizeHint().width() < 338 and pt < 30:
            pt += 1
            window.preferences_dialog.base_font = QFont("DejaVu Sans Mono", pt)
            window.preferences_dialog.apply_zoom(1.5)
        self.pump()
        self.assertGreaterEqual(window.details.acquire.sizeHint().width(), 338)

        # References unlinked card with Browser searches expanded
        window.details._expand(window.details.list.item(0))
        card0 = window.details.list.itemWidget(window.details.list.item(0))
        for btn in card0.findChildren(QPushButton):
            if btn.isCheckable() and "Browser searches" in btn.text():
                btn.setChecked(True)
        self.pump()
        self.assertGreater(self.assert_buttons_fit(window.details_column, "References panel (unlinked card)"), 0)

        # References linked card
        window.details._expand(window.details.list.item(1))
        self.pump()
        self.assertGreater(self.assert_buttons_fit(window.details_column, "References panel (linked card)"), 0)

        # Comments idle
        window.comments.show_mode("comments")
        self.pump()
        self.assertGreater(self.assert_buttons_fit(window.details_column, "Comments panel (idle)"), 0)

        # Comments composer
        window.comments.compose()
        window.comments.panel.text.setPlainText("Check 150% composer buttons")
        self.pump()
        self.assertGreater(self.assert_buttons_fit(window.details_column, "Comments panel (composer)"), 0)

        # Comments with-comment
        window.comments.save_draft(window.pane())
        self.pump()
        self.assertGreater(self.assert_buttons_fit(window.details_column, "Comments panel (with comment)"), 0)

    def test_expanded_card_refits_its_height_when_the_panel_narrows(self):
        from test_library_window import cited_pdf

        workspace = Workspace(self.dir / "refit-library")
        self.addCleanup(workspace.close)
        path = cited_pdf(self.dir / "refit.pdf", pages=3)
        doc = workspace.library.import_stream(io.BytesIO(path.read_bytes()), path.name)
        references.analyze(workspace.library, doc["id"])
        window = self.make_window(workspace=workspace, size=(1400, 900))
        panel, splitter = window.details, window.centralWidget()
        splitter.setSizes([230, 400, 770])
        tab = window.open_library_document(doc["id"])
        self.pump(lambda: tab.main.view.has_page(0) and panel.list.count() >= 1)
        for dlg in list(window.acquisition.dialogs.values()):
            dlg.close()
        item = panel.list.item(0)
        # A scrollbar appearing after the card is sized would take width from it and change the target.
        panel.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The card shows the item's first line. Wrapped text this long is taller at the narrowest width
        # than the label's own size hint, whatever the font metrics, so a stale item height is detectable.
        item.setText("A long reference title that wraps once the column gets narrow " * 25 + "\nauthors\nstatus")
        panel._expand(item)
        self.pump()
        wide, wide_width = item.sizeHint().height(), panel.list.viewport().width()

        splitter.setSizes([230, 1000, 170])  # clamped to the card's minimum width
        self.pump()
        card = panel.list.itemWidget(item)
        self.assertLess(panel.list.viewport().width(), wide_width)
        needed = card.layout().totalHeightForWidth(panel.list.viewport().width())
        self.assertGreater(needed, wide)
        self.assertGreaterEqual(item.sizeHint().height(), needed)


if __name__ == "__main__":
    unittest.main()
