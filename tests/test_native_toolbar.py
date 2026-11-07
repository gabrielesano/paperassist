"""Overflow actions remain reachable and owned by their toolbar across openings."""
import unittest

from test_native_support import needs_qt
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QToolBar, QToolButton

from paperassist.native.toolbar import install_overflow


@needs_qt
class ToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_keyboard_and_mouse_menu_retain_original_actions(self):
        window = QMainWindow()
        self.addCleanup(window.close)
        toolbar = QToolBar("Test actions")
        window.addToolBar(toolbar)
        actions = [toolbar.addAction(f"Long action {number}") for number in range(10)]
        install_overflow(toolbar)
        window.resize(320, 200)
        window.show()
        self.app.processEvents()
        extension = toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
        hidden = [action for action in actions if not toolbar.widgetForAction(action).isVisible()]
        self.assertTrue(hidden)
        self.assertEqual(extension.focusPolicy(), Qt.StrongFocus)
        observed = QSignalSpy(hidden[0].triggered)
        for key in (Qt.Key_Space, Qt.Key_Return):
            extension.setFocus()
            QTest.keyClick(extension, key)
            menu = self.app.activePopupWidget()
            self.assertIsInstance(menu, QMenu)
            self.assertEqual(menu.actions(), hidden)
            QTest.keyClick(menu, Qt.Key_Return)
            self.app.processEvents()
        self.assertEqual(observed.count(), 2)
        QTest.mouseClick(extension, Qt.LeftButton)
        menu = self.app.activePopupWidget()
        self.assertEqual(menu.actions(), hidden)
        QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(hidden[0]).center())
        self.assertEqual(observed.count(), 3)
        self.assertEqual(toolbar.actions(), actions)
