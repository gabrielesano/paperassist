"""Preferences dialog: interface chrome size, shared with the web reader via settings.preferences()."""
from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from paperassist import settings

ZOOM_CHOICES = (0.8, 0.9, 1, 1.1, 1.25, 1.5)
STYLE_START = "\n/* paperassist-ui-zoom */\n"
STYLE_END = "\n/* /paperassist-ui-zoom */"


def parse_zoom(stored):
    """Accept only the six validated zoom factors so stale config values fall back to 100%."""
    return next((c for c in ZOOM_CHOICES if type(stored) in (int, float) and stored == c), 1)


def _strip_zoom_style(style):
    start = style.find(STYLE_START)
    if start < 0:
        return style
    end = style.find(STYLE_END, start)
    return style[:start] + (style[end + len(STYLE_END):] if end >= 0 else "")


def _zoom_rules(font, zoom):
    size = (f"{font.pointSizeF() * zoom:g}pt" if font.pointSizeF() > 0
            else f"{max(1, round((font.pixelSize() or 13) * zoom))}px")
    return f"""{STYLE_START}QToolBar, QToolBar QWidget, QTabBar, QMenu, QStatusBar, QStatusBar QWidget,
SideColumn, SideColumn QWidget, FindBar, FindBar QWidget, QDialog, QDialog QWidget {{ font-size: {size}; }}
QToolBar {{ spacing: {round(2 * zoom)}px; }}
QToolButton {{ padding: {round(3 * zoom)}px {round(8 * zoom)}px; border-radius: {round(4 * zoom)}px; }}
QTabBar::tab {{ padding: {round(5 * zoom)}px {round(12 * zoom)}px; }}{STYLE_END}"""


class PreferencesDialog(QDialog):
    def __init__(self, host):
        super().__init__(host, objectName="preferences-dialog", windowTitle="Preferences")
        self.host, self.zoom_value, self.base_font = host, 1, QFont(host.font())
        self.setStyleSheet("#preferences-dialog { background: #fbfcf7; border: 1px solid #cfcac1; }")

        label = QLabel("Interface size")
        self.size_combo = QComboBox(objectName="preferences-zoom", accessibleName="Interface size")
        for value in ZOOM_CHOICES:
            self.size_combo.addItem(f"{round(value * 100)}%", value)
        label.setBuddy(self.size_combo)

        help_text = ("Applies at once to the toolbar, panels and dialogs on this computer. "
                     "Documents keep their own zoom.")
        self.message = QLabel(objectName="preferences-message", wordWrap=True, visible=False)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for widget in (QLabel("<b>Preferences</b>"), label, self.size_combo,
                       QLabel(help_text, wordWrap=True), self.message, buttons):
            layout.addWidget(widget)

        self.size_combo.currentIndexChanged.connect(self._on_size_changed)
        self.finished.connect(lambda _: host.activateWindow())

    def apply_zoom(self, value):
        zoom = parse_zoom(value)
        self.zoom_value = zoom
        with QSignalBlocker(self.size_combo):
            self.size_combo.setCurrentIndex(ZOOM_CHOICES.index(zoom))
        # Replace only our delimited block so stylesheet changes made after install survive.
        base = _strip_zoom_style(self.host.styleSheet())
        self.host.setStyleSheet(base if zoom == 1 else base + _zoom_rules(self.base_font, zoom))
        if self.isVisible():
            self.adjustSize()

    def _on_size_changed(self, _index):
        value = parse_zoom(self.size_combo.currentData())
        self.apply_zoom(value)
        try:
            settings.update_preferences({"ui_zoom": value})
            self.message.hide()
        except (OSError, ValueError) as error:
            self.message.setText(f"Could not save interface size: {error}")
            self.message.show()
            self.adjustSize()

    def open_preferences(self, *_):
        stored = parse_zoom(settings.preferences().get("ui_zoom"))
        if stored != self.zoom_value:
            self.apply_zoom(stored)
        self.message.hide()
        self.adjustSize()
        self.open()
        self.activateWindow()
        self.size_combo.setFocus()


def install(window):
    if existing := getattr(window, "preferences_dialog", None):
        return existing
    dialog = PreferencesDialog(window)
    window.preferences_dialog = dialog
    window.show_preferences = dialog.open_preferences
    dialog.apply_zoom(settings.preferences().get("ui_zoom"))
    return dialog
