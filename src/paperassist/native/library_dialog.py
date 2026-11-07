"""The library list: search, open in a tab or quick view, and import PDFs as a background job."""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from paperassist.library import pdf_files
from .jobs import JobWatcher
from .library_source import display_title
from .metadata_dialog import MetadataDialog

REVIEW = QColor("#a15c00")  # amber: not an error, a paper whose metadata nobody confirmed
NEEDS_REVIEW = ("needs_review", "incomplete")


def size_text(size):
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


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
        self.table.setColumnWidth(0, 420)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(2, 70)
        self.open_button = QPushButton("Open")
        self.quick_button = QPushButton("Quick view")
        self.edit_button = QPushButton("Edit details…")
        self.edit_button.setToolTip("Correct the title, authors, year or DOI (F2)")
        self.import_button = QPushButton("Import PDFs…")
        self.folder_button = QPushButton("Import folder…")
        self.cancel_button = QPushButton("Cancel import")
        self.progress = QProgressBar()
        self.hint = QLabel("The library is empty.\nChoose Import PDFs or Import folder to add papers.", alignment=Qt.AlignCenter)
        self.hint.setAccessibleName("Empty library")
        self.estimate = QLabel(wordWrap=True)  # what an import would add, shown before anything is copied
        self.estimate.setAccessibleName("Import estimate")
        self.start_button = QPushButton("Start import")
        self.discard_button = QPushButton("Cancel selection")
        self.message = QLabel(wordWrap=True)
        import_row = QHBoxLayout()
        import_row.addWidget(self.progress, 1)
        import_row.addWidget(self.cancel_button)
        buttons = QHBoxLayout()
        for widget in (self.import_button, self.folder_button):
            buttons.addWidget(widget)
        buttons.addWidget(self.message, 1)
        for widget in (self.edit_button, self.quick_button, self.open_button):
            buttons.addWidget(widget)
        self.confirm_row = QHBoxLayout()
        for widget in (self.estimate, self.start_button, self.discard_button):
            self.confirm_row.addWidget(widget, 1 if widget is self.estimate else 0)
        column = QVBoxLayout(self)
        column.addWidget(self.search)
        column.addWidget(self.table, 1)
        column.addWidget(self.hint, 1)
        column.addLayout(self.confirm_row)
        column.addLayout(import_row)
        column.addLayout(buttons)
        self.pending = []
        self.typing = QTimer(self, singleShot=True, interval=150, timeout=self.refresh)
        self.search.textChanged.connect(self.typing.start)
        self.table.itemActivated.connect(lambda: self._request(self.open_requested))
        self.table.itemSelectionChanged.connect(self._enable)
        self.open_button.clicked.connect(lambda: self._request(self.open_requested))
        self.quick_button.clicked.connect(lambda: self._request(self.quick_requested))
        self.edit_button.clicked.connect(self.edit_metadata)
        self.import_button.clicked.connect(self.import_dialog)
        self.folder_button.clicked.connect(self.import_folder_dialog)
        self.start_button.clicked.connect(self._confirm_import)
        self.discard_button.clicked.connect(self._discard_selection)
        self.cancel_button.clicked.connect(lambda: self.watcher.cancel())
        self._importing(False)
        self._discard_selection()
        self.refresh()

    def keyPressEvent(self, event):
        # A key event handler rather than a QShortcut: that only fires once the window counts as active.
        if event.key() == Qt.Key_F2:
            return self.edit_metadata()
        super().keyPressEvent(event)

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
        empty = not documents and not self.search.text()
        self.table.setVisible(not empty)
        self.hint.setVisible(empty)
        if empty:
            self.message.setText("")
        elif not documents:
            self.message.setText("No paper matches this search.")
        elif not self.watcher:
            self.message.setText(f"{len(documents)} papers")
        self._enable()

    def selected_id(self):
        item = self.table.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _enable(self):
        for button in (self.open_button, self.quick_button, self.edit_button):
            button.setEnabled(self.selected_id() is not None)

    def _request(self, signal):
        if (document_id := self.selected_id()) is not None:
            signal.emit(document_id)
            self.hide()

    # ---- metadata -------------------------------------------------------------------------
    def edit_metadata(self):
        document_id = self.selected_id()
        if document_id is None:
            return
        self.editor = MetadataDialog(self.workspace.library, self.workspace.library.get_document(document_id), self)
        self.editor.saved.connect(self._corrected)
        self.editor.open()

    def _corrected(self, document):
        """Bring every place that shows this paper up to date: the list, the tab titles and the references."""
        self.refresh()
        window = self.parent()
        if window is None or not hasattr(window, "all_panes"):  # shown on its own, e.g. in tests
            return
        for pane in window.all_panes():
            source = pane.view.doc.source
            if getattr(source, "document_id", None) == document["id"]:
                source.title = display_title(document)
        window.refresh()
        # Open tabs may cite the corrected paper, so drop cached resolutions before reloading the active one.
        window.details.library_changed()

    # ---- import ---------------------------------------------------------------------------
    def import_dialog(self):
        names, _ = QFileDialog.getOpenFileNames(self, "Import PDFs", "", "PDF (*.pdf)")
        if names:
            self.select_import(names)

    def import_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Import a folder of PDFs")
        if folder:
            self.select_import([folder])

    def select_import(self, names):
        """Show what the selection would add and wait for the user to confirm; non-PDF files are ignored."""
        errors = []
        files = pdf_files(names, errors)
        if not files:
            self._discard_selection()
            self.message.setText("Could not scan selection: " + "; ".join(f"{e['path']}: {e['error']}" for e in errors)
                                 if errors else "No PDF files in the selection.")
            return
        total = 0
        for path in files:
            try:
                total += path.stat().st_size
            except OSError as error:
                errors.append({"path": str(path), "error": str(error)})
        # Duplicates are not copied, so the total is an upper bound.
        self.estimate.setText(f"{len(files)} PDF{'s' if len(files) != 1 else ''}, {size_text(total)}. "
                              f"Importing copies them: up to {size_text(total)} of extra storage.")
        if errors:
            self.estimate.setText(f"Found {len(files)} PDF{'s' if len(files) != 1 else ''}. "
                                  "Total storage estimate unavailable because some paths could not be read. "
                                  "Importing copies readable PDFs.")
        self.pending = list(names)
        self.message.setText("Could not scan some paths: " + "; ".join(f"{e['path']}: {e['error']}" for e in errors)
                             if errors else "")
        self._show_estimate(True)
        self.start_button.setFocus()

    def _show_estimate(self, shown):
        for widget in (self.estimate, self.start_button, self.discard_button):
            widget.setVisible(shown)
        self.import_button.setEnabled(not shown and not self.watcher)
        self.folder_button.setEnabled(not shown and not self.watcher)

    def _confirm_import(self):
        names = self.pending
        self._discard_selection()
        self.start_import(names)

    def _discard_selection(self):
        self.pending = []
        self._show_estimate(False)

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
            review = sum(doc["metadata_status"] in NEEDS_REVIEW for doc in result["documents"])
            text = (f"{result['imported']} added, {result['duplicates']} already in the library, "
                    f"{review} need metadata review, {len(result['errors'])} failed"
                    + (": " + "; ".join(f"{e['path']}: {e['error']}" for e in result["errors"])
                       if result["errors"] else "") + (" (cancelled)" if result["cancelled"] else ""))
        self.refresh()
        self.message.setText(text)
        window = self.parent()
        if job.get("result", {}).get("imported") and hasattr(window, "details"):
            window.details.library_changed()

    def _importing(self, running):
        self.import_button.setEnabled(not running)
        self.folder_button.setEnabled(not running)
        self.progress.setVisible(running)
        self.cancel_button.setVisible(running)
