"""Keyboard shortcuts of the native window: navigation, tabs, columns, rename, help and key conflicts."""
import io
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from test_native_support import make_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction, QKeySequence, QShortcut
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QTextBrowser

    from paperassist.native import shortcuts
    from paperassist.native.positions import current_page
    from paperassist.native.renderer import RenderClient
    from paperassist.native.rules import CANCEL
    from paperassist.native.source import FileSource
    from paperassist.native.window import MainWindow
    from paperassist.workspace import Workspace


@needs_qt
class ShortcutTests(unittest.TestCase):
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
        self.workspace = Workspace(self.dir / "library")
        self.addCleanup(self.workspace.close)
        self.paths = [make_pdf(self.dir / f"paper{n}.pdf", pages=3 + n) for n in range(3)]
        self.window = MainWindow(self.client, self.workspace)
        self.window.resize(1200, 800)
        shortcuts.install(self.window)
        self.window.show()
        self.addCleanup(self.window.close)
        QTest.qWaitForWindowActive(self.window)

    def open_papers(self, count=1):
        for path in self.paths[:count]:
            self.window.open_source(FileSource(path))
        self.app.processEvents()
        return self.window.pane().view

    def press(self, key, modifiers=Qt.NoModifier, widget=None):
        QTest.keyClick(widget or QApplication.focusWidget() or self.window.pane().view, key, modifiers)

    def focus_view(self):
        view = self.window.pane().view
        view.setFocus()
        return view

    # ---- no two bindings share a key sequence -------------------------------------------------
    def test_no_key_sequence_is_bound_twice(self):
        self.open_papers()
        sequences = [seq.toString() for action in self.window.findChildren(QAction) for seq in action.shortcuts()]
        sequences += [shortcut.key().toString() for shortcut in self.window.findChildren(QShortcut)]
        repeated = [seq for seq, count in Counter(sequences).items() if count > 1]
        self.assertEqual(repeated, [], "Qt ignores an ambiguous shortcut, which disables both owners")
        self.assertIn("N", sequences)
        self.assertIn("Ctrl+B", sequences)

    def test_table_has_no_repeated_key_and_avoids_browser_reserved_ones(self):
        keys = [QKeySequence(key).toString() for entry in shortcuts.TABLE for key in entry.keys]
        self.assertEqual([key for key, count in Counter(keys).items() if count > 1], [])
        reserved = {"Ctrl+T", "Ctrl+W", "Ctrl+Tab", "Alt+Left", "Alt+Right", "Ctrl+,"}
        self.assertFalse(reserved & set(keys))

    def test_install_twice_keeps_one_set_of_bindings(self):
        self.assertIs(shortcuts.install(self.window), self.window.shortcuts)
        count = len(self.window.findChildren(QAction))
        shortcuts.install(self.window)
        self.assertEqual(len(self.window.findChildren(QAction)), count)

    # ---- pages ----------------------------------------------------------------------------------
    def test_page_keys_move_between_pages_and_stop_at_the_ends(self):
        view = self.open_papers()
        self.focus_view()
        self.assertEqual(current_page(view), 0)
        self.press(Qt.Key_N)
        self.assertEqual(current_page(view), 1)
        self.press(Qt.Key_J)
        self.assertEqual(current_page(view), 2)
        self.press(Qt.Key_P)
        self.press(Qt.Key_K)
        self.assertEqual(current_page(view), 0)
        self.press(Qt.Key_K)
        self.assertEqual(current_page(view), 0)
        self.press(Qt.Key_End)
        self.assertEqual(view.verticalScrollBar().value(), int(view.pages.tops[2]) - 12)
        self.press(Qt.Key_N)
        self.press(Qt.Key_Home)
        self.assertEqual(view.verticalScrollBar().value(), 0)

    def test_page_field_follows_the_document_and_g_enter_round_trips(self):
        view = self.open_papers()
        field = self.window.shortcuts.page_field
        self.assertEqual((field.edit.text(), field.total.text()), ("1", "/ 3"))
        self.press(Qt.Key_N, widget=self.focus_view())
        self.assertEqual(field.edit.text(), "2")
        self.press(Qt.Key_G)
        self.assertIs(QApplication.focusWidget(), field.edit)
        QTest.keyClicks(field.edit, "99")  # beyond the end: clamped to the last page
        self.press(Qt.Key_Return)
        self.assertIs(QApplication.focusWidget(), view)
        self.assertEqual(field.edit.text(), "3")
        self.press(Qt.Key_G)
        QTest.keyClicks(field.edit, "1")
        self.press(Qt.Key_Escape)  # leaving without Enter changes nothing
        self.assertIs(QApplication.focusWidget(), view)
        self.assertEqual(field.edit.text(), "3")

    def test_page_field_belongs_to_the_active_document(self):
        self.open_papers(2)
        field = self.window.shortcuts.page_field
        self.assertEqual(field.total.text(), "/ 4")
        self.window.tabs.setCurrentIndex(0)
        self.assertEqual(field.total.text(), "/ 3")
        self.window.close_tab(0)
        self.window.close_tab(0)
        self.assertFalse(field.isEnabled())
        self.assertEqual(field.edit.text(), "")
        self.press(Qt.Key_G, widget=self.window)  # nothing open: the key does nothing

    def test_scroll_keys_and_zoom_reach_a_focused_document(self):
        view = self.open_papers()
        self.focus_view()
        bar = view.verticalScrollBar()
        view.zoom_by(2)  # zoomed so that there is something to scroll horizontally
        for key, modifiers, direction in [(Qt.Key_Down, Qt.NoModifier, 1), (Qt.Key_Space, Qt.NoModifier, 1),
                                          (Qt.Key_PageDown, Qt.NoModifier, 1), (Qt.Key_Up, Qt.NoModifier, -1),
                                          (Qt.Key_Space, Qt.ShiftModifier, -1), (Qt.Key_PageUp, Qt.NoModifier, -1)]:
            before = bar.value()
            self.press(key, modifiers)
            self.assertGreater((bar.value() - before) * direction, 0, f"{key} {modifiers}")
        horizontal = view.horizontalScrollBar()
        self.assertGreater(horizontal.maximum(), 0)
        self.press(Qt.Key_Right)
        self.assertGreater(horizontal.value(), 0)
        self.press(Qt.Key_0, Qt.ControlModifier)
        self.assertTrue(view.fit)
        self.assertEqual(horizontal.maximum(), 0)

    # ---- tabs ----------------------------------------------------------------------------------
    def test_comma_and_period_step_tabs_without_wrapping_and_x_closes(self):
        self.open_papers(3)
        tabs = self.window.tabs
        self.assertEqual(tabs.currentIndex(), 2)
        self.press(Qt.Key_Period, widget=self.focus_view())
        self.assertEqual(tabs.currentIndex(), 2)
        self.press(Qt.Key_Comma)
        self.assertEqual(tabs.currentIndex(), 1)
        self.press(Qt.Key_Comma, widget=self.focus_view())
        self.press(Qt.Key_Comma, widget=self.focus_view())
        self.assertEqual(tabs.currentIndex(), 0)
        self.press(Qt.Key_X, widget=self.focus_view())
        self.assertEqual(tabs.count(), 2)

    def test_x_asks_about_unsaved_changes_and_cancel_keeps_the_tab(self):
        view = self.open_papers()
        view.doc.add_ink(0, [[(50, 50), (100, 100)]])
        self.assertTrue(self.window.pane().modified)
        asked = []
        self.window.ask_decision = lambda pane: asked.append(pane) or CANCEL
        self.press(Qt.Key_X, widget=self.focus_view())
        self.assertEqual((len(asked), self.window.tabs.count()), (1, 1))

    # ---- columns and the filter ------------------------------------------------------------------
    def test_b_and_o_toggle_columns(self):
        self.open_papers()
        window = self.window
        self.press(Qt.Key_O, widget=self.focus_view())
        self.assertFalse(window.outline_column.isVisibleTo(window))
        self.assertTrue(window.details_column.isVisibleTo(window))
        self.press(Qt.Key_O)
        self.assertTrue(window.outline_column.isVisibleTo(window))
        self.assertTrue(window.outline.hasFocus())
        self.press(Qt.Key_B)
        self.assertFalse(window.outline_column.isVisibleTo(window) or window.details_column.isVisibleTo(window))
        self.press(Qt.Key_B, widget=self.focus_view())
        self.assertTrue(window.outline_column.isVisibleTo(window) and window.details_column.isVisibleTo(window))

    def test_l_opens_the_library(self):
        self.open_papers()
        with patch.object(self.window, "show_library") as show:
            self.press(Qt.Key_L, widget=self.focus_view())
        show.assert_called_once()

    def test_slash_focuses_the_filter_and_escape_clears_then_leaves(self):
        self.open_papers()
        window, field = self.window, self.window.details.filter
        window.set_columns(details=False)
        self.press(Qt.Key_Slash, widget=self.focus_view())
        self.assertTrue(window.details_column.isVisibleTo(window))
        self.assertIs(QApplication.focusWidget(), field)
        QTest.keyClicks(field, "nj,")  # typing never triggers a plain-key shortcut
        self.assertEqual(field.text(), "nj,")
        self.assertEqual(window.tabs.currentIndex(), 0)
        self.press(Qt.Key_Escape)
        self.assertEqual(field.text(), "")
        self.assertIs(QApplication.focusWidget(), field)
        self.press(Qt.Key_Escape)
        self.assertIs(QApplication.focusWidget(), window.pane().view)

    def test_plain_keys_are_ignored_while_a_modal_dialog_is_open(self):
        view = self.open_papers()
        dialog = QDialog(self.window)
        dialog.setModal(True)
        QLineEdit(dialog)
        dialog.show()
        QTest.qWaitForWindowActive(dialog)
        self.addCleanup(dialog.close)
        for key in (Qt.Key_N, Qt.Key_B, Qt.Key_X):
            QTest.keyClick(dialog, key)
        self.assertEqual(current_page(view), 0)
        self.assertEqual((self.window.tabs.count(), self.window.outline_column.isVisibleTo(self.window)), (1, True))

    # ---- renaming ----------------------------------------------------------------------------------
    def edit_tab_name(self, text, finish):
        editor = next(e for e in self.window.tabs.tabBar().findChildren(QLineEdit) if e.isVisible())
        editor.selectAll()
        QTest.keyClicks(editor, text) if text else QTest.keyClick(editor, Qt.Key_Delete)
        finish(editor)

    def test_double_click_renames_only_the_tab(self):
        document = self.workspace.library.import_stream(io.BytesIO(self.paths[0].read_bytes()), "alpha.pdf")
        self.window.open_library_document(document["id"])
        quick = self.window.open_library_document_in_quick_view(document["id"])
        # Background reference analysis can add job rows while the editor is open.
        original_document = self.workspace.library.get_document(document["id"])
        pdf_path = self.workspace.library.get_path(document["id"])
        pdf_before = pdf_path.read_bytes()
        title = self.window.tab().main.title
        bar = self.window.tabs.tabBar()
        QTest.mouseDClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(0).center())
        self.edit_tab_name("Loops review", lambda editor: QTest.keyClick(editor, Qt.Key_Return))
        self.assertEqual(pdf_path.read_bytes(), pdf_before)
        self.assertEqual(self.workspace.library.get_document(document["id"]), original_document)
        self.assertEqual(self.window.tab().main.title, title)
        self.assertEqual(quick.title, title)
        self.assertTrue(self.window.tabs.tabText(0).startswith("Loops review"))
        self.assertIn("Loops review", self.window.tabs.tabToolTip(0))
        self.assertIn("Loops review", bar.accessibleTabName(0))
        self.assertTrue(bar.hasFocus())

        # A metadata refresh changes the underlying paper title, not this alias.
        self.window.tab().main.view.doc.source.title = "Corrected title"
        self.window.refresh()
        self.assertTrue(self.window.tabs.tabText(0).startswith("Loops review"))
        self.window.shortcuts.rename_tab(0)
        self.edit_tab_name("", lambda editor: QTest.keyClick(editor, Qt.Key_Return))
        self.assertTrue(self.window.tabs.tabText(0).startswith("Corrected title"))

    def test_alias_is_independent_for_two_tabs_of_the_same_paper(self):
        document = self.workspace.library.import_stream(io.BytesIO(self.paths[0].read_bytes()), "alpha.pdf")
        first = self.window.open_library_document(document["id"])
        second = self.window._add_tab(self.window._wrap(self.window._open_pane(self.window.library_source(document["id"]))))
        self.window.shortcuts.set_label(first, "First tab")
        self.assertEqual(first.title, "First tab")
        self.assertNotEqual(second.title, "First tab")
        self.assertIn("First tab", self.window.tabs.tabToolTip(0))
        self.assertNotIn("First tab", self.window.tabs.tabToolTip(1))
        self.window.close_tab(0)
        self.assertNotEqual(self.window.tab().title, "First tab")
        self.assertEqual(self.workspace.library.get_document(document["id"])["label"], "")

    def test_ampersand_in_tab_title_escapes_mnemonic_in_tab_text(self):
        document = self.workspace.library.import_stream(io.BytesIO(self.paths[0].read_bytes()), "alpha.pdf")
        self.window.open_library_document(document["id"])
        bar = self.window.tabs.tabBar()
        QTest.mouseDClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(0).center())
        self.edit_tab_name("A&A & Keppens", lambda editor: QTest.keyClick(editor, Qt.Key_Return))
        self.app.processEvents()

        tab_text = self.window.tabs.tabText(0)
        self.assertIn("A&&A && Keppens", tab_text)
        self.assertTrue(QKeySequence.mnemonic(tab_text).isEmpty())
        self.assertIn("A&A & Keppens", self.window.tabs.tabToolTip(0))
        self.assertNotIn("&&", self.window.tabs.tabToolTip(0))

        self.window.shortcuts.rename_tab(0)
        editor = next(e for e in bar.findChildren(QLineEdit) if e.isVisible())
        self.assertEqual(editor.text(), "A&A & Keppens")
        QTest.keyClick(editor, Qt.Key_Escape)

    def test_escape_cancels_and_an_empty_label_restores_the_paper_title(self):
        document = self.workspace.library.import_stream(io.BytesIO(self.paths[0].read_bytes()), "alpha.pdf")
        self.window.open_library_document(document["id"])
        original = self.window.tabs.tabText(0)
        self.window.tabs.tabBar().setFocus()
        self.window.shortcuts.rename_tab(0)
        self.edit_tab_name("Draft", lambda editor: QTest.keyClick(editor, Qt.Key_Escape))
        self.assertEqual(self.workspace.library.get_document(document["id"])["label"], "")
        self.assertEqual(self.window.tabs.tabText(0), original)
        self.window.shortcuts.rename_tab(0)
        self.edit_tab_name("Kept", lambda editor: QTest.keyClick(editor, Qt.Key_Return))
        self.assertEqual(self.workspace.library.get_document(document["id"])["label"], "")
        self.assertTrue(self.window.tabs.tabText(0).startswith("Kept"))
        self.window.shortcuts.rename_tab(0)
        self.edit_tab_name("", lambda editor: QTest.keyClick(editor, Qt.Key_Return))
        self.assertEqual(self.workspace.library.get_document(document["id"])["label"], "")
        self.assertEqual(self.window.tabs.tabText(0), original)

    def test_f2_on_the_focused_tab_bar_renames_and_losing_focus_commits(self):
        self.open_papers()
        self.window.tabs.tabBar().setFocus()
        QTest.keyClick(self.window.tabs.tabBar(), Qt.Key_F2)
        editor = next(e for e in self.window.tabs.tabBar().findChildren(QLineEdit) if e.isVisible())
        editor.selectAll()
        QTest.keyClicks(editor, "Session name")
        self.window.pane().view.setFocus()
        self.app.processEvents()
        self.assertTrue(self.window.tabs.tabText(0).startswith("Session name"))
        self.assertEqual(self.window.tab().title, "Session name")
        self.assertEqual(self.window.pane().title, "paper0.pdf")
        self.assertEqual(self.paths[0].name, "paper0.pdf")  # a loose file keeps its name on disk
        self.window.shortcuts.rename_tab(0)
        self.edit_tab_name("", lambda editor: QTest.keyClick(editor, Qt.Key_Return))
        self.assertEqual(self.window.pane().title, "paper0.pdf")

    def test_f2_does_nothing_when_the_document_has_focus(self):
        self.open_papers()
        self.press(Qt.Key_F2, widget=self.focus_view())
        self.assertEqual([e for e in self.window.tabs.tabBar().findChildren(QLineEdit) if e.isVisible()], [])

    # ---- help ----------------------------------------------------------------------------------------
    def test_question_mark_opens_the_help_listing_the_installed_bindings(self):
        self.open_papers()
        # The offscreen platform sends "?" without Shift; the Xvfb run covers the real keyboard layouts.
        self.press(Qt.Key_Question, widget=self.focus_view())
        dialog = self.window.findChild(QDialog, "shortcuts-help")
        self.assertIsNotNone(dialog)
        text = dialog.findChild(QTextBrowser).toPlainText()
        for entry in shortcuts.TABLE:
            self.assertIn(entry.label, text)
        self.assertIn("Fit width", text)  # an action the table does not own is listed from the window
        self.assertIn(QKeySequence('Ctrl+0').toString(QKeySequence.NativeText), text)
        dialog.close()
        self.assertIsNotNone(shortcuts.show_help(self.window))  # also callable from the app menu
