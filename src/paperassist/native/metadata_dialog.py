"""Correct the title, authors, year and DOI of one paper; a rejected value is explained and kept for editing."""
import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from .theme import APP_STYLE, ERROR_INK, serif_heading_font

FIELDS = (("title", "Title"), ("authors", "Authors"), ("year", "Year"), ("doi", "DOI"))


class MetadataDialog(QDialog):
    saved = Signal(dict)  # the document as stored, after the correction

    def __init__(self, library, document, parent=None):
        super().__init__(parent)
        if parent is None:
            self.setStyleSheet(APP_STYLE)
        self.library, self.document_id = library, document["id"]
        self.setWindowTitle("Edit paper details")
        self.setMinimumWidth(520)
        heading = QLabel("Edit paper details")
        heading.setProperty("headingScale", "dialog")
        heading.setFont(serif_heading_font(self.font()))
        form = QFormLayout()
        form.setSpacing(8)
        self.fields = {}
        for key, label in FIELDS:
            field = QLineEdit(str(document.get(key) or ""))
            field.setAccessibleName(label)
            form.addRow(f"&{label}", field)
            self.fields[key] = field
        # Shown in place, so the typed values stay on screen while the user fixes them.
        self.error = QLabel(wordWrap=True, styleSheet=f"color: {ERROR_INK};")
        self.error.setAccessibleName("Validation message")
        self.error.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        if save_btn := buttons.button(QDialogButtonBox.Save):
            save_btn.setProperty("primary", True)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 18)
        column.setSpacing(10)
        column.addWidget(heading)
        column.addLayout(form)
        column.addWidget(self.error)
        column.addWidget(buttons)
        self.fields["title"].setFocus()

    def save(self):
        values = {key: field.text().strip() for key, field in self.fields.items()}
        try:
            if values["year"] and not re.fullmatch(r"\d{1,4}", values["year"]):
                raise ValueError("Year must be a number such as 2015")
            document = self.library.update_metadata(self.document_id, values)
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.saved.emit(document)
        self.accept()
