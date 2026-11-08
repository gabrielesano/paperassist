"""Independent and stable page and zoom controls for split papers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_native_support import make_pdf, needs_qt
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from paperassist.native.positions import current_page
from paperassist.native.renderer import RenderClient
from paperassist.native.source import FileSource
from paperassist.native.window import MainWindow


@needs_qt
class NavigationControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        config = patch('paperassist.paths.config_file', return_value=self.path / 'config.json')
        config.start()
        self.addCleanup(config.stop)
        self.paths = [make_pdf(self.path / f'{i}.pdf', pages=3 + i) for i in range(3)]
        self.window = MainWindow(self.client)
        self.window.resize(1600, 900)
        self.window.show()
        self.addCleanup(self.window.close)
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        self.tab = self.window.open_source(FileSource(self.paths[0]))
        self.quick = self.window.open_quick(FileSource(self.paths[1]))
        self.app.processEvents()

    def action(self, toolbar, name):
        return next(action for action in toolbar.actions() if action.text() == name)

    def test_controls_target_their_paper_in_both_layouts_and_after_tab_switch(self):
        main, quick = self.tab.main.view, self.quick.view
        for side in (False, True):
            self.tab.set_side_by_side(side)
            self.tab.activate(self.quick)
            self.window.shortcuts.page_field.edit.setText('2')
            self.window.shortcuts.page_field.commit()
            self.assertEqual(current_page(main), 1)
            self.assertEqual(current_page(quick), 0)
            self.quick.navigation_field.edit.setText('3')
            self.quick.navigation_field.commit()
            self.assertEqual(current_page(quick), 2)
            self.assertEqual(current_page(main), 1)
            main_zoom, quick_zoom = main.zoom, quick.zoom
            self.action(self.quick.navigation_bar, 'Second paper: zoom in').trigger()
            self.assertEqual(main.zoom, main_zoom)
            self.assertGreater(quick.zoom, quick_zoom)
            self.window.fit_action.trigger()
            self.assertTrue(main.fit)
            self.assertFalse(quick.fit)
            main.goto(0)
            quick.goto(0)
        self.window.open_source(FileSource(self.paths[2]))
        self.assertFalse(self.quick.isVisible())
        self.window.tabs.setCurrentIndex(0)
        self.assertTrue(self.quick.navigation_bar.isVisible())
        self.tab.activate(self.quick)
        self.assertEqual(self.window.shortcuts.page_field.total.text(), '/ 3')
        self.assertEqual(self.quick.navigation_field.total.text(), '/ 4')
        self.window.shortcuts.focus_page()
        self.app.processEvents()
        focused = QApplication.focusWidget()
        self.assertEqual(focused.accessibleName(), 'Second paper: page number')
        self.action(self.quick.navigation_bar, 'Second paper: find').trigger()
        self.assertTrue(self.quick.find_bar.isVisible())

    def test_toolbar_positions_do_not_move_when_zoom_label_changes(self):
        main_bar, quick_bar = self.window.main_bar, self.quick.bar
        main_next = self.action(main_bar, 'Main paper: next page')
        quick_next = self.action(quick_bar, 'Second paper: next page')
        quick_zoom = self.action(quick_bar, 'Second paper: zoom in')

        def positions():
            return (self.window.shortcuts.page_field.width(), main_bar.widgetForAction(main_next).x(),
                    main_bar.widgetForAction(self.window.fit_action).width(),
                    main_bar.widgetForAction(self.window.highlight_action).x(),
                    self.quick.title_action.isVisible(), self.quick.title_label.width(),
                    quick_bar.widgetForAction(quick_next).x(),
                    quick_bar.widgetForAction(self.quick.navigation_fit).width(),
                    quick_bar.widgetForAction(quick_zoom).x())

        for ui_zoom in (0.8, 1, 1.5):
            self.window.preferences_dialog.apply_zoom(ui_zoom)
            for width in (900, 1100, 1300, 1600):
                with self.subTest(ui_zoom=ui_zoom, window_width=width):
                    self.window.resize(width, 900)
                    for view in (self.tab.main.view, self.quick.view):
                        view.fit_width()
                    self.window.refresh()
                    self.app.processEvents()
                    before = positions()
                    for zoom in (1.4, 4.0):
                        for view in (self.tab.main.view, self.quick.view):
                            view._set_zoom(zoom)
                        self.window.refresh()
                        self.app.processEvents()
                        self.assertEqual(before, positions(), f'UI {ui_zoom}, document zoom {zoom}')

    def test_page_field_remains_editable_through_overflow(self):
        self.window.resize(800, 700)
        self.tab.set_side_by_side(True)
        self.quick.navigation_bar.setFixedWidth(45)
        self.app.processEvents()
        bar = self.quick.navigation_bar
        extension = bar.findChild(QToolButton, 'qt_toolbar_ext_button')
        QTest.mouseClick(extension, Qt.LeftButton)
        menu = self.app.activePopupWidget()
        action = next((a for a in menu.actions() if a.text() == 'Second paper: page number'), None)
        self.assertIsNotNone(action, ([a.text() for a in menu.actions()], bar.width(), self.quick.navigation_field.geometry(), self.quick.navigation_field.isVisible()))
        submenu = action.menu()
        submenu.popup(menu.mapToGlobal(menu.rect().topRight()))
        field = submenu.actions()[0].defaultWidget()
        field.edit.setText('2')
        QTest.keyClick(field.edit, Qt.Key_Return)
        self.assertEqual(current_page(self.quick.view), 1)
        self.assertEqual(current_page(self.tab.main.view), 0)
