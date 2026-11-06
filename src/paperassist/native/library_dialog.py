"""The library list: search, open in a tab or quick view, and import PDFs as a background job."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .jobs import JobWatcher
from .library_source import display_title

REVIEW = QColor("#a15c00")  # amber: not an error, a paper whose metadata nobody confirmed
NEEDS_REVIEW = ("needs_review", "incomplete")


class LibraryDialog(QDialog):
    open_requested = Signal(int)   # document id, to read in a tab
    quick_requested = Signal(int)  # document id, to consult in the quick view

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace, self.watcher = workspace, None
        self.setWindowTitle("Library")
        self.resize(920, 560)
        self.search = QLineEdit(placeholderText="Search title, author, year or DOI", clearButtonEnabled=True)
        self.search.setAccessibleName("Search the library")
        self.table = QTreeWidget(rootIsDecorated=False, uniformRowHeights=True, headerLabels=["Title", "Authors", "Year", "Metadata"])
        self.table.setAccessibleName("Papers")
        self.table.setColumnWidth(0, 460)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(2, 50)
        self.open_button = QPushButton("Open")
        self.quick_button = QPushButton("Quick view")
        self.import_button = QPushButton("Import PDFs…")
        self.cancel_button = QPushButton("Cancel import")
        self.progress = QProgressBar()
        self.message = QLabel(wordWrap=True)
        buttons = QHBoxLayout()
        for widget in (self.import_button, self.progress, self.cancel_button):
            buttons.addWidget(widget)
        buttons.addWidget(self.message, 1)
        for widget in (self.quick_button, self.open_button):
            buttons.addWidget(widget)
        column = QVBoxLayout(self)
        column.addWidget(self.search)
        column.addWidget(self.table, 1)
        column.addLayout(buttons)
        self.typing = QTimer(self, singleShot=True, interval=150, timeout=self.refresh)
        self.search.textChanged.connect(self.typing.start)
        self.table.itemActivated.connect(lambda: self._request(self.open_requested))
        self.table.itemSelectionChanged.connect(self._enable)
        self.open_button.clicked.connect(lambda: self._request(self.open_requested))
        self.quick_button.clicked.connect(lambda: self._request(self.quick_requested))
        self.import_button.clicked.connect(self.import_dialog)
        self.cancel_button.clicked.connect(lambda: self.watcher.cancel())
        self._importing(False)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.search.setFocus()

    # ---- list -----------------------------------------------------------------------------
    def refresh(self):
        selected = self.selected_id()
        documents = self.workspace.library.list_documents(self.search.text())
        self.table.clear()
        for document in documents:
            review = document["metadata_status"] in NEEDS_REVIEW
            item = QTreeWidgetItem([display_title(document), document["authors"] or "", str(document["year"] or ""),
                                    "Needs review" if review else ""])
            item.setData(0, Qt.UserRole, document["id"])
            item.setToolTip(0, document["filename"])
            if review:
                item.setForeground(3, REVIEW)
                item.setToolTip(3, "The title, authors or year could not be confirmed.")
            self.table.addTopLevelItem(item)
            if document["id"] == selected:
                self.table.setCurrentItem(item)
        if not documents and not self.search.text():
            self.message.setText("The library is empty. Import PDFs to start.")
        elif not documents:
            self.message.setText("No paper matches this search.")
        elif not self.watcher:
            self.message.setText(f"{len(documents)} papers")
        self._enable()

    def selected_id(self):
        item = self.table.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _enable(self):
        for button in (self.open_button, self.quick_button):
            button.setEnabled(self.selected_id() is not None)

    def _request(self, signal):
        if (document_id := self.selected_id()) is not None:
            signal.emit(document_id)
            self.hide()

    # ---- import ---------------------------------------------------------------------------
    def import_dialog(self):
        names, _ = QFileDialog.getOpenFileNames(self, "Import PDFs", "", "PDF (*.pdf)")
        if names:
            self.start_import(names)

    def start_import(self, names):
        try:
            job = self.workspace.import_paths(names)
        except Exception as error:  # too many jobs, or an invalid request
            self.message.setText(f"Could not start the import: {error}")
            return
        self.watcher = JobWatcher(self.workspace, job, self)
        self.watcher.progress.connect(self._progress)
        self.watcher.finished.connect(self._imported)
        self._importing(True)
        self.message.setText("Importing…")

    def _progress(self, job):
        done = job.get("progress") or {}
        self.progress.setMaximum(done.get("total") or 0)  # 0 keeps the bar busy until the first file is counted
        self.progress.setValue(done.get("completed") or 0)
        if done:
            self.message.setText(f"Importing {done['completed']} of {done['total']}…")

    def _imported(self, job):
        self.watcher.deleteLater()
        self.watcher = None
        self._importing(False)
        if job["status"] == "failed":
            text = f"Import failed: {job['error']}"
        elif "result" not in job:  # cancelled before the first file
            text = "Import cancelled"
        else:
            result = job["result"]
            text = (f"{result['imported']} added, {result['duplicates']} already in the library"
                    + (f", {len(result['errors'])} failed: " + "; ".join(f"{e['path']}: {e['error']}" for e in result["errors"])
                       if result["errors"] else "") + (" (cancelled)" if result["cancelled"] else ""))
        self.refresh()
        self.message.setText(text)

    def _importing(self, running):
        self.import_button.setEnabled(not running)
        self.progress.setVisible(running)
        self.cancel_button.setVisible(running)
