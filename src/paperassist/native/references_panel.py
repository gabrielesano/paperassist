"""The references column: the main paper's bibliography, filterable, with local availability."""
import unicodedata

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from paperassist import references

from .jobs import JobWatcher

SHOWN_CHARS = 240  # one bibliography entry can run to a page; the tooltip keeps the whole text
MUTED = QColor("#6b675f")


def fold(text):
    """Case- and accent-insensitive, so "Antolín" is found by typing "antolin"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.category(c).startswith("M")).lower()


def reference_matcher(query):
    """Every word must match somewhere in the entry, in any order (same rule as web/reference-filter.js)."""
    words = fold(query).split()

    def matches(ref):
        text = fold(" ".join(str(ref.get(key)) for key in ("label", "authors", "year", "doi", "title", "raw") if ref.get(key)))
        return all(word in text for word in words)
    return matches


class ReferencesPanel(QWidget):
    open_requested = Signal(int)  # id of a reference's PDF that is in the library

    def __init__(self, workspace):
        super().__init__()
        self.workspace, self.document_id, self.refs = workspace, None, []
        self.filters, self.analyzing = {}, {}  # filter text and running jobs, by document id
        self.filter = QLineEdit(placeholderText="Filter references", clearButtonEnabled=True)
        self.filter.setAccessibleName("Filter references")
        self.status = QLabel(wordWrap=True)
        self.progress = QProgressBar(maximum=0, textVisible=False, maximumHeight=6)  # no progress figures: busy only
        self.retry = QPushButton("Analyze again")
        self.list = QListWidget(wordWrap=True, textElideMode=Qt.ElideNone, horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOff)
        self.list.setAccessibleName("References")
        self.list.setUniformItemSizes(False)
        column = QVBoxLayout(self)
        column.setContentsMargins(6, 0, 6, 6)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.retry)
        column.addWidget(self.filter)
        column.addLayout(row)
        column.addWidget(self.progress)
        column.addWidget(self.list, 1)
        self.filter.textChanged.connect(self._filtered)
        self.retry.clicked.connect(lambda: self.analyze(self.document_id))
        self.list.itemClicked.connect(self._open)
        self.list.itemActivated.connect(self._open)
        self._set_busy(False)

    # ---- what is shown ------------------------------------------------------------------
    def show_for(self, document_id):
        """Show the bibliography of a library paper; anything else gets an explanation."""
        self.filters[self.document_id] = self.filter.text()
        self.document_id = document_id
        self.filter.blockSignals(True)
        self.filter.setText(self.filters.get(document_id, ""))
        self.filter.blockSignals(False)
        self.refs = []
        self.list.clear()
        self.retry.hide()
        if document_id is None:
            return self._message("References are available for papers in the library.")
        if document_id in self.analyzing:
            return self._message("Analyzing references…", busy=True)
        try:
            payload = references.get(self.workspace.library, document_id)
        except Exception as error:  # a broken PDF or catalog must leave the column usable
            return self._message(f"Could not read the references: {error}", retry=True)
        if payload["status"] == "not_analyzed":
            return self.analyze(document_id)
        self.refs = payload["references"]
        self._fill()

    def _message(self, text, busy=False, retry=False):
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self._set_busy(busy)
        self.retry.setVisible(retry)

    def _set_busy(self, busy):
        self.progress.setVisible(busy)

    def _fill(self):
        matches = reference_matcher(self.filter.text())
        self.list.clear()
        shown = [ref for ref in self.refs if matches(ref)]
        for ref in shown:
            local = ref.get("local_document_id")
            text = (ref.get("title") or ref.get("raw") or "Unidentified reference").strip()
            label = f"[{ref['label']}] " if str(ref.get("label")).isdigit() else ""  # author-year labels repeat the entry
            item = QListWidgetItem(f"{label}{text[:SHOWN_CHARS]}\n{'In library' if local else 'Not in library'}")
            item.setToolTip(ref.get("raw") or text)
            item.setData(Qt.UserRole, local)
            if not local:
                item.setForeground(MUTED)
            self.list.addItem(item)
        if not self.refs:
            self._message("No references were found in this paper.", retry=True)
        elif not shown:
            self._message("No matching references.")
        else:
            self._message(f"{len(shown)} of {len(self.refs)} references" if len(shown) != len(self.refs) else f"{len(shown)} references")

    def _filtered(self):
        if self.refs:
            self._fill()

    def _open(self, item):
        if (target := item.data(Qt.UserRole)) is not None:
            self.open_requested.emit(target)

    # ---- analysis job ---------------------------------------------------------------------
    def analyze(self, document_id):
        try:
            job = self.workspace.analyze_references(document_id)
        except Exception as error:  # too many jobs, or the document is gone
            return self._message(f"Could not start the analysis: {error}", retry=True)
        watcher = self.analyzing[document_id] = JobWatcher(self.workspace, job, self)
        watcher.finished.connect(lambda result: self._analyzed(document_id, result))
        if document_id == self.document_id:
            self._message("Analyzing references…", busy=True)

    def _analyzed(self, document_id, job):
        self.analyzing.pop(document_id).deleteLater()
        if document_id != self.document_id:
            return  # the user moved on; the result is saved and appears when they come back
        if job["status"] == "completed":
            self.show_for(document_id)
        else:
            self._message(f"Could not analyze the references: {job.get('error') or job['status']}", retry=True)

    def close(self):
        for watcher in self.analyzing.values():
            watcher.stop()
        return super().close()
