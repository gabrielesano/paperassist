"""Reference acquisition and inline actions, using the existing Workspace jobs."""
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from paperassist import references

from .jobs import JobWatcher

PROVIDERS = {"crossref": "Crossref", "ads": "ADS", "arxiv": "arXiv", "openalex": "OpenAlex",
             "semantic_scholar": "Semantic Scholar", "core": "CORE", "unpaywall": "Unpaywall", "local": "Local library"}
RESULTS = {"downloaded": "Downloaded", "local": "Already in library", "not_identified": "Not identified",
           "ambiguous": "Several matches", "blocked": "PDF to open in browser", "not_found": "No open-access PDF found",
           "failed": "Search incomplete"}


def current_retrieval(ref):
    value = ref.get("retrieval") or {}
    return value if value.get("version") == 2 else {}


def reference_status(ref):
    result = current_retrieval(ref)
    if ref.get("local_document_id"):
        return "Linked PDF needs verification" if ref.get("local_conflict") else "In library"
    if ref.get("local_invalid"):
        return "Invalid link: source paper selected"
    if ref.get("local_missing"):
        return "Linked PDF missing from library"
    if ref.get("legacy"):
        return "Previous reference — verify link"
    return RESULTS.get(result.get("status"), "Not searched")


def status_tone(ref):
    if ref.get("local_conflict") or ref.get("local_invalid"):
        return "#9a4b2e"
    return "#267368" if ref.get("local_document_id") else "#6b675f"


def retrieval_details(ref):
    result = current_retrieval(ref)
    lines = [result.get("summary") or ""]
    if ref.get("retrieval") and not result and not ref.get("local_document_id"):
        lines = ["Earlier search used fewer sources. Not searched with current providers."]
    for attempt in result.get("attempts") or []:
        if attempt.get("status") not in ("skipped", "not_configured"):
            lines.append(f"{PROVIDERS.get(attempt.get('provider'), attempt.get('provider', 'Provider'))}: "
                         f"{attempt.get('message') or attempt.get('status', '')}")
    return "\n".join(line for line in lines if line)


def web_url(url):
    try:
        parts = urlsplit(url)
        return url if parts.scheme in ("http", "https") and parts.hostname else None
    except (TypeError, ValueError):
        return None


def proxy_url(proxy, target):
    if not proxy or not web_url(target) or not web_url(proxy.replace("{url}", "x")):
        return None
    return proxy.replace("{url}", quote(target, safe="")) if "{url}" in proxy else proxy + target


def search_links(ref):
    queries = [("Search title", ref.get("title")),
               ("Search author/year", " ".join(str(ref.get(k) or "") for k in ("authors", "year")).strip()),
               ("Search DOI", ref.get("doi"))]
    combined = " ".join(str(ref.get(k) or "") for k in ("title", "authors", "year", "doi")).strip() or ref.get("raw", "")
    queries.append(("Search PDF", combined + " filetype:pdf" if combined else ""))
    return [(label, "https://www.google.com/search?" + urlencode({"q": query})) for label, query in queries if query]


def provider_summary(settings):
    return " · ".join(f"{PROVIDERS[name]}: " + ("disabled" if not state["enabled"] else
                     "ready" if state["configured"] else f"{state.get('needs') or 'configuration'} needed")
                     for name, state in settings.providers().items())


class SelectionStore:
    """A library-local UI sidecar keeps exact-source selections without a database migration."""

    def __init__(self, directory):
        self.path = Path(directory) / "native-acquisition.json"

    def load(self, document_id):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return set(map(str, value.get(str(document_id), []))) if isinstance(value, dict) else set()
        except (OSError, ValueError, TypeError):
            return set()

    def save(self, document_id, values):
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(stored, dict):
                stored = {}
        except (OSError, ValueError):
            stored = {}
        stored[str(document_id)] = sorted(map(str, values))
        descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix=".acquisition-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(stored, stream)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


