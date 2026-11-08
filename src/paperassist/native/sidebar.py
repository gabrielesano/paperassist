"""A collapsible side column: a single 40px header bar above the panel that belongs to it."""
from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from .theme import INK, icon


class ColumnTab(QToolButton):
    def minimumSizeHint(self):
        return self.sizeHint()


class DetailsPanel(QWidget):
    """References stay on the main paper while Comments follow the active pane."""

    def __init__(self, references, comments):
        super().__init__()
        self.mode = "references"
        self.stack = QStackedWidget()
        self.buttons = {}
        self.tab_bar = QWidget()
        row = QHBoxLayout(self.tab_bar)
        row.setContentsMargins(2, 0, 0, 0)
        row.setSpacing(2)
        for mode, title, panel in (("references", "References", references), ("comments", "Comments", comments)):
            button = ColumnTab(text=title, checkable=True, objectName="column-tab")
            button.clicked.connect(lambda _=False, mode=mode: self.show_mode(mode))
            self.buttons[mode] = button
            row.addWidget(button)
            self.stack.addWidget(panel)
        row.addStretch(1)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.stack, 1)
        self.show_mode("references")

    def show_mode(self, mode):
        self.mode = mode
        self.stack.setCurrentIndex(0 if mode == "references" else 1)
        for name, button in self.buttons.items():
            button.setChecked(name == mode)


class SideColumn(QWidget):
    hide_requested = Signal()

    def __init__(self, title, body, header_widget=None):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)
        button = QToolButton(toolTip=f"Hide {title}")
        button.setIcon(icon("minus", INK, 14))
        button.setAccessibleName(f"Hide {title}")
        button.clicked.connect(self.hide_requested)
        header_box = self.header_box = QWidget(objectName="column-header")
        header_box.setAttribute(Qt.WA_StyledBackground, True)
        header = QHBoxLayout(header_box)
        header.setContentsMargins(10 if header_widget is None else 4, 3, 6, 3)
        header.setSpacing(4)
        if header_widget is not None:
            header.addWidget(header_widget, 1)
        else:
            label = QLabel(title.upper(), objectName="column-title")
            header.addWidget(label, 1)
        header.addWidget(button)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(header_box)
        column.addWidget(body, 1)
        QTimer.singleShot(0, self._fit_header)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            QTimer.singleShot(0, self._fit_header)

    def _fit_header(self):
        self.setMinimumWidth(max(160, self.header_box.minimumSizeHint().width()))

    def minimumSizeHint(self):
        # Follow the body's controls (wide fonts, expanded cards) but never shrink below a usable column.
        hint = super().minimumSizeHint()
        return QSize(max(160, hint.width()), hint.height())
