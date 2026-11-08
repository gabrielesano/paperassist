"""Reachable overflow actions for native toolbars."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QMenu, QToolButton


def reserve_zoom_width(action, button, text):
    action.setText(text)
    widths = []
    for label in ('Fit width', '400%'):
        button.setText(label)
        widths.append(button.sizeHint().width())
    button.setText(text)
    button.setFixedWidth(max(widths))


class _Overflow(QObject):
    def __init__(self, toolbar):
        super().__init__(toolbar)
        self.toolbar = toolbar
        self.button = toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
        self.button.setFocusPolicy(Qt.StrongFocus)
        self.button.setAccessibleName(f"More {toolbar.windowTitle().lower()} actions")
        self.menu = QMenu(toolbar)
        self.generated_menus = []
        self.button.installEventFilter(self)

    def eventFilter(self, watched, event):
        mouse = event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton
        key = event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space, Qt.Key_Down)
        if mouse or key:
            self.popup()
            if key:
                first = next((a for a in self.menu.actions() if a.isEnabled() and not a.isSeparator()), None)
                self.menu.setActiveAction(first)
            return True
        return event.type() == QEvent.MouseButtonRelease

    def is_hidden(self, widget):
        return not widget.isVisible() or widget.geometry().right() >= self.button.geometry().left()

    def popup(self):
        for action in self.menu.actions():
            self.menu.removeAction(action)
        for submenu in self.generated_menus:
            submenu.deleteLater()
        self.generated_menus.clear()
        for action in self.toolbar.actions():
            widget = self.toolbar.widgetForAction(action)
            if action.isVisible() and widget is not None and self.is_hidden(widget):
                if hasattr(widget, 'add_overflow_actions'):
                    self.generated_menus.append(widget.add_overflow_actions(self.menu))
                else:
                    self.menu.addAction(action)
        self.menu.popup(self.button.mapToGlobal(self.button.rect().bottomLeft()))


def install_overflow(toolbar):
    # Qt recomputes its extension menu on layout changes; keep our keyboard path independent.
    toolbar.overflow = _Overflow(toolbar)
    return toolbar.overflow