class AcquisitionDialog(QDialog):
    def __init__(self, controller, document_id, refs):
        super().__init__(controller.window)
        self.setAttribute(Qt.WA_DeleteOnClose)  # a closed offer would otherwise keep a checkbox per reference alive
        self.controller, self.document_id, self.boxes, self.rows = controller, document_id, [], {}
        self.setWindowTitle("Acquire references")
        self.resize(650, 540)
        column = QVBoxLayout(self)
        column.addWidget(QLabel("Find open-access PDFs for this paper's references.", wordWrap=True))
        column.addWidget(QLabel(provider_summary(controller.workspace.get_settings()), wordWrap=True))
        row = QHBoxLayout()
        self.count = QLabel()
        for label, selected in (("Select all", True), ("Clear all", False)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=selected: self.select_all(value))
            row.addWidget(button)
        row.addWidget(self.count, 1)
        column.addLayout(row)
        content, scroll = QWidget(), QScrollArea(widgetResizable=True)
        entries = QVBoxLayout(content)
        saved = controller.selections.load(document_id)
        for ref in refs:
            local = bool(ref.get("local_document_id"))
            text = f"{ref.get('label') or ''} {ref.get('title') or ref.get('raw') or 'Unidentified reference'}"
            box = QCheckBox(text.strip())
            box.setToolTip(text)
            box.setEnabled(not local and not ref.get("legacy"))
            box.setChecked(box.isEnabled() and str(ref["id"]) in saved)
            box.setProperty("reference_id", str(ref["id"]))
            box.toggled.connect(self.selection_changed)
            entries.addWidget(box)
            status = QLabel("In library — no download needed" if local else reference_status(ref), wordWrap=True)
            entries.addWidget(status)
            self.boxes.append(box)
            self.rows[str(ref["id"])] = (box, status)
        if not refs:
            entries.addWidget(QLabel("No references were found in this paper.", wordWrap=True))
        entries.addStretch()
        scroll.setWidget(content)
        column.addWidget(scroll, 1)
        self.message = QLabel(wordWrap=True)
        column.addWidget(self.message)
        buttons = QHBoxLayout()
        self.all_button, self.selected_button = QPushButton("Acquire all missing"), QPushButton("Acquire selected")
        continue_button = QPushButton("Continue reading")
        for button in (self.all_button, self.selected_button, continue_button):
            buttons.addWidget(button)
        self.all_button.clicked.connect(lambda: self.start(True))
        self.selected_button.clicked.connect(lambda: self.start(False))
        continue_button.clicked.connect(self.close)
        column.addLayout(buttons)
        self.update_count()

    def selected(self):
        return [box.property("reference_id") for box in self.boxes if box.isEnabled() and box.isChecked()]

    def update_count(self):
        values = self.selected()
        self.count.setText(f"{len(values)} selected")
        self.selected_button.setEnabled(bool(values))
        self.all_button.setEnabled(any(box.isEnabled() for box in self.boxes))
        return values

    def selection_changed(self):
        try:
            self.controller.selections.save(self.document_id, self.update_count())
        except OSError as error:
            self.message.setText(f"Could not remember the selection: {error}")

    def select_all(self, selected):
        for box in self.boxes:
            if box.isEnabled():
                box.blockSignals(True)
                box.setChecked(selected)
                box.blockSignals(False)
        self.selection_changed()

    def update_references(self, refs):
        available = {str(ref["id"]): ref for ref in refs}
        for reference_id, (box, status) in self.rows.items():
            ref = available.get(reference_id)
            enabled = ref is not None and not ref.get("local_document_id") and not ref.get("legacy")
            box.setEnabled(enabled)
            if not enabled:
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
            if ref:
                status.setText("In library — no download needed" if ref.get("local_document_id") else reference_status(ref))
        self.selection_changed()

    def start(self, all_missing):
        ids = [box.property("reference_id") for box in self.boxes if box.isEnabled()] if all_missing else self.selected()
        if ids and self.controller.acquire(self.document_id, ids):
            self.close()


class CardActions(QWidget):
    layout_changed = Signal()


