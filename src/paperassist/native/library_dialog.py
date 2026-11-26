"""The library list and paper metadata editor: search, open, edit, refresh, and import PDFs."""
from pathlib import Path
import re

from PySide6.QtCore import QEvent, QItemSelectionModel, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QWidget)

from paperassist.library import MAX_LABEL, manual_fields, pdf_files
from paperassist.paths import format_size
from paperassist.settings import PROVIDER_NAMES

from .dialogs import ElidedLabel, setup_dialog
from .jobs import JobWatcher
from .source import display_title
from .theme import FlowLayout, serif_heading_font, set_props

TITLE, TAB_NAME, AUTHORS, YEAR, METADATA = range(5)
REVIEW = QColor("#a15c00")  # amber: not an error, a paper whose metadata nobody confirmed
NEEDS_REVIEW = ("needs_review", "incomplete")
OCR_TIP = ("OCR runs locally and can take up to about a minute per paper. It never modifies the PDF, "
           "and its results stay provisional until you review them.")
METADATA_FIELDS = (("title", "Title"), ("label", "Tab name"), ("authors", "Authors"), ("year", "Year"), ("doi", "DOI"))
SOURCE_LABELS = {
    "manual": "Corrected by you",
    "pdf_info": "PDF properties",
    "xmp": "XMP properties",
    "filename": "Filename",
    **PROVIDER_NAMES,
    "arxiv_submission_date": "Printed arXiv submission date (preprint year)",
    "first_page_typography": "First-page title typography (provisional)",
    "first_page_authors": "First-page author lines (provisional)",
    "ocr_title": "Offline OCR of the first page (provisional)",
    "ocr_authors": "Offline OCR author lines (provisional)",
}


class MetadataDialog(QDialog):
    """Correct the title, authors, year and DOI of one paper; a rejected value is explained and kept for editing."""
    saved = Signal(dict)  # the document as stored, after the correction

    def __init__(self, library, document, parent=None):
        super().__init__(parent)
        self.library, self.document_id = library, document["id"]
        self.setMinimumWidth(520)
        column, _ = setup_dialog(self, "Edit paper details")
        form = QFormLayout()
        form.setSpacing(8)
        self.fields = {}
        for key, label in METADATA_FIELDS:
            field = QLineEdit(str(document.get(key) or ""))
            field.setAccessibleName(label)
            form.addRow(f"&{label}", field)
            self.fields[key] = field
        # Shown in place, so the typed values stay on screen while the user fixes them.
        self.error = QLabel(wordWrap=True)
        set_props(self.error, tone="error")
        self.error.setAccessibleName("Validation message")
        self.error.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        if save_btn := buttons.button(QDialogButtonBox.Save):
            save_btn.setProperty("primary", True)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        column.addLayout(form)
        if note := document.get("metadata", {}).get("refresh", {}).get("message"):
            column.addWidget(QLabel(note, objectName="muted-note", wordWrap=True))
        column.addWidget(self.error)
        column.addWidget(buttons)
        self.fields["title"].setFocus()

    def save(self):
        values = {key: field.text().strip() for key, field in self.fields.items()}
        try:
            if values["year"] and not re.fullmatch(r"\d{1,4}", values["year"]):
                raise ValueError("Year must be a number such as 2015")
            if len(values.get("label", "")) > MAX_LABEL:
                raise ValueError(f"Tab name cannot be longer than {MAX_LABEL} characters")
            label = values.pop("label", None)
            document = self.library.update_metadata(self.document_id, values)
            if label is not None:
                document = self.library.set_label(self.document_id, label)
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.saved.emit(document)
        self.accept()


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


class ErrorDetailsDialog(QDialog):
    """Read-only table of per-item failures for import or metadata refresh runs."""

    def __init__(self, title, errors, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(680, 340)
        has_location = any(len(entry) > 2 and entry[2] for entry in errors)
        headers = ["File", "Problem", "Location"] if has_location else ["Paper", "Problem"]
        count = len(errors)
        subtitle = f"{count} item{'s' if count != 1 else ''} could not be processed."
        column, self.heading = setup_dialog(self, title, subtitle=subtitle)
        self.table = QTreeWidget(objectName="dialog-tree", rootIsDecorated=False, headerLabels=headers)
        self.table.setAccessibleName(title)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 240 if has_location else 420)
        for entry in errors:
            item_name, problem = entry[0], entry[1]
            location = entry[2] if len(entry) > 2 else ""
            row = [item_name, problem, location] if has_location else [item_name, problem]
            item = QTreeWidgetItem(row)
            for col, value in enumerate(row):
                if value:
                    item.setToolTip(col, value)
            self.table.addTopLevelItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        column.addWidget(self.table, 1)
        column.addWidget(buttons)


