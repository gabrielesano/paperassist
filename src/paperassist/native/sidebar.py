"""A collapsible side column: a header with a "−" button above the panel that belongs to it."""
from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QToolButton, QVBoxLayout, QWidget


class DetailsPanel(QWidget):
    """References stay on the main paper while Comments follow the active pane."""
    def __init__(self, references, comments):
        super().__init__()
        self.mode = "references"
        self.stack = QStackedWidget()
        self.buttons = {}
        row = QHBoxLayout()
        row.setContentsMargins(6, 2, 6, 2)
        for mode, title, panel in (("references", "References", references), ("comments", "Comments", comments)):
            button = QToolButton(text=title, checkable=True)
            button.clicked.connect(lambda _=False, mode=mode: self.show_mode(mode))
            self.buttons[mode] = button
            row.addWidget(button)
            self.stack.addWidget(panel)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addLayout(row)
        column.addWidget(self.stack)
        self.show_mode("references")

    def show_mode(self, mode):
        self.mode = mode
        self.stack.setCurrentIndex(0 if mode == "references" else 1)
        for name, button in self.buttons.items():
            button.setChecked(name == mode)


class SideColumn(QWidget):
    hide_requested = Signal()

    def __init__(self, title, body):
        super().__init__()
        button = QToolButton(text="−", toolTip=f"Hide {title}")
        button.setAccessibleName(f"Hide {title}")
        button.clicked.connect(self.hide_requested)
        header = QHBoxLayout()
        header.setContentsMargins(8, 4, 4, 4)
        header.addWidget(QLabel(f"<b>{title}</b>"), 1)
        header.addWidget(button)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addLayout(header)
        column.addWidget(body, 1)

    def minimumSizeHint(self):
        # Follow the body's controls (wide fonts, expanded cards) but never shrink below a usable column.
        hint = super().minimumSizeHint()
        return QSize(max(160, hint.width()), hint.height())