class AcquisitionController(QObject):
    changed = Signal(int)
    open_requested = Signal(int)
    preferences_requested = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window, self.workspace = window, window.workspace
        self._closed = False
        self.selections = SelectionStore(self.workspace.data_dir) if self.workspace else None
        self.data, self.pending, self.dialogs, self.jobs, self.messages, self.expanded = {}, set(), {}, {}, {}, set()
        self.status = QLabel(wordWrap=True)
        self.cancel_button = QPushButton("Cancel search")
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(self.cancel)
        self.window.statusBar().addWidget(self.status, 1)
        self.window.statusBar().addPermanentWidget(self.cancel_button)

    def set_reference_data(self, document_id, refs, status="ready"):
        if self._closed:
            return
        self.data[document_id] = list(refs)
        if document_id in self.dialogs:
            self.dialogs[document_id].update_references(refs)
        if document_id in self.pending:
            self.pending.remove(document_id)
            QTimer.singleShot(0, lambda: self.offer(document_id))

    def main_opened(self, document_id):
        if self._closed or self.workspace is None or document_id is None:
            return
        if document_id in self.data:
            QTimer.singleShot(0, lambda: self.offer(document_id))
        else:
            self.pending.add(document_id)

    def offer(self, document_id, refs=None):
        if self._closed or self.workspace is None:
            return
        if refs is not None:
            self.data[document_id] = list(refs)
        if document_id not in self.data:
            self.pending.add(document_id)
            return
        dialog = self.dialogs.get(document_id)
        if dialog is None:
            dialog = AcquisitionDialog(self, document_id, self.data[document_id])
            self.dialogs[document_id] = dialog
            dialog.finished.connect(lambda: self.dialogs.pop(document_id, None))
        dialog.show()
        dialog.raise_()

    def _say(self, document_id, text):
        self.messages[document_id] = text
        self.window.statusBar().clearMessage()
        self.status.setText(text)
        self.status.setToolTip(text)

    def _open_for_source(self, document_id, target):
        if hasattr(self.window, "tab"):
            tab = self.window.tab()
            if tab is None or getattr(tab.main.view.doc.source, "document_id", None) != document_id:
                return
        self.open_requested.emit(target)

    def _watch(self, document_id, job, finished):
        watcher = JobWatcher(self.workspace, job, self)
        self.jobs[job["id"]] = watcher
        self.cancel_button.show()
        watcher.progress.connect(lambda value: self._progress(document_id, value))
        def done(value):
            self.jobs.pop(job["id"], None)
            watcher.deleteLater()
            self.cancel_button.setVisible(bool(self.jobs))
            if value["status"] == "failed":
                self._say(document_id, f"Action failed: {value.get('error', 'Unknown error')}")
            else:
                finished(value)
            self.changed.emit(document_id)
        watcher.finished.connect(done)
        return watcher

    def _progress(self, document_id, job):
        progress = job.get("progress") or {}
        provider = PROVIDERS.get(progress.get("provider"), progress.get("provider") or "References")
        count = f" {progress.get('completed', 0)} / {progress['total']}" if progress.get("total") else ""
        self._say(document_id, f"Searching {provider}…{count}")

    def cancel(self):
        for watcher in self.jobs.values():
            watcher.cancel()
        self.status.setText("Cancellation requested; the current reference may finish first.")

    def acquire(self, document_id, ids, open_id=None):
        refs = self.data.get(document_id, [])
        allowed = {str(ref["id"]) for ref in refs if not ref.get("local_document_id") and not ref.get("legacy")}
        ids = [str(value) for value in ids if str(value) in allowed]
        if not ids:
            return False
        if self.jobs:
            self._say(document_id, "Wait for the current reference task to finish.")
            return False
        try:
            job = self.workspace.download_references(document_id, ids)
        except Exception as error:
            self._say(document_id, f"Could not start acquisition: {error}")
            return False
        self._say(document_id, "Searching local library first…")
        def finished(value):
            results = (value.get("result") or {}).get("references", [])
            counts = Counter(item.get("status", "failed") for item in results)
            parts = [f"{count} {RESULTS.get(status, status).lower()}" for status, count in counts.items()]
            if len(results) < len(ids):
                parts.append(f"{len(ids) - len(results)} not searched")
            self._say(document_id, ("Search cancelled: " if value["status"] == "cancelled" else "Search finished: ")
                      + (" · ".join(parts) or "no results"))
            match = next((item for item in results if str(item.get("id")) == str(open_id) and item.get("document_id")), None)
            if match:
                self._open_for_source(document_id, match["document_id"])
        self._watch(document_id, job, finished)
        return True

    def _task(self, document_id, start, finished):
        try:
            self._watch(document_id, start(), lambda value: finished(value.get("result") or {}))
        except Exception as error:
            self._say(document_id, str(error))

    def check_folder(self, document_id, ref):
        if not self.workspace.get_settings().inbox:
            self._say(document_id, "No downloads folder is configured. Set it in Preferences.")
            self.preferences_requested.emit("inbox")
            return
        self._say(document_id, "Checking the downloads folder…")
        self._task(document_id, lambda: self.workspace.check_folder(document_id, ref["id"]),
                   lambda result: self._folder_result(document_id, ref, result))

    def _folder_result(self, document_id, ref, result):
        self._say(document_id, result.get("summary") or "Folder check finished.")
        if result.get("status") == "linked" and result.get("document_id"):
            self._open_for_source(document_id, result["document_id"])
        elif result.get("status") == "not_configured":
            self.preferences_requested.emit("inbox")
        elif result.get("candidates"):
            dialog = QDialog(self.window)
            dialog.setWindowTitle("Choose a downloaded PDF")
            dialog.resize(620, 330)
            column = QVBoxLayout(dialog)
            column.addWidget(QLabel(result.get("summary", "Choose the correct PDF."), wordWrap=True))
            table = QTreeWidget(headerLabels=["File", "Size", "Modified"], rootIsDecorated=False)
            for candidate in result["candidates"]:
                item = QTreeWidgetItem([candidate["name"], f"{candidate.get('size', 0) / 1024:.0f} KB", str(candidate.get("modified", ""))[:10]])
                item.setData(0, Qt.UserRole, candidate["name"])
                table.addTopLevelItem(item)
            table.setColumnWidth(0, 340)
            column.addWidget(table)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Ok).setText("Link selected PDF")
            buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            table.itemSelectionChanged.connect(lambda: buttons.button(QDialogButtonBox.Ok).setEnabled(table.currentItem() is not None))
            buttons.accepted.connect(lambda: (self.link_folder_file(document_id, ref, table.currentItem().data(0, Qt.UserRole)), dialog.accept()))
            buttons.rejected.connect(dialog.reject)
            column.addWidget(buttons)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.show()
            self._folder_dialog = dialog

    def link_folder_file(self, document_id, ref, name):
        self._task(document_id, lambda: self.workspace.submit(lambda job, cancel: self.workspace.link_file(document_id, ref["id"], name)),
                   lambda result: self._open_for_source(document_id, result["local_document_id"]) if result.get("local_document_id") else None)

    def link(self, document_id, ref, target):
        if target is not None and str(target) == str(document_id):
            self._say(document_id, "A reference cannot link to its source document.")
            return
        self._task(document_id, lambda: self.workspace.submit(lambda job, cancel: references.link(self.workspace.library, document_id, ref["id"], target)),
                   lambda result: self._say(document_id, "PDF link reset." if target is None else "PDF link saved."))

    def choose_link(self, document_id, ref):
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Link local PDF — verify the paper")
        dialog.resize(760, 420)
        column = QVBoxLayout(dialog)
        column.addWidget(QLabel(ref.get("raw") or ref.get("title") or "Choose the cited paper.", wordWrap=True))
        search = QLineEdit(placeholderText="Search title, author, year or DOI")
        table = QTreeWidget(headerLabels=["Title", "Authors", "Year", "Match"], rootIsDecorated=False)
        table.setColumnWidth(0, 370)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Link selected PDF")
        buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        suggestions = {candidate["id"] for candidate in ref.get("local_candidates") or []}
        def fill():
            table.clear()
            documents = self.workspace.library.list_documents(search.text())
            for document in sorted(documents, key=lambda doc: doc["id"] not in suggestions):
                if document["id"] == document_id:
                    continue
                item = QTreeWidgetItem([document.get("title") or document["filename"], document.get("authors") or "",
                                        str(document.get("year") or ""), "Possible match — verify" if document["id"] in suggestions else ""])
                item.setData(0, Qt.UserRole, document["id"])
                table.addTopLevelItem(item)
        typing = QTimer(dialog, singleShot=True, interval=150, timeout=fill)
        search.textChanged.connect(typing.start)
        table.itemSelectionChanged.connect(lambda: buttons.button(QDialogButtonBox.Ok).setEnabled(table.currentItem() is not None))
        buttons.accepted.connect(lambda: (self.link(document_id, ref, table.currentItem().data(0, Qt.UserRole)), dialog.accept()))
        buttons.rejected.connect(dialog.reject)
        for widget in (search, table, buttons):
            column.addWidget(widget)
        fill()
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        self._link_dialog = dialog

    def repair(self, document_id):
        self._say(document_id, "Checking earlier downloads…")
        self._task(document_id, lambda: self.workspace.repair_downloads(document_id),
                   lambda result: self._say(document_id, f"Checked earlier downloads: {len(result.get('documents') or [])} results."))

    def resolve(self, document_id, ref):
        self._say(document_id, "Resolving DOI metadata…")
        self._task(document_id, lambda: self.workspace.resolve_reference(document_id, ref["id"]),
                   lambda result: self._say(document_id, str(result.get("title") or result.get("summary") or "DOI metadata lookup finished.")))

    def show_reference(self, document_id, ref):
        self.expanded.add((document_id, str(ref["id"])))
        self.changed.emit(document_id)

    def card_actions(self, document_id, ref, parent=None):
        widget = CardActions(parent)
        column = QVBoxLayout(widget)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        status = QLabel(reference_status(ref), wordWrap=True)
        status.setStyleSheet(f"color: {status_tone(ref)}")
        column.addWidget(status)
        if ref.get("local_conflict"):
            column.addWidget(QLabel(ref["local_conflict"], wordWrap=True))
        details = retrieval_details(ref)
        if details:
            label = QLabel(details, wordWrap=True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            column.addWidget(label)
        grid = QGridLayout()
        column.addLayout(grid)
        actions = []
        local = ref.get("local_document_id")
        if local and not ref.get("local_invalid"):
            actions.append(("Open PDF", lambda: self.open_requested.emit(local)))
        if not local:
            actions.append(("Find open access", lambda: self.acquire(document_id, [ref["id"]], ref["id"])))
        actions.append(("Change PDF" if local or ref.get("local_invalid") or ref.get("local_missing") else "Link local PDF",
                        lambda: self.choose_link(document_id, ref)))
        if ref.get("manual_link"):
            actions.append(("Reset PDF link", lambda: self.link(document_id, ref, None)))
        if not local:
            actions.append(("Check folder", lambda: self.check_folder(document_id, ref)))
        for index, (text, callback) in enumerate(actions):
            button = QPushButton(text)
            button.clicked.connect(callback)
            button.setEnabled(not (ref.get("legacy") and text == "Find open access"))
            grid.addWidget(button, index // 2, index % 2)
        if not local:
            extra = QWidget()
            more = QGridLayout(extra)
            more.setContentsMargins(0, 0, 0, 0)
            links = search_links(ref)
            doi = ref.get("doi")
            if doi:
                links.insert(0, ("Publisher / DOI", "https://doi.org/" + quote(doi, safe="/")))
            result = current_retrieval(ref)
            pdf = web_url(result.get("pdf_url"))
            if pdf:
                links.insert(0, ("Open PDF in browser", pdf))
            proxy = self.workspace.get_settings().proxy
            for label, target in list(links):
                if label in ("Publisher / DOI", "Open PDF in browser") and (url := proxy_url(proxy, target)):
                    links.append((label + " via library", url))
            for index, (text, url) in enumerate(links):
                button = QPushButton(text)
                button.setToolTip(url)
                button.clicked.connect(lambda checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
                more.addWidget(button, index // 2, index % 2)
            if doi:
                button = QPushButton("Resolve DOI metadata")
                button.clicked.connect(lambda: self.resolve(document_id, ref))
                more.addWidget(button, (len(links) + 1) // 2, 0, 1, 2)
            toggle = QPushButton("Browser searches and links", checkable=True)
            key = (document_id, str(ref["id"]))
            toggle.setChecked(key in self.expanded)
            extra.setVisible(toggle.isChecked())
            def expand(checked):
                self.expanded.add(key) if checked else self.expanded.discard(key)
                extra.setVisible(checked)
                widget.layout_changed.emit()
            toggle.toggled.connect(expand)
            column.addWidget(toggle)
            column.addWidget(extra)
        return widget

    def close(self):
        self._closed = True
        self.pending.clear()
        for watcher in self.jobs.values():
            watcher.stop()
        for dialog in list(self.dialogs.values()):
            dialog.close()