class LibraryDialog(QDialog):
    open_requested = Signal(int)   # document id, to read in a tab
    quick_requested = Signal(int)  # document id, to consult in the quick view

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace, self.watcher = workspace, None
        self.resize(960, 600)
        self.setMinimumSize(720, 440)
        column, self.heading = setup_dialog(self, "Library")
        self.search = QLineEdit(placeholderText="Search title, tab name, author, year or DOI", clearButtonEnabled=True)
        self.search.setAccessibleName("Search the library")
        self.table = QTreeWidget(objectName="dialog-tree", rootIsDecorated=False, uniformRowHeights=True,
                                 headerLabels=["Title", "Tab name", "Authors", "Year", "Metadata"])
        self.table.setAccessibleName("Papers")
        self._column_reflow = QTimer(self, singleShot=True, interval=0,
                                     timeout=lambda: self._sync_column_widths(force_minimums=True))
        self.table.installEventFilter(self)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.AscendingOrder)
        header = self.table.header()
        header.setStretchLastSection(False)
        header.setToolTip("Click a column to sort; click again to reverse the order.")
        self._updating_columns = False
        self._columns_customized = False
        header.sectionResized.connect(self._on_column_resized)
        self.open_button = QPushButton("Open")
        self.open_button.setProperty("primary", True)
        self.quick_button = QPushButton("Quick view")
        self.edit_button = QPushButton("Edit details…")
        self.edit_button.setToolTip("Correct the title, authors, year or DOI (F2)")
        self.copy_bibtex_button = QPushButton("Copy BibTeX")
        self.copy_bibtex_button.setToolTip("Copy BibTeX citations for verified selected papers")
        self.remove_button = QPushButton("Remove…")
        self.remove_button.setToolTip("Remove selected papers from this library (Delete)")
        self.selection_hint = ElidedLabel(objectName="muted-note")
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
        self.compact_button = QPushButton("Compact revisions…")
        self.compact_button.setToolTip("Remove superseded PDF revisions from working/")
        self.cancel_button = QPushButton("Cancel import")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setFixedHeight(14)
        self.progress.setTextVisible(False)
        self.hint = QLabel("The library is empty.\nChoose Import PDFs or Import folder to add papers.",
                           objectName="muted-note", alignment=Qt.AlignCenter)
        self.hint.setAccessibleName("Empty library")
        self.estimate = ElidedLabel(objectName="muted-note")  # what an import would add, shown before anything is copied
        self.estimate.setAccessibleName("Import estimate")
        self.start_button = QPushButton("Start import")
        self.start_button.setProperty("primary", True)
        self.import_open_button = QPushButton("Import && open")
        self.import_open_button.setToolTip("Import this PDF and open it in a reader tab when ready.")
        self.discard_button = QPushButton("Cancel")
        self.discard_button.setAccessibleName("Cancel import selection")
        self.message = ElidedLabel(objectName="muted-note")
        self.message.setAccessibleName("Library status")
        self.details_button = QPushButton("Details…")
        self.details_button.setProperty("link", True)
        self.details_button.setAccessibleName("Show error details")
        self.details_button.hide()

        self.status_bar = QWidget()
        status_row = QHBoxLayout(self.status_bar)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        status_row.addWidget(self.message, 1)
        status_row.addWidget(self.details_button)
        status_row.addWidget(self.progress)
        status_row.addWidget(self.cancel_button)

        buttons = FlowLayout(h_spacing=8)
        for widget in (self.import_button, self.folder_button, self.compact_button, self.quick_button, self.open_button):
            buttons.addWidget(widget)

        metadata_row = FlowLayout(h_spacing=8)
        for widget in (self.selection_hint, self.copy_bibtex_button, self.edit_button, self.remove_button,
                       self.refresh_button):
            metadata_row.addWidget(widget)

        self.confirm_row = FlowLayout(h_spacing=8)
        for widget in (self.estimate, self.start_button, self.import_open_button, self.discard_button):
            self.confirm_row.addWidget(widget)

        column.addWidget(self.search)
        column.addLayout(metadata_row)
        column.addWidget(self.table, 1)
        column.addWidget(self.hint, 1)
        column.addLayout(self.confirm_row)
        column.addWidget(self.status_bar)
        column.addLayout(buttons)

        self.pending = []
        self.library_count = 0
        self._shown_count = 0
        self._filtered = False
        self.review_ids = []
        self._superseded = {"count": 0, "bytes": 0}
        self.single_import = False
        self.job_kind = "import"
        self.error_details = []
        self._details_title = "Details"
        self.error_dialog = None
        self.typing = QTimer(self, singleShot=True, interval=150, timeout=lambda: self.refresh(reload_counts=False))
        self.search.textChanged.connect(self.typing.start)
        self.table.itemActivated.connect(lambda: self._request(self.open_requested))
        self.table.itemSelectionChanged.connect(self._enable)
        self.open_button.clicked.connect(lambda: self._request(self.open_requested))
        self.quick_button.clicked.connect(lambda: self._request(self.quick_requested))
        self.copy_bibtex_button.clicked.connect(self.copy_selected_bibtex)
        self.edit_button.clicked.connect(self.edit_metadata)
        self.remove_button.clicked.connect(self.remove_selected)
        self.import_button.clicked.connect(self.import_dialog)
        self.folder_button.clicked.connect(self.import_folder_dialog)
        self.compact_button.clicked.connect(self.compact_revisions)
        self.start_button.clicked.connect(lambda: self._confirm_import())
        self.import_open_button.clicked.connect(lambda: self._confirm_import(open_after=True))
        self.refresh_selected.triggered.connect(lambda: self.start_metadata_refresh(self.selected_ids()))
        self.refresh_review.triggered.connect(lambda: self.start_metadata_refresh(self.review_ids))
        self.refresh_all.triggered.connect(lambda: self.start_metadata_refresh())
        self.recover_selected.triggered.connect(lambda: self.start_metadata_refresh(self.selected_ids(), ocr="force"))
        self.discard_button.clicked.connect(self._discard_selection)
        self.cancel_button.clicked.connect(lambda: self.watcher.cancel())
        self.details_button.clicked.connect(self.show_error_details)
        self._sync_column_widths()
        self._set_job_running(False)
        self._discard_selection()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange) and hasattr(self, "heading"):
            self.heading.setFont(serif_heading_font(self.search.font()))
            if hasattr(self, "_column_reflow"):
                self._column_reflow.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_column_widths()

    def _on_column_resized(self, _index, _old, _new):
        if not self._updating_columns:
            self._columns_customized = True

    def _sync_column_widths(self, *, force_minimums=False):
        fm = self.table.fontMetrics()
        year_w = max(72, fm.horizontalAdvance("2026") + 34)
        meta_w = max(114, fm.horizontalAdvance("Needs review") + 30)
        has_labels = any(self.table.topLevelItem(i).text(TAB_NAME) for i in range(self.table.topLevelItemCount()))
        tab_w = max(116 if has_labels else 92, fm.horizontalAdvance("Tab name") + 30)
        self._updating_columns = True
        try:
            if self._columns_customized:
                if force_minimums:
                    if self.table.columnWidth(YEAR) < year_w:
                        self.table.setColumnWidth(YEAR, year_w)
                    if self.table.columnWidth(METADATA) < meta_w:
                        self.table.setColumnWidth(METADATA, meta_w)
                return
            avail = max(420, (self.table.viewport().width() or (self.width() - 48)) - year_w - meta_w - tab_w)
            title_w = round(avail * 0.65)
            authors_w = avail - title_w
            for col, width in ((TITLE, title_w), (TAB_NAME, tab_w), (AUTHORS, authors_w),
                               (YEAR, year_w), (METADATA, meta_w)):
                self.table.setColumnWidth(col, width)
        finally:
            self._updating_columns = False

    def _sync_status_bar(self):
        self.status_bar.setVisible(bool(self.message.text()) or self.watcher is not None)

    def _set_message(self, text="", *, tone="", details=None, details_title="Details"):
        self.error_details = list(details or [])
        self._details_title = details_title
        self.message.setText(text)
        self.message.setObjectName("library-status" if tone else "muted-note")
        set_props(self.message, tone=tone)
        if self.error_details:
            lines = [f"{location or item}: {problem}" for item, problem, location in self.error_details[:8]]
            if len(self.error_details) > 8:
                lines.append(f"…and {len(self.error_details) - 8} more")
            self.message.setToolTip("\n".join(lines))
        else:
            self.message.setToolTip("")
        self.details_button.setVisible(bool(self.error_details))
        self._sync_status_bar()

    def show_error_details(self):
        if not self.error_details:
            return
        self.error_dialog = ErrorDetailsDialog(self._details_title, self.error_details, self)
        self.error_dialog.open()

    def keyPressEvent(self, event):
        # A key event handler rather than a QShortcut: that only fires once the window counts as active.
        if event.key() == Qt.Key_F2:
            return self.edit_metadata()
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.table and event.type() in (QEvent.FontChange, QEvent.StyleChange):
            # The view may receive its new font after the dialog's style change.
            self._column_reflow.start()
        # Qt's itemActivated key differs by platform; both Enter keys open the selected paper.
        if (watched is self.table and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)):
            self._request(self.open_requested)
            return True
        if (watched is self.table and event.type() == QEvent.KeyPress
                and event.key() == Qt.Key_Delete and event.modifiers() == Qt.NoModifier):
            self.remove_selected()
            return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._sync_column_widths()
        self.search.setFocus()

    # ---- list -----------------------------------------------------------------------------
    def refresh(self, *, reload_counts=True):
        selected, current = set(self.selected_ids()), self.selected_id()
        query = self.search.text()
        documents = self.workspace.library.list_documents(query, lightweight=True)
        self._shown_count = len(documents)
        self._filtered = bool(query.strip())
        if not query:
            self.library_count = len(documents)
            self.review_ids = [document["id"] for document in documents if document["metadata_status"] in NEEDS_REVIEW]
        elif reload_counts:
            self.library_count, self.review_ids = self.workspace.library.status_counts()
        if not query or reload_counts:
            self._superseded = self.workspace.library.superseded_snapshots()
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
            provider = PROVIDER_NAMES.get(str(provider).lower(), provider)
            corrected = "Corrected by you; refresh keeps those fields and can still fill the others."
            note = "\n".join(filter(None, [metadata.get("refresh", {}).get("message"),
                                           corrected if manual_fields(document) else ""])) or (
                f"Verified using {provider}." if verified and provider else
                "Extracted locally; not yet verified. Refresh metadata can recover more data from the PDF or online sources.")
            item.setToolTip(METADATA, note)
            for field, col in (("title", TITLE), ("authors", AUTHORS), ("year", YEAR)):
                source = metadata.get("sources", {}).get(field)
                if source:
                    source = SOURCE_LABELS.get(source, source)
                    if source.startswith("ocr"):  # any other OCR-derived field
                        source = "Offline OCR (provisional)"
                    item.setToolTip(col, item.toolTip(col) + ("\n" if item.toolTip(col) else "") + f"Source: {source}")
            self.table.addTopLevelItem(item)
            if document["id"] in selected:
                item.setSelected(True)
            if document["id"] == current:
                self.table.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
        self.table.setSortingEnabled(True)
        blocker.unblock()
        self._sync_column_widths()
        empty = not documents and not query
        self.table.setVisible(not empty)
        self.hint.setVisible(empty)
        if empty:
            self._set_message("")
        elif not documents:
            self._set_message("No paper matches this search.")
        elif not self.watcher:
            self._set_message("")
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
        self.copy_bibtex_button.setEnabled(count > 0 and not self.pending)
        has_superseded = self._superseded["count"] > 0
        self.compact_button.setVisible(has_superseded)
        self.compact_button.setEnabled(available and has_superseded)
        if has_superseded:
            self.compact_button.setToolTip(
                f"Remove {self._superseded['count']} superseded PDF revision"
                f"{'s' if self._superseded['count'] != 1 else ''} ({format_size(self._superseded['bytes'])})"
            )
        else:
            self.compact_button.setToolTip("No superseded PDF revisions to compact.")
        self.remove_button.setEnabled(count > 0 and available)
        self.refresh_selected.setEnabled(count > 0 and available)
        self.refresh_selected.setText(f"Refresh selected papers ({count})")
        self.recover_selected.setEnabled(count > 0 and available)
        self.recover_selected.setText(f"Recover selected papers with OCR ({count})")
        self.refresh_review.setEnabled(available and bool(self.review_ids))
        self.refresh_review.setText(f"Refresh papers that need review ({len(self.review_ids)})")
        self.refresh_all.setEnabled(available and self.library_count > 0)
        self.refresh_all.setText(f"Force refresh entire library ({self.library_count})")
        self.refresh_button.setEnabled(available and self.refresh_all.isEnabled())
        if self.library_count == 0 and not self._filtered:
            self.selection_hint.setText("")
        else:
            base = (f"{self._shown_count} of {self.library_count} papers" if self._filtered
                    else f"{self._shown_count} paper{'s' if self._shown_count != 1 else ''}")
            self.selection_hint.setText(f"{base} · {count} selected" if count else base)

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

    def confirm_remove(self, documents):
        count = len(documents)
        subject = f'"{display_title(documents[0])}"' if count == 1 else f"{count} selected papers"
        box = QMessageBox(QMessageBox.Warning, "Remove from library", f"Remove {subject} from this library?",
                          QMessageBox.Yes | QMessageBox.Cancel, self)
        box.setInformativeText(
            "Managed PDF copies, saved annotations, and comments in this library will be permanently deleted. "
            "External files outside PaperAssist stay untouched."
        )
        box.button(QMessageBox.Yes).setText("Remove")
        box.setDefaultButton(QMessageBox.Cancel)
        return box.exec() == QMessageBox.Yes

    def copy_selected_bibtex(self):
        ids = self.selected_ids()
        if not ids or self.pending:
            return
        try:
            bibtex_text, exported, skipped = self.workspace.library.export_bibtex(ids)
        except KeyError:
            self.refresh()
            return
        if not exported:
            self._set_message("Verify metadata first to copy BibTeX.")
            return
        QGuiApplication.clipboard().setText(bibtex_text)
        entry_word = "entry" if exported == 1 else "entries"
        if skipped:
            skip_word = "paper" if skipped == 1 else "papers"
            self._set_message(f"Copied {exported} BibTeX {entry_word} ({skipped} unverified {skip_word} skipped).")
        else:
            self._set_message(f"Copied {exported} BibTeX {entry_word}.")

    def confirm_compact(self, info):
        count, size = info["count"], format_size(info["bytes"])
        box = QMessageBox(
            QMessageBox.Question,
            "Compact revisions",
            f"Remove {count} superseded PDF revision{'s' if count != 1 else ''} ({size})?",
            QMessageBox.Yes | QMessageBox.Cancel,
            self,
        )
        box.setInformativeText(
            "Only older intermediate snapshots in working/ will be deleted. "
            "Current revisions and original PDFs stay untouched."
        )
        box.button(QMessageBox.Yes).setText("Compact")
        box.setDefaultButton(QMessageBox.Cancel)
        return box.exec() == QMessageBox.Yes

    def compact_revisions(self):
        if self.watcher or self.pending:
            return
        info = self.workspace.library.superseded_snapshots()
        self._superseded = info
        self._enable()
        if info["count"] <= 0 or not self.confirm_compact(info):
            return
        result = self.workspace.library.compact_snapshots()
        self._superseded = self.workspace.library.superseded_snapshots()
        self._enable()
        removed = result["removed"]
        self._set_message(
            f"Removed {removed} superseded revision{'s' if removed != 1 else ''}, "
            f"freed {format_size(result['bytes_freed'])}."
        )

    def remove_selected(self):
        ids = self.selected_ids()
        if not ids or self.watcher or self.pending:
            return
        try:
            documents = [self.workspace.library.get_document(document_id) for document_id in ids]
        except KeyError:
            self.refresh()
            return
        if not self.confirm_remove(documents):
            return
        window = self.parent()
        # Close open tabs/quick views first so PositionKeeper does not write to deleted rows.
        if window is not None and hasattr(window, "close_documents"):
            window.close_documents(ids)
        try:
            removed = self.workspace.library.remove_documents(ids)
        except Exception as error:
            self._set_message(f"Could not remove selected papers: {error}", tone="error")
            return
        self.refresh()
        self._set_message(f"Removed {removed} paper{'s' if removed != 1 else ''} from the library.")
        if window is not None and hasattr(window, "details"):
            window.details.library_changed()

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
            if source.document_id in titles:
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
            self._set_message(f"Could not refresh metadata: {error}", tone="error")
            return
        self._watch_job(job, "metadata", self._metadata_refreshed)
        self._set_message("Refreshing metadata…")

    def _metadata_refreshed(self, job):
        result = job.get("result", {})
        self._metadata_changed(result.get("documents", []))
        if job["status"] == "failed":
            self._set_message(f"Metadata refresh failed: {job['error']}", tone="error")
            return
        checked = len(result.get("documents", []))
        errors = result.get("errors", [])
        details = [(f"Paper {e['document_id']}", e["error"], "") for e in errors]
        changes = []
        for key, label in (("local", "improved locally"), ("verified", "verified online"),
                           ("preserved", "kept corrections"), ("ocr", "recovered with OCR")):
            if count := result.get(key, 0):
                changes.append(f"{count} {label}")
        if len(details) == 1:
            changes.append(f"1 failed ({details[0][0]}: {details[0][1]})")
        elif details:
            changes.append(f"{len(details)} failed")
        parts = [f"{checked} checked"] + (changes or (["no changes"] if checked else []))
        text = ", ".join(parts) + (" (cancelled)" if job["status"] == "cancelled" else "")
        self._set_message(text, tone="error" if details else "", details=details, details_title="Metadata refresh errors")

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
        details = [(Path(e["path"]).name or e["path"], e["error"], e["path"]) for e in errors]
        if not files:
            self._discard_selection()
            if details:
                summary = (f"Could not scan selection ({details[0][0]}: {details[0][1]})"
                           if len(details) == 1 else f"Could not scan selection ({len(details)} errors)")
                self._set_message(summary, tone="error", details=details, details_title="Scan errors")
            else:
                self._set_message("No PDF files in the selection.")
            return
        total = 0
        for path in files:
            try:
                total += path.stat().st_size
            except OSError as error:
                errors.append({"path": str(path), "error": str(error)})
        details = [(Path(e["path"]).name or e["path"], e["error"], e["path"]) for e in errors]
        if details:
            self.estimate.setText(
                f"{len(files)} readable PDF{'s' if len(files) != 1 else ''} (storage estimate unavailable)"
            )
            summary = (f"Skipped unreadable path ({details[0][0]}: {details[0][1]})"
                       if len(details) == 1 else f"Skipped {len(details)} unreadable paths")
            self._set_message(summary, tone="error", details=details, details_title="Scan errors")
        else:
            # Duplicates are not copied, so the total is an upper bound.
            self.estimate.setText(
                f"{len(files)} PDF{'s' if len(files) != 1 else ''}, up to {format_size(total)} of extra storage"
            )
            self._set_message("")
        self.pending = list(names)
        self.single_import = len(files) == 1
        self.start_button.setText("Import only" if self.single_import else "Start import")
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
            self._set_message(f"Could not start the import: {error}", tone="error")
            return
        self._watch_job(job, "import", lambda result: self._imported(result, open_after))
        self._set_message("Importing…")

    def _watch_job(self, job, kind, finished):
        self.job_kind = kind
        watcher = self.watcher = JobWatcher(self.workspace, job, self)
        watcher.progress.connect(self._progress)

        def done(result):
            watcher.deleteLater()
            self.watcher = None
            self._set_job_running(False)
            finished(result)

        watcher.finished.connect(done)
        self._set_job_running(True)

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
                detail = PROVIDER_NAMES.get(provider, provider)
            else:
                detail = ""
            self._set_message(f"{action} {done['completed']} of {done['total']}…" + (f" ({detail})" if detail else ""))

    def _imported(self, job, open_after):
        if job["status"] == "failed":
            self.refresh()
            self._set_message(f"Import failed: {job['error']}", tone="error")
            return
        if "result" not in job:  # cancelled before the first file
            self.refresh()
            self._set_message("Import cancelled")
            return
        result = job["result"]
        imported, dups, errors = result["imported"], result["duplicates"], result["errors"]
        details = [(Path(e["path"]).name or e["path"], e["error"], e["path"]) for e in errors]
        parts = []
        if imported or (not dups and not details):
            parts.append(f"{imported} added")
        if dups:
            parts.append(f"{dups} already in library")
        if len(details) == 1:
            parts.append(f"1 failed ({details[0][0]}: {details[0][1]})")
        elif details:
            parts.append(f"{len(details)} failed")
        text = (", ".join(parts) or "Import cancelled") + (" (cancelled)" if result["cancelled"] and parts else "")
        self.refresh()
        self._set_message(text, tone="error" if details else "", details=details, details_title="Import errors")
        window = self.parent()
        if imported and hasattr(window, "details"):
            window.details.library_changed()
        documents = result.get("documents", [])
        if open_after and job["status"] == "completed" and len(documents) == 1:
            self.open_requested.emit(documents[0]["id"])
            self.hide()

    def _set_job_running(self, running):
        self.import_button.setEnabled(not running)
        self.folder_button.setEnabled(not running)
        self.progress.setVisible(running)
        self.cancel_button.setVisible(running)
        self.cancel_button.setText("Cancel refresh" if self.job_kind == "metadata" else "Cancel import")
        self._sync_status_bar()
        self._enable()
