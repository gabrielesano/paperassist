"""A collapsible side column: a header with a "−" button above the panel that belongs to it."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget


class SideColumn(QWidget):
    hide_requested = Signal()

    def __init__(self, title, body):
        super().__init__()
        self.setMinimumWidth(160)
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
