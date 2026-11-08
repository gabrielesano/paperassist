"""Overflow actions remain reachable and owned by their toolbar across openings."""
import unittest

from test_native_support import needs_qt
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QToolBar, QToolButton

from paperassist.native.annotation_bar import COLORS, ColorSwatches
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

    def test_hidden_color_swatches_are_usable_from_overflow(self):
        window = QMainWindow()
        self.addCleanup(window.close)
        toolbar = QToolBar('Annotations')
        window.addToolBar(toolbar)
        toolbar.addAction('A long preceding action')
        colors = ColorSwatches(COLORS)
        toolbar.addWidget(colors)
        overflow = install_overflow(toolbar)
        window.resize(160, 200)
        window.show()
        self.app.processEvents()
        self.assertFalse(colors.isVisible())
        extension = toolbar.findChild(QToolButton, 'qt_toolbar_ext_button')
        QTest.keyClick(extension, Qt.Key_Space)
        submenu = next(action.menu() for action in overflow.menu.actions() if action.text() == 'Annotation color')
        submenu.setActiveAction(submenu.actions()[1])
        QTest.keyClick(submenu, Qt.Key_Return)
        self.assertEqual(colors.currentIndex(), 1)

    def test_selected_swatch_has_a_distinct_focus_indicator(self):
        colors = ColorSwatches(COLORS)
        self.addCleanup(colors.close)
        colors.setCurrentIndex(0)
        colors.show()
        colors.activateWindow()
        chip = colors.buttons[0][1]
        other = colors.buttons[1][1]
        other.setFocus()
        self.app.processEvents()
        without_focus = chip.grab().toImage()
        chip.setFocus()
        self.app.processEvents()
        self.assertTrue(chip.hasFocus())
        self.assertNotEqual(without_focus, chip.grab().toImage())

    def test_overflow_keeps_existing_action_submenus(self):
        window = QMainWindow()
        self.addCleanup(window.close)
        toolbar = QToolBar('Test')
        window.addToolBar(toolbar)
        toolbar.addAction('A long preceding action')
        submenu = QMenu('Existing submenu', toolbar)
        submenu.addAction('Choice')
        action = toolbar.addAction('With submenu')
        action.setMenu(submenu)
        overflow = install_overflow(toolbar)
        window.resize(160, 200)
        window.show()
        self.app.processEvents()
        extension = toolbar.findChild(QToolButton, 'qt_toolbar_ext_button')
        for _ in range(2):
            QTest.mouseClick(extension, Qt.LeftButton)
            self.assertIn(action, overflow.menu.actions())
            self.assertIs(action.menu(), submenu)
            overflow.menu.hide()
