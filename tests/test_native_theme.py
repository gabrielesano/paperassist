"""Accessibility and zoom behavior of the native theme."""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from paperassist.native.interface_zoom import window_style
from paperassist.native.theme import APP_STYLE, DIALOG_BG, MUTED, PAPER, SURFACE, TEAL_SOFT, WELCOME_BG, serif_heading_font


app = QApplication.instance() or QApplication([])


def contrast(foreground, background):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    dark, light = sorted((luminance(foreground), luminance(background)))
    return (light + 0.05) / (dark + 0.05)


class NativeThemeTest(unittest.TestCase):
    def test_muted_text_meets_aa_on_theme_surfaces(self):
        for background in (PAPER, SURFACE, WELCOME_BG, DIALOG_BG, TEAL_SOFT):
            with self.subTest(background=background):
                self.assertGreaterEqual(contrast(MUTED, background), 4.5)

    def test_heading_remains_larger_through_zoom_and_reset(self):
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        heading = QLabel('Heading')
        heading.setProperty('headingScale', 'dialog')
        heading.setFont(serif_heading_font(dialog.font()))
        body = QLabel('Body')
        layout.addWidget(heading)
        layout.addWidget(body)
        base_font = dialog.font()
        dialog.setStyleSheet(APP_STYLE)
        dialog.show()
        app.processEvents()
        original = heading.font().pointSizeF()
        dialog.setStyleSheet(window_style(APP_STYLE, base_font, 1.5))
        app.processEvents()
        self.assertAlmostEqual(heading.font().pointSizeF(), base_font.pointSizeF() * 1.5 * 1.55, places=2)
        self.assertGreater(heading.font().pointSizeF(), body.font().pointSizeF())
        dialog.setStyleSheet(window_style(dialog.styleSheet(), base_font, 1))
        app.processEvents()
        self.assertAlmostEqual(heading.font().pointSizeF(), original, places=2)
        dialog.close()

    def test_welcome_heading_remains_large_at_150_percent(self):
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        welcome = QWidget(objectName="welcome-screen")
        inner = QVBoxLayout(welcome)
        heading = QLabel('Welcome')
        heading.setProperty('headingScale', 'welcome')
        heading.setFont(serif_heading_font(dialog.font(), 2.05))
        inner.addWidget(heading)
        layout.addWidget(welcome)
        base_font = dialog.font()
        dialog.setStyleSheet(window_style(APP_STYLE, base_font, 1.5))
        dialog.show()
        app.processEvents()
        self.assertAlmostEqual(heading.font().pointSizeF(), base_font.pointSizeF() * 1.5 * 2.05, places=2)
        dialog.close()

    def test_keyboard_focus_changes_button_rendering_without_clearing_checked_state(self):
        dialog = QDialog()
        dialog.setStyleSheet(APP_STYLE)
        layout = QVBoxLayout(dialog)
        checked = QToolButton(text='Checked', checkable=True, checked=True)
        link = QPushButton('Link')
        link.setProperty('link', True)
        primary = QPushButton('Primary')
        primary.setProperty('primary', True)
        layout.addWidget(checked)
        layout.addWidget(link)
        layout.addWidget(primary)
        dialog.show()
        dialog.activateWindow()
        app.processEvents()
        for button, other in ((checked, link), (link, checked), (primary, checked)):
            other.setFocus(Qt.TabFocusReason)
            app.processEvents()
            before = button.grab().toImage()
            button.setFocus(Qt.TabFocusReason)
            app.processEvents()
            after = button.grab().toImage()
            self.assertTrue(button.hasFocus())
            self.assertNotEqual(before, after)
        self.assertTrue(checked.isChecked())
        dialog.close()


if __name__ == '__main__':
    unittest.main()
