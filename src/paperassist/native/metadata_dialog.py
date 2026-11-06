"""Correct the title, authors, year and DOI of one paper; a rejected value is explained and kept for editing."""
import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

FIELDS = (("title", "Title"), ("authors", "Authors"), ("year", "Year"), ("doi", "DOI"))


class MetadataDialog(QDialog):
    saved = Signal(dict)  # the document as stored, after the correction

    def __init__(self, library, document, parent=None):
        super().__init__(parent)
        self.library, self.document_id = library, document["id"]
        self.setWindowTitle("Edit paper details")
        self.setMinimumWidth(520)
        form = QFormLayout()
        self.fields = {}
        for key, label in FIELDS:
            field = QLineEdit(str(document.get(key) or ""))
            field.setAccessibleName(label)
            form.addRow(f"&{label}", field)
            self.fields[key] = field
        # Shown in place, so the typed values stay on screen while the user fixes them.
        self.error = QLabel(wordWrap=True, styleSheet="color: #b3261e")
        self.error.setAccessibleName("Validation message")
        self.error.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        column = QVBoxLayout(self)
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
