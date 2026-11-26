"""Reachable overflow actions, page navigation field, and cached zoom width for native toolbars."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QProxyStyle,
                               QSizePolicy, QStyle, QToolButton, QWidget, QWidgetAction)

from .positions import current_page
from .theme import theme_generation


def go_to_page(view, page):
    """Zero-based page; numbers outside the document land on the first or last page."""
    if view:
        view.goto(max(0, min(page, len(view.doc) - 1)))


def step_page(view, step):
    if view:
        go_to_page(view, current_page(view) + step)


class PageField(QWidget):
    """Current page and total for one document, grouped with page step buttons."""

    def __init__(self, prefix="Main paper"):
        super().__init__()
        self.view, self.prefix = None, prefix
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.toolbar_actions = []
        self.edit = QLineEdit(alignment=Qt.AlignCenter, toolTip="Go to page (G)")
        self.edit.setAccessibleName(f"{prefix}: page number")
        self.edit.setValidator(QIntValidator(1, 99999))
        self.edit.setMinimumWidth(46)
        self.edit.setMaximumWidth(62)
        self.total = QLabel(objectName="muted-note")
        self._total_font_key = None
        self._fit_total_width()
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(5)
        row.addWidget(self.edit)
        row.addWidget(self.total)
        self.edit.returnPressed.connect(self.commit)
        self.edit.installEventFilter(self)

    def _fit_total_width(self):
        key = (self.total.font().toString(), theme_generation())
        if key != self._total_font_key:
            self._total_font_key = key
            self.total.setFixedWidth(self.total.fontMetrics().horizontalAdvance('/ 99999') + 4)

    def eventFilter(self, watched, event):
        if watched is self.edit and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.leave()
            return True
        return super().eventFilter(watched, event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit_total_width()

    def show_page(self, view):
        self.view = view
        self._fit_total_width()
        shown = view is not None
        for action in self.toolbar_actions:
            action.setVisible(shown)
        self.setVisible(shown)
        self.setEnabled(shown)
        if not shown:
            self.edit.clear()
            self.total.clear()
            return
        total_text = f"/ {len(view.doc)}"
        if self.total.text() != total_text:
            self.total.setText(total_text)
        if not self.edit.hasFocus():  # never rewrite a number the user is typing
            page_text = str(current_page(view) + 1)
            if self.edit.text() != page_text:
                self.edit.setText(page_text)

    def focus(self):
        if self.view:
            self.edit.setFocus()
            self.edit.selectAll()

    def commit(self):
        if self.view and self.edit.text():
            go_to_page(self.view, int(self.edit.text()) - 1)
        self.leave()

    def leave(self):
        """Back to the document; the field shows the real page again."""
        view = self.view
        if view:
            view.setFocus()
        self.show_page(view)

    def add_overflow_actions(self, menu):
        submenu = menu.addMenu(f'{self.prefix}: page number')
        field = PageField(self.prefix)
        field.show_page(self.view)
        action = QWidgetAction(submenu)
        action.setDefaultWidget(field)
        submenu.addAction(action)
        return submenu


class _ToolbarStyle(QProxyStyle):
    def pixelMetric(self, metric, option=None, widget=None):
        value = super().pixelMetric(metric, option, widget)
        return max(value, 24) if metric == QStyle.PM_ToolBarExtensionExtent else value


def reserve_zoom_width(action, button, text):
    if action.text() != text:
        action.setText(text)
    metrics = button.fontMetrics()
    window = button.window()
    key = (button.font().toString(), metrics.horizontalAdvance('Fit width'), metrics.height(),
           theme_generation(), len(window.styleSheet()) if window else 0)
    if getattr(button, "_zoom_width_key", None) == key:
        return
    widths = []
    for label in ('Fit width', '400%'):
        button.setText(label)
        widths.append(button.sizeHint().width())
    button.setText(text)
    button.setFixedWidth(max(widths))
    button._zoom_width_key = key


class _Overflow(QObject):
    def __init__(self, toolbar):
        super().__init__(toolbar)
        self.toolbar = toolbar
        # Qt's toolbar layout reserves the style metric, regardless of CSS minimum widths.
        self.style = style = _ToolbarStyle(QApplication.style().name())
        # Widget actions can consult the style while the toolbar releases their widgets.
        style.setParent(QApplication.instance())
        toolbar.destroyed.connect(style.deleteLater)
        toolbar.setStyle(style)
        self.button = toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
        self.button.setStyle(style)
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
