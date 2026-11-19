"""Panning in the native PDF reader: Pan tool (M), middle-drag, and hold-Space drag."""
import unittest

from support import QtTestCase, make_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QTextBrowser

    from paperassist.native.engine import Rect
    from paperassist.native.source import FileSource
    from paperassist.native.window import MainWindow


@needs_qt
class PanningTests(QtTestCase):
    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.client)
        self.window.resize(1200, 800)
        self.window.show()
        self.window.activateWindow()
        self.bar = self.window.annotation_bar
        self.addCleanup(lambda: self.cleanup_window(self.window))
        QTest.qWaitForWindowActive(self.window)

    def open_zoomed(self, name="pan", pages=4):
        path = make_pdf(self.dir / f"{self.id()}-{name}.pdf", pages=pages)
        tab = self.window.open_source(FileSource(path))
        view = tab.main.view
        self.pump(lambda: view.has_page(0))
        view.zoom_by(2.0)
        self.app.processEvents()
        self.assertGreater(view.horizontalScrollBar().maximum(), 100)
        self.assertGreater(view.verticalScrollBar().maximum(), 200)
        view.horizontalScrollBar().setValue(50)
        view.verticalScrollBar().setValue(100)
        view.setFocus()
        return view

    def drag(self, view, start, end, button=Qt.LeftButton, modifiers=Qt.NoModifier):
        QTest.mousePress(view.viewport(), button, modifiers, start)
        QTest.mouseMove(view.viewport(), end)
        QTest.mouseRelease(view.viewport(), button, modifiers, end)

    def link_center(self, view, page=0):
        link = view.doc.links(page)[0]
        r = link.rect
        return view._band(page, Rect((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, r.x1, r.y1)).topLeft().toPoint()

    def test_middle_drag_pans_in_any_tool_and_preserves_selection(self):
        view = self.open_zoomed("middle")
        view.select_all()
        selected_before = view.selected_text()
        self.assertTrue(selected_before)

        for tool in ("select", "highlight", "ink"):
            with self.subTest(tool=tool):
                view.set_tool(tool)
                h0, v0 = view.horizontalScrollBar().value(), view.verticalScrollBar().value()
                start = QPoint(300, 300)
                mid = QPoint(260, 230)
                QTest.mousePress(view.viewport(), Qt.MiddleButton, Qt.NoModifier, start)
                self.assertEqual(view.viewport().cursor().shape(), Qt.ClosedHandCursor)
                QTest.mouseMove(view.viewport(), mid)
                QTest.mouseRelease(view.viewport(), Qt.MiddleButton, Qt.NoModifier, mid)
                self.assertEqual(view.horizontalScrollBar().value(), h0 + 40)
                self.assertEqual(view.verticalScrollBar().value(), v0 + 70)
                self.assertEqual(view.selected_text(), selected_before)
                self.assertFalse(view.doc.modified)
                self.assertEqual(view.doc.annotations(0), [])

        # Middle-clicking directly on a link does not follow it.
        view.goto(0)
        view.horizontalScrollBar().setValue(0)
        v_before = view.verticalScrollBar().value()
        QTest.mouseClick(view.viewport(), Qt.MiddleButton, Qt.NoModifier, self.link_center(view))
        self.assertEqual(view.verticalScrollBar().value(), v_before)

    def test_pan_tool_shortcut_toggles_pans_and_keeps_annotation_bar_hidden(self):
        view = self.open_zoomed("tool")
        self.assertFalse(self.bar.isVisibleTo(self.window))

        QTest.keyClick(view, Qt.Key_M)
        self.assertTrue(view.panning)
        self.assertTrue(self.bar.pan.isChecked())
        self.assertFalse(self.bar.isVisibleTo(self.window))
        self.assertEqual(view.viewport().cursor().shape(), Qt.OpenHandCursor)

        h0, v0 = view.horizontalScrollBar().value(), view.verticalScrollBar().value()
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(280, 280))
        self.assertEqual(view.viewport().cursor().shape(), Qt.ClosedHandCursor)
        QTest.mouseMove(view.viewport(), QPoint(230, 200))
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(230, 200))
        self.assertEqual(view.viewport().cursor().shape(), Qt.OpenHandCursor)
        self.assertEqual(view.horizontalScrollBar().value(), h0 + 50)
        self.assertEqual(view.verticalScrollBar().value(), v0 + 80)
        self.assertIsNone(view.selection)
        self.assertFalse(view.doc.modified)

        # Pressing M again toggles Pan off back to Select.
        QTest.keyClick(view, Qt.Key_M)
        self.assertEqual(view.tool, "select")
        self.assertFalse(self.bar.pan.isChecked())
        self.assertTrue(self.bar.select.isChecked())

        # Works in read-only panes too.
        view.locked = True
        self.bar.refresh()
        self.assertTrue(self.bar.pan.isEnabled())
        self.bar.pan.trigger()
        self.assertTrue(view.panning)
        h1, v1 = view.horizontalScrollBar().value(), view.verticalScrollBar().value()
        self.drag(view, QPoint(260, 260), QPoint(290, 310))
        self.assertEqual(view.horizontalScrollBar().value(), h1 - 30)
        self.assertEqual(view.verticalScrollBar().value(), v1 - 50)
        self.bar.pan.trigger()
        self.assertEqual(view.tool, "select")

    def test_pan_tool_follows_clicked_link_but_drags_starting_on_link_pan_instead(self):
        view = self.open_zoomed("links")
        view.goto(0)
        view.horizontalScrollBar().setValue(0)
        QTest.keyClick(view, Qt.Key_M)
        link_pt = self.link_center(view)

        # Drag starting on the link pans instead of jumping to the link target.
        v0 = view.verticalScrollBar().value()
        self.drag(view, link_pt, link_pt + QPoint(0, -40))
        self.assertEqual(view.verticalScrollBar().value(), v0 + 40)
        self.assertEqual(view.history, [])

        # Clicking the link without moving follows it.
        view.goto(0)
        view.horizontalScrollBar().setValue(0)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.link_center(view))
        self.assertEqual(len(view.history), 1)
        self.assertGreater(view.verticalScrollBar().value(), 200)

    def test_hold_space_drag_pans_temporarily_while_tap_space_still_scrolls(self):
        view = self.open_zoomed("space")
        self.bar.draw.trigger()
        self.assertTrue(view.drawing)

        # Holding Space (including synthetic auto-repeat events) does not jump a page.
        h0, v0 = view.horizontalScrollBar().value(), view.verticalScrollBar().value()
        QApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ", False))
        self.assertEqual(view.viewport().cursor().shape(), Qt.OpenHandCursor)
        QApplication.sendEvent(view, QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.NoModifier, " ", True))
        QApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ", True))
        self.assertEqual(view.verticalScrollBar().value(), v0)

        # Dragging while holding Space pans without drawing an ink stroke.
        self.drag(view, QPoint(300, 300), QPoint(255, 225))
        self.assertEqual(view.horizontalScrollBar().value(), h0 + 45)
        self.assertEqual(view.verticalScrollBar().value(), v0 + 75)
        self.assertEqual(view.doc.annotations(0), [])
        self.assertFalse(view.doc.modified)

        # Releasing Space after panning does not scroll an extra screen and keeps the previous tool.
        v_after_drag = view.verticalScrollBar().value()
        QApplication.sendEvent(view, QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.NoModifier, " ", False))
        self.assertEqual(view.verticalScrollBar().value(), v_after_drag)
        self.assertTrue(view.drawing)

        # Releasing Space mid-drag keeps panning cleanly until mouse release without drawing.
        h1, v1 = view.horizontalScrollBar().value(), view.verticalScrollBar().value()
        QApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ", False))
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(300, 300))
        QTest.mouseMove(view.viewport(), QPoint(280, 270))
        QApplication.sendEvent(view, QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.NoModifier, " ", False))
        QTest.mouseMove(view.viewport(), QPoint(260, 240))
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(260, 240))
        self.assertEqual(view.horizontalScrollBar().value(), h1 + 40)
        self.assertEqual(view.verticalScrollBar().value(), v1 + 60)
        self.assertEqual(view.doc.annotations(0), [])

        # A plain Space tap still scrolls one screen down, and Shift+Space scrolls one screen up.
        v2 = view.verticalScrollBar().value()
        QTest.keyClick(view, Qt.Key_Space)
        self.assertGreater(view.verticalScrollBar().value(), v2)
        v3 = view.verticalScrollBar().value()
        QTest.keyClick(view, Qt.Key_Space, Qt.ShiftModifier)
        self.assertLess(view.verticalScrollBar().value(), v3)

    def test_m_shortcut_stays_out_of_text_fields_and_appears_in_help(self):
        view = self.open_zoomed("help")
        pane = self.window.pane()
        pane.find_bar.open()
        QTest.keyClicks(pane.find_bar.field, "m")
        self.assertEqual(pane.find_bar.field.text(), "m")
        self.assertFalse(view.panning)
        pane.find_bar.close_bar()

        QTest.keyClick(view, Qt.Key_Question)
        dialog = self.window.findChild(QDialog, "shortcuts-help")
        self.assertIsNotNone(dialog)
        text = dialog.findChild(QTextBrowser).toPlainText()
        self.assertIn("Pan", text)
        self.assertIn("hold Space + drag or middle-drag to pan", text)
        dialog.close()


if __name__ == "__main__":
    unittest.main()
