"""Shared dialog chrome and table-picker helper."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit, QTreeWidget, QVBoxLayout)

from .theme import serif_heading_font


def setup_dialog(dialog, title, *, heading=None, subtitle=None, spacing=10, margins=(22, 20, 22, 18)):
    """Configure standard window title, margins, serif heading, and optional muted subtitle."""
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
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
