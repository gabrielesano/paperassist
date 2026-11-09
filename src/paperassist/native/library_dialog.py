"""The library list: search, open in a tab or quick view, and import PDFs as a background job."""
from PySide6.QtCore import QEvent, QItemSelectionModel, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from paperassist.library import manual_fields, pdf_files

from .jobs import JobWatcher
from .library_source import display_title
from .metadata_dialog import MetadataDialog
from .theme import APP_STYLE, serif_heading_font

TITLE, TAB_NAME, AUTHORS, YEAR, METADATA = range(5)
REVIEW = QColor("#a15c00")  # amber: not an error, a paper whose metadata nobody confirmed
NEEDS_REVIEW = ("needs_review", "incomplete")
OCR_TIP = ("OCR runs locally and can take up to about a minute per paper. It never modifies the PDF, "
           "and its results stay provisional until you review them.")


def size_text(size):
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


class PaperItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        left, right = self.text(column), other.text(column)
        if column == YEAR:
            if bool(left) != bool(right):
                ascending = self.treeWidget().header().sortIndicatorOrder() == Qt.AscendingOrder
                return bool(left) if ascending else bool(right)
            left, right = int(left or 0), int(right or 0)
        else:
            left, right = left.casefold(), right.casefold()
        return (left, self.data(0, Qt.UserRole)) < (right, other.data(0, Qt.UserRole))


