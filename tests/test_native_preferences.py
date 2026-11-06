"""Tests for the native brand menu, interface size preferences, and About dialog."""
from collections import Counter
from importlib import metadata
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_native_support import make_pdf, needs_qt, pdf_available

if pdf_available:
    import paperassist
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction, QShortcut
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTextBrowser, QToolBar

    from paperassist import settings
    from paperassist.native import app_menu, preferences_dialog, shortcuts
    from paperassist.native.layout import MARGIN
    from paperassist.native.renderer import RenderClient
    from paperassist.native.source import FileSource
    from paperassist.native.sources_dialog import SourcesDialog
    from paperassist.native.window import MainWindow
    from paperassist.workspace import Workspace


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
                "outline_hint": window.outline_column.minimumSizeHint(),
                "details_hint": window.details_column.minimumSizeHint(),
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


if __name__ == "__main__":
    unittest.main()
