"""Mouse-wheel gliding in the native PDF reader."""
import unittest

from support import QtTestCase, make_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QAbstractAnimation, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from paperassist.native.source import FileSource
    from paperassist.native.view import WHEEL_LINE_PX
    from paperassist.native.window import MainWindow


@needs_qt
class WheelGlideTests(QtTestCase):
    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.client)
        self.window.resize(1200, 800)
        self.window.show()
        self.addCleanup(lambda: self.cleanup_window(self.window))
        QTest.qWaitForWindowActive(self.window)
        path = make_pdf(self.dir / f"{self.id()}.pdf", pages=6)
        self.view = self.window.open_source(FileSource(path)).main.view
        self.pump(lambda: self.view.has_page(0))
        self.bar = self.view.verticalScrollBar()
        self.assertGreater(self.bar.maximum(), 1000)
        self.bar.setValue(500)
        self.distance = 3 * WHEEL_LINE_PX  # the offscreen platform keeps the default of 3 lines
        self.assertEqual(QApplication.wheelScrollLines(), 3)

    def wheel(self, delta=-120, modifiers=Qt.NoModifier, phase=Qt.NoScrollPhase, pixel=QPoint()):
        pos = QPointF(100, 100)
        event = QWheelEvent(pos, pos, pixel, QPoint(0, delta), Qt.NoButton, modifiers, phase, False)
        QApplication.sendEvent(self.view.viewport(), event)
        return event

    def settled(self):
        return self.view._glide.state() != QAbstractAnimation.Running

    def test_notch_glides_monotonically_to_its_distance(self):
        event = self.wheel()
        self.assertTrue(event.isAccepted())
        self.assertEqual(self.bar.value(), 500)  # no instant jump
        seen = []
        self.pump(lambda: seen.append(self.bar.value()) or self.settled())
        self.assertEqual(self.bar.value(), 500 + self.distance)
        self.assertEqual(seen, sorted(seen))
        self.assertGreater(len(set(seen)), 2)

    def test_up_notch_scrolls_up(self):
        self.wheel(120)
        self.pump(self.settled)
        self.assertEqual(self.bar.value(), 500 - self.distance)

    def test_quick_notches_accumulate(self):
        for _ in range(4):
            self.wheel()
            self.pump(seconds=0.02)
        self.pump(self.settled)
        self.assertEqual(self.bar.value(), 500 + 4 * self.distance)

    def test_fractional_notches_accumulate(self):
        for _ in range(8):
            self.wheel(-15)
            self.pump(seconds=0.01)
        self.pump(self.settled)
        self.assertEqual(self.bar.value(), 500 + self.distance)

    def test_reversed_notch_moves_back_up(self):
        self.wheel()
        self.pump(lambda: self.bar.value() > 520)
        mid = self.bar.value()
        self.wheel(120)
        self.pump(self.settled)
        self.assertEqual(self.bar.value(), mid - self.distance)

    def test_target_is_clamped_to_the_document(self):
        self.bar.setValue(self.bar.maximum() - 10)
        self.wheel()
        self.pump(self.settled)
        self.assertEqual(self.bar.value(), self.bar.maximum())
        self.bar.setValue(5)
        self.wheel(120)
        self.pump(self.settled)
        self.assertEqual(self.bar.value(), 0)

    def test_ctrl_wheel_zooms_without_gliding(self):
        zoom = self.view.zoom
        self.wheel(120, Qt.ControlModifier)
        self.assertGreater(self.view.zoom, zoom)
        self.assertTrue(self.settled())

    def test_touchpad_events_keep_default_scrolling(self):
        self.wheel(-120, phase=Qt.ScrollUpdate, pixel=QPoint(0, -40))
        self.assertTrue(self.settled())
        self.assertGreater(self.bar.value(), 500)  # moved at once, by Qt, not by the glide

    def test_shift_wheel_is_not_glided(self):
        self.wheel(-120, Qt.ShiftModifier)
        self.assertTrue(self.settled())

    def test_other_movement_cancels_the_glide(self):
        jumps = {
            "scrollbar": lambda: self.bar.setValue(900),
            "goto": lambda: self.view.goto(3),
            "key": lambda: QTest.keyClick(self.view, Qt.Key_PageDown),
        }
        for name, jump in jumps.items():
            with self.subTest(name):
                self.bar.setValue(500)
                self.wheel()
                self.pump(lambda: self.bar.value() > 500)
                jump()
                landed = self.bar.value()
                self.pump(seconds=0.3)
                self.assertTrue(self.settled())
                self.assertEqual(self.bar.value(), landed)

    def test_hiding_the_view_stops_the_glide(self):
        self.wheel()
        self.assertFalse(self.settled())
        self.view.hide()
        self.assertTrue(self.settled())


if __name__ == "__main__":
    unittest.main()
