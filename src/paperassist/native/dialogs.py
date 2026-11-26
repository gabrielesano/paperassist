"""Shared dialog chrome, single-line elided labels, and table-picker helper."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLayout, QLineEdit,
                               QSizePolicy, QTreeWidget, QVBoxLayout)

from .theme import serif_heading_font, style_resize_grip


class ElidedLabel(QLabel):
    """Single-line label that elides long text cleanly without stretching or wrapping layouts."""

    def __init__(self, text="", parent=None, **kwargs):
        super().__init__(text, parent, **kwargs)
        self._explicit_tip = kwargs.get("toolTip", "")
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if not self._explicit_tip:
            super().setToolTip(text)

    def setText(self, text):
        super().setText(text)
        if not self._explicit_tip:
            super().setToolTip(text)

    def setToolTip(self, tip):
        self._explicit_tip = tip or ""
        super().setToolTip(self._explicit_tip or self.text())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(min(hint.width(), 360), hint.height())

    def paintEvent(self, _event):
        painter = QPainter(self)
        rect = self.contentsRect()
        elided = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(0, rect.width()))
        self.style().drawItemText(
            painter,
            rect,
            int(self.alignment()) | int(Qt.TextSingleLine),
            self.palette(),
            self.isEnabled(),
            elided,
            QPalette.WindowText,
        )


def setup_dialog(dialog, title, *, heading=None, subtitle=None, spacing=10, margins=(22, 20, 22, 18),
                 resizable=True):
    """Configure shared dialog chrome and headings."""
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    if resizable:
        dialog.setSizeGripEnabled(True)
        style_resize_grip(dialog)
    else:
        layout.setSizeConstraint(QLayout.SetFixedSize)
    heading_label = QLabel(heading or title)
    heading_label.setProperty("headingScale", "dialog")
    heading_label.setFont(serif_heading_font(dialog.font()))
    layout.addWidget(heading_label)
    if subtitle:
        layout.addWidget(QLabel(subtitle, objectName="muted-note", wordWrap=True))
    return layout, heading_label


class PickerDialog(QDialog):
    """Reusable table-selection dialog with optional search and primary confirm button."""

    def __init__(self, parent, title, *, heading=None, subtitle="", headers, table_name,
                 confirm_text="OK", size=(640, 360), searchable=False, search_placeholder="",
                 search_name="", on_choose=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(*size)
        column, self.heading = setup_dialog(self, title, heading=heading, subtitle=subtitle)
        self.search = None
        if searchable:
            self.search = QLineEdit(placeholderText=search_placeholder, clearButtonEnabled=True)
            self.search.setAccessibleName(search_name or "Search")
            column.addWidget(self.search)
        self.table = QTreeWidget(objectName="dialog-tree", headerLabels=list(headers), rootIsDecorated=False)
        self.table.setAccessibleName(table_name)
        column.addWidget(self.table, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText(confirm_text)
        self.ok_button.setProperty("primary", True)
        self.ok_button.setEnabled(False)
        self.table.itemSelectionChanged.connect(lambda: self.ok_button.setEnabled(bool(self.table.selectedItems())))

        def accept():
            if selected := self.table.selectedItems():
                if on_choose is not None:
                    on_choose(selected[0].data(0, Qt.UserRole))
                self.accept()

        self.buttons.accepted.connect(accept)
        self.buttons.rejected.connect(self.reject)
        column.addWidget(self.buttons)
