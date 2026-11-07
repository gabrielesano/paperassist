"""Computed accessible names and keyboard focus across real native dialogs."""
import os
import unittest

from test_library_window import LibraryWindowCase
from test_native_support import needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtGui import QAccessible
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QDialogButtonBox, QLineEdit, QTabBar, QTreeWidget, QWidget
    from shiboken6 import isValid

    from paperassist.native.shortcuts import show_help
    from paperassist.native.sources_dialog import show_sources


@needs_qt
class AccessibilityTests(LibraryWindowCase):
    def setUp(self):
        super().setUp()
        self.window = self.make_window()
        self.window.acquisition.main_opened = lambda document_id: None
        self.view = self.open_document(self.window, self.docs[0]).main.view
        self.window.activateWindow()
        self.pump(lambda: self.window.isActiveWindow())

    def test_tab_close_buttons_identify_their_papers_after_rename_and_reorder(self):
        self.open_document(self.window, self.docs[1])
        self.window.shortcuts.set_label(self.window.tab().main, "My reference")
        bar = self.window.tabs.tabBar()
        bar.moveTab(1, 0)
        for index in range(self.window.tabs.count()):
            title = self.window.tabs.widget(index).main.title
            buttons = [bar.tabButton(index, side) for side in (QTabBar.LeftSide, QTabBar.RightSide)]
            (button,) = [button for button in buttons if button is not None]
            self.assertEqual(QAccessible.queryAccessibleInterface(button).text(QAccessible.Name), f"Close {title}")

    def open_dialog(self, name):
        if name == "preferences":
            self.window.show_preferences()
            return self.window.preferences_dialog
        if name == "about":
            self.window.show_about()
            return self.window.about_dialog
        if name == "help":
            return show_help(self.window)
        if name == "sources":
            return show_sources(self.window)
        if name == "library":
            self.window.show_library()
            return self.window.library_dialog
        if name == "acquisition":
            self.window.acquisition.offer(self.docs[0]["id"], [])
            return self.window.acquisition.dialogs[self.docs[0]["id"]]
        if name == "downloaded":
            self.window.acquisition._folder_result(self.docs[0]["id"], {"id": "1"}, {
                "status": "ambiguous", "candidates": [{"name": "one.pdf"}, {"name": "two.pdf"}]})
            return self.window.acquisition._folder_dialog
        self.window.acquisition.choose_link(self.docs[0]["id"], {"id": "1", "raw": "A reference"})
        return self.window.acquisition._link_dialog

    def test_dialog_controls_have_computed_accessible_names(self):
        for name in ("preferences", "about", "help", "sources", "library", "acquisition", "link", "downloaded"):
            with self.subTest(dialog=name):
                dialog = self.open_dialog(name)
                self.pump(seconds=0.05)
                try:
                    for widget in dialog.findChildren(QWidget):
                        if (isinstance(widget, (QAbstractButton, QComboBox, QLineEdit, QTreeWidget))
                                and widget.isVisibleTo(dialog) and widget.isEnabled()):
                            accessible = QAccessible.queryAccessibleInterface(widget)
                            self.assertTrue(accessible.text(QAccessible.Name),
                                            f"{name}: {type(widget).__name__} {widget.objectName()}")
                finally:
                    dialog.reject()
                self.window.activateWindow()
                self.pump(lambda: self.window.isActiveWindow())

    @unittest.skipUnless(os.environ.get("PAPERASSIST_WINDOW_MANAGER_TEST") == "1", "Requires a real window manager")
    def test_escape_restores_document_focus_after_main_dialogs(self):
        for name in ("preferences", "about", "help", "sources", "library", "acquisition", "link", "downloaded"):
            with self.subTest(dialog=name):
                self.view.setFocus()
                self.pump(lambda: self.app.focusWidget() is self.view)
                dialog = self.open_dialog(name)
                dialog.activateWindow()
                self.pump(lambda: dialog.isActiveWindow())
                QTest.keyClick(dialog, Qt.Key_Escape)
                self.pump(lambda: self.window.isActiveWindow() and self.app.focusWidget() is self.view)

    @unittest.skipUnless(os.environ.get("PAPERASSIST_WINDOW_MANAGER_TEST") == "1", "Requires a real window manager")
    def test_metadata_dialog_returns_focus_to_the_library(self):
        self.window.show_library()
        library = self.window.library_dialog
        self.pump(lambda: library.isActiveWindow())
        library.table.setCurrentItem(library.table.topLevelItem(0))
        library.table.setFocus()
        library.edit_metadata()
        dialog = library.editor
        dialog.activateWindow()
        self.pump(lambda: dialog.isActiveWindow())
        QTest.keyClick(dialog, Qt.Key_Escape)
        self.pump(lambda: library.isActiveWindow() and self.app.focusWidget() is library.table)
        library.reject()

    @unittest.skipUnless(os.environ.get("PAPERASSIST_WINDOW_MANAGER_TEST") == "1", "Requires a real window manager")
    def test_enter_and_space_activate_dialog_buttons_and_restore_focus(self):
        for key in (Qt.Key_Return, Qt.Key_Space):
            with self.subTest(key=key):
                self.view.setFocus()
                self.pump(lambda: self.app.focusWidget() is self.view)
                dialog = self.open_dialog("preferences")
                self.pump(lambda: dialog.isActiveWindow())
                button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close)
                button.setFocus()
                self.pump(lambda: self.app.focusWidget() is button)
                QTest.keyClick(button, key)
                self.pump(lambda: not dialog.isVisible())
                self.pump(lambda: self.window.isActiveWindow() and self.app.focusWidget() is self.view)

    def test_tab_and_shift_tab_return_to_the_document(self):
        self.view.setFocus()
        QTest.keyClick(self.view, Qt.Key_Tab)
        following = self.app.focusWidget()
        self.assertIsNotNone(following)
        self.assertIsNot(following, self.view)
        QTest.keyClick(following, Qt.Key_Backtab)
        self.assertIs(self.app.focusWidget(), self.view)

    def test_preferences_return_to_the_document_if_the_invoking_editor_was_deleted(self):
        self.window.shortcuts.rename_tab(0)
        editor = self.window.shortcuts.editor
        self.window.show_preferences()
        self.pump(seconds=0.05)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(editor))
        self.window.preferences_dialog.reject()
        self.pump(lambda: self.app.focusWidget() is self.view)