class LibraryDialog(QDialog):
    open_requested = Signal(int)   # document id, to read in a tab
    quick_requested = Signal(int)  # document id, to consult in the quick view

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace, self.watcher = workspace, None
        self.setWindowTitle("Library")
        self.resize(940, 580)
        self.heading = QLabel("Library")
        self.heading.setProperty("headingScale", "dialog")
        self.heading.setFont(serif_heading_font(self.font()))
        self.search = QLineEdit(placeholderText="Search title, tab name, author, year or DOI", clearButtonEnabled=True)
        self.search.setAccessibleName("Search the library")
        self.table = QTreeWidget(objectName="dialog-tree", rootIsDecorated=False, uniformRowHeights=True,
                                 headerLabels=["Title", "Tab name", "Authors", "Year", "Metadata"])
        self.table.setAccessibleName("Papers")
        self.table.installEventFilter(self)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.AscendingOrder)
        self.table.header().setToolTip("Click a column to sort; click again to reverse the order.")
        for column, width in ((TITLE, 360), (TAB_NAME, 130), (AUTHORS, 200), (YEAR, 56)):
            self.table.setColumnWidth(column, width)
        self.open_button = QPushButton("Open")
        self.open_button.setProperty("primary", True)
        self.quick_button = QPushButton("Quick view")
        self.edit_button = QPushButton("Edit details…")
        self.edit_button.setToolTip("Correct the title, authors, year or DOI (F2)")
        self.selection_hint = QLabel("Select papers with Ctrl-click or Shift-click.", objectName="muted-note")
        self.refresh_button = QPushButton("Refresh metadata")
        self.refresh_button.setToolTip("Recover metadata locally from the PDF, with optional online verification. Fields you corrected are preserved.")
        menu = QMenu(self.refresh_button)
        menu.setToolTipsVisible(True)
        self.refresh_selected = menu.addAction("Refresh selected papers")
        self.refresh_review = menu.addAction("Refresh papers that need review")
        self.refresh_review.setToolTip("Skips papers that are verified or corrected by you, and ignores the current search. "
                                      "Use Refresh selected papers to recheck those.")
        self.recover_selected = menu.addAction("Recover selected papers with OCR")
        self.recover_selected.setToolTip(OCR_TIP + " Runs on every selected paper, including ones with a text layer.")
        menu.addSeparator()
        self.refresh_all = menu.addAction("Force refresh entire library")
        self.refresh_all.setToolTip("Also rechecks verified papers; online verification may update their values. "
                                    "Fields you corrected are always preserved.")
        menu.addSeparator()
        self.online_metadata = menu.addAction("Look up metadata online (arXiv, Crossref)")
        self.online_metadata.setCheckable(True)
        self.online_metadata.setChecked(True)
        self.ocr_metadata = menu.addAction("Read scanned PDFs with offline OCR")
        self.ocr_metadata.setCheckable(True)
        self.ocr_metadata.setChecked(True)
        self.ocr_metadata.setToolTip(OCR_TIP + " Used only for papers whose text cannot be read.")
        self.refresh_button.setMenu(menu)
        self.import_button = QPushButton("Import PDFs…")
        self.folder_button = QPushButton("Import folder…")
        self.cancel_button = QPushButton("Cancel import")
        self.progress = QProgressBar()
        self.hint = QLabel("The library is empty.\nChoose Import PDFs or Import folder to add papers.",
                           objectName="muted-note", alignment=Qt.AlignCenter)
        self.hint.setAccessibleName("Empty library")
        self.estimate = QLabel(objectName="muted-note", wordWrap=True)  # what an import would add, shown before anything is copied
        self.estimate.setAccessibleName("Import estimate")
        self.start_button = QPushButton("Start import")
        self.start_button.setProperty("primary", True)
        self.import_open_button = QPushButton("Import && open")
        self.import_open_button.setToolTip("Import this PDF and open it in a reader tab when ready.")
        self.discard_button = QPushButton("Cancel selection")
        self.message = QLabel(objectName="muted-note", wordWrap=True)

        import_row = QHBoxLayout()
        import_row.setSpacing(8)
        import_row.addWidget(self.progress, 1)
        import_row.addWidget(self.cancel_button)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for widget in (self.import_button, self.folder_button):
            buttons.addWidget(widget)
        buttons.addWidget(self.message, 1)
        for widget in (self.quick_button, self.open_button):
            buttons.addWidget(widget)

        metadata_row = QHBoxLayout()
        metadata_row.addWidget(self.selection_hint, 1)
        metadata_row.addWidget(self.edit_button)
        metadata_row.addWidget(self.refresh_button)

        self.confirm_row = QHBoxLayout()
        self.confirm_row.setSpacing(8)
        for widget in (self.estimate, self.start_button, self.import_open_button, self.discard_button):
            self.confirm_row.addWidget(widget, 1 if widget is self.estimate else 0)

        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 18)
        column.setSpacing(10)
        column.addWidget(self.heading)
        column.addWidget(self.search)
        column.addLayout(metadata_row)
        column.addWidget(self.table, 1)
        column.addWidget(self.hint, 1)
        column.addLayout(self.confirm_row)
        column.addLayout(import_row)
        column.addLayout(buttons)

        self.pending = []
        self.library_count = 0
        self.review_ids = []
        self.single_import = self.open_after_import = False
        self.job_kind = "import"
        self.typing = QTimer(self, singleShot=True, interval=150, timeout=self.refresh)
        self.search.textChanged.connect(self.typing.start)
        self.table.itemActivated.connect(lambda: self._request(self.open_requested))
        self.table.itemSelectionChanged.connect(self._enable)
        self.open_button.clicked.connect(lambda: self._request(self.open_requested))
        self.quick_button.clicked.connect(lambda: self._request(self.quick_requested))
        self.edit_button.clicked.connect(self.edit_metadata)
        self.import_button.clicked.connect(self.import_dialog)
        self.folder_button.clicked.connect(self.import_folder_dialog)
        self.start_button.clicked.connect(lambda: self._confirm_import())
        self.import_open_button.clicked.connect(lambda: self._confirm_import(open_after=True))
        self.refresh_selected.triggered.connect(lambda: self.start_metadata_refresh(self.selected_ids()))
        self.refresh_review.triggered.connect(lambda: self.start_metadata_refresh(self.review_ids))
        self.refresh_all.triggered.connect(lambda: self.start_metadata_refresh())
        self.recover_selected.triggered.connect(lambda: self.start_metadata_refresh(self.selected_ids(), ocr="force"))
        self.discard_button.clicked.connect(self._discard_selection)
        self.cancel_button.clicked.connect(lambda: self.watcher.cancel())
        self._importing(False)
        self._discard_selection()
        if parent is None:
            self.setStyleSheet(APP_STYLE)
        self.refresh()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange) and hasattr(self, "heading"):
            self.heading.setFont(serif_heading_font(self.search.font()))

    def keyPressEvent(self, event):
        # A key event handler rather than a QShortcut: that only fires once the window counts as active.
        if event.key() == Qt.Key_F2:
            return self.edit_metadata()
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        # Qt's itemActivated key differs by platform; both Enter keys open the selected paper.
        if (watched is self.table and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)):
            self._request(self.open_requested)
            return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.search.setFocus()

    # ---- list -----------------------------------------------------------------------------
    def refresh(self):
        selected, current = set(self.selected_ids()), self.selected_id()
        documents = self.workspace.library.list_documents(self.search.text())
        everyone = self.workspace.library.list_documents() if self.search.text() else documents
        self.library_count = len(everyone)
        self.review_ids = [document["id"] for document in everyone if document["metadata_status"] in NEEDS_REVIEW]
        blocker = QSignalBlocker(self.table)
        self.table.setSortingEnabled(False)
        self.table.clear()
        for document in documents:
            review = document["metadata_status"] in NEEDS_REVIEW
            verified = document["metadata_status"] == "verified"
            # The tab nickname has its own column; the title column always names the paper itself.
            item = PaperItem([document["title"] or document["filename"], document["label"], document["authors"] or "",
                              str(document["year"] or ""), "Needs review" if review else "Verified" if verified else ""])
            item.setData(0, Qt.UserRole, document["id"])
            item.setToolTip(TITLE, document["filename"])
            if review:
                item.setForeground(METADATA, REVIEW)
            metadata = document["metadata"]
            provider = (metadata.get("verified") or {}).get("provider")
            provider = {"arxiv": "arXiv", "crossref": "Crossref"}.get(str(provider).lower(), provider)
            corrected = "Corrected by you; refresh keeps those fields and can still fill the others."
            note = "\n".join(filter(None, [metadata.get("refresh", {}).get("message"),
                                           corrected if manual_fields(document) else ""])) or (
                f"Verified using {provider}." if verified and provider else
                "Extracted locally; not yet verified. Refresh metadata can recover more data from the PDF or online sources.")
            item.setToolTip(METADATA, note)
            for field, column in (("title", TITLE), ("authors", AUTHORS), ("year", YEAR)):
                source = metadata.get("sources", {}).get(field)
                if source:
                    source = {"manual": "Corrected by you", "pdf_info": "PDF properties", "xmp": "XMP properties", "filename": "Filename",
                              "arxiv": "arXiv", "crossref": "Crossref",
                              "arxiv_submission_date": "Printed arXiv submission date (preprint year)",
                              "first_page_typography": "First-page title typography (provisional)",
                              "first_page_authors": "First-page author lines (provisional)",
                              "ocr_title": "Offline OCR of the first page (provisional)",
                              "ocr_authors": "Offline OCR author lines (provisional)"}.get(source, source)
                    if source.startswith("ocr"):  # any other OCR-derived field
                        source = "Offline OCR (provisional)"
                    item.setToolTip(column, item.toolTip(column) + ("\n" if item.toolTip(column) else "") + f"Source: {source}")
            self.table.addTopLevelItem(item)
            if document["id"] in selected:
                item.setSelected(True)
            if document["id"] == current:
                self.table.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
        self.table.setSortingEnabled(True)
        blocker.unblock()
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

    def selected_ids(self):
        return [item.data(0, Qt.UserRole) for item in self.table.selectedItems()]

    def _enable(self):
        count = len(self.selected_ids())
        for button in (self.open_button, self.quick_button, self.edit_button):
            button.setEnabled(count == 1 and (button is not self.edit_button or not self.watcher))
        available = not self.watcher and not self.pending
        self.refresh_selected.setEnabled(count > 0 and available)
        self.refresh_selected.setText(f"Refresh selected papers ({count})")
        self.recover_selected.setEnabled(count > 0 and available)
        self.recover_selected.setText(f"Recover selected papers with OCR ({count})")
        self.refresh_review.setEnabled(available and bool(self.review_ids))
        self.refresh_review.setText(f"Refresh papers that need review ({len(self.review_ids)})")
        self.refresh_all.setEnabled(available and self.library_count > 0)
        self.refresh_all.setText(f"Force refresh entire library ({self.library_count})")
        self.refresh_button.setEnabled(available and self.refresh_all.isEnabled())
        self.selection_hint.setText(f"{count} selected" if count else "Select papers with Ctrl-click or Shift-click.")

    def _request(self, signal):
        ids = self.selected_ids()
        if len(ids) == 1:
            signal.emit(ids[0])
            self.hide()

    # ---- metadata -------------------------------------------------------------------------
    def edit_metadata(self):
        ids = self.selected_ids()
        if len(ids) != 1 or self.watcher:
            return
        document_id = ids[0]
        self.editor = MetadataDialog(self.workspace.library, self.workspace.library.get_document(document_id), self)
        self.editor.saved.connect(self._corrected)
        self.editor.open()

    def _corrected(self, document):
        """Bring every place that shows this paper up to date: the list, the tab titles and the references."""
        self._metadata_changed([document])

    def _metadata_changed(self, documents):
        self.refresh()
        window = self.parent()
        if window is None or not hasattr(window, "all_panes"):  # shown on its own, e.g. in tests
            return
        titles = {document["id"]: display_title(document) for document in documents}
        for pane in window.all_panes():
            source = pane.view.doc.source
            if getattr(source, "document_id", None) in titles:
                source.title = titles[source.document_id]
        window.refresh()
        # Open tabs may cite the corrected paper, so drop cached resolutions before reloading the active one.
        window.details.library_changed()

    def start_metadata_refresh(self, document_ids=None, ocr=None):
        if self.watcher or self.pending or document_ids == []:
            return
        ocr = ocr or ("auto" if self.ocr_metadata.isChecked() else "off")
        try:
            job = self.workspace.refresh_metadata(document_ids, online=self.online_metadata.isChecked(), ocr=ocr)
        except Exception as error:
            self.message.setText(f"Could not refresh metadata: {error}")
            return
        self.job_kind = "metadata"
        self.watcher = JobWatcher(self.workspace, job, self)
        self.watcher.progress.connect(self._progress)
        self.watcher.finished.connect(self._metadata_refreshed)
        self._importing(True)
        self.message.setText("Refreshing metadata… Fields you corrected are preserved.")

    def _metadata_refreshed(self, job):
        self.watcher.deleteLater()
        self.watcher = None
        self._importing(False)
        result = job.get("result", {})
        self._metadata_changed(result.get("documents", []))
        if job["status"] == "failed":
            text = f"Metadata refresh failed: {job['error']}"
        else:
            review = sum(doc["metadata_status"] in NEEDS_REVIEW for doc in result.get("documents", []))
            text = (f"{len(result.get('documents', []))} checked, {result.get('local', 0)} improved locally, "
                    f"{result.get('verified', 0)} verified online, {result.get('preserved', 0)} with your corrections kept, "
                    f"{review} need review, {len(result.get('errors', []))} failed")
            if result.get("ocr"):
                text += f", {result['ocr']} recovered with OCR"
            if result.get("errors"):
                text += ": " + "; ".join(f"Paper {e['document_id']}: {e['error']}" for e in result["errors"])
            if job["status"] == "cancelled":
                text += " (cancelled)"
        self.message.setText(text)

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
        if self.watcher:
            return
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
        self.single_import = len(files) == 1
        self.start_button.setText("Import only" if self.single_import else "Start import")
        self.message.setText("Could not scan some paths: " + "; ".join(f"{e['path']}: {e['error']}" for e in errors)
                             if errors else "")
        self._show_estimate(True)
        self.start_button.setFocus()

    def _show_estimate(self, shown):
        for widget in (self.estimate, self.start_button, self.discard_button):
            widget.setVisible(shown)
        self.import_open_button.setVisible(shown and self.single_import)
        self.import_button.setEnabled(not shown and not self.watcher)
        self.folder_button.setEnabled(not shown and not self.watcher)
        self._enable()

    def _confirm_import(self, open_after=False):
        open_after = open_after and self.single_import
        names = self.pending
        self._discard_selection()
        self.start_import(names, open_after=open_after)

    def _discard_selection(self):
        self.pending = []
        self.single_import = False
        self._show_estimate(False)

    def start_import(self, names, *, open_after=False):
        if self.watcher:
            return
        try:
            job = self.workspace.import_paths(names)
        except Exception as error:  # too many jobs, or an invalid request
            self.message.setText(f"Could not start the import: {error}")
            return
        self.job_kind, self.open_after_import = "import", open_after
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
            action = "Refreshing metadata" if self.job_kind == "metadata" else "Importing"
            provider = done.get("provider")
            if done.get("stage") == "ocr":
                detail = "offline OCR" + (f", page {done['page']}" if done.get("page") else "")
            elif provider:
                detail = "arXiv" if provider == "arxiv" else "Crossref"
            else:
                detail = ""
            self.message.setText(f"{action} {done['completed']} of {done['total']}…" + (f" ({detail})" if detail else ""))

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
        documents = job.get("result", {}).get("documents", [])
        open_after, self.open_after_import = self.open_after_import, False
        if open_after and job["status"] == "completed" and len(documents) == 1:
            self.open_requested.emit(documents[0]["id"])
            self.hide()

    def _importing(self, running):
        self.import_button.setEnabled(not running)
        self.folder_button.setEnabled(not running)
        self.progress.setVisible(running)
        self.cancel_button.setVisible(running)
        self.cancel_button.setText("Cancel refresh" if self.job_kind == "metadata" else "Cancel import")
        self._enable()
