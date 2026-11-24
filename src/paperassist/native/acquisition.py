"""Reference acquisition and inline actions, using the existing Workspace jobs."""
import json
import math
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from PySide6.QtCore import QObject, QPointF, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPalette, QTextLayout, QTextOption
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QStyle, QStyleOptionButton,
                               QStyleOptionFocusRect, QStylePainter, QTreeWidgetItem, QVBoxLayout, QWidget)

from paperassist import references
from paperassist.paths import write_json_atomic
from paperassist.settings import PROVIDER_NAMES

from .dialogs import PickerDialog, setup_dialog
from .jobs import JobWatcher
from .theme import FlowLayout, reference_palette, reference_tone, set_props

PROVIDERS = {**PROVIDER_NAMES, "local": "Local library", "folder": "downloads folder"}
MAX_BROWSER_TABS = 10
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
    return reference_palette(ref)[2]


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


def blocked_urls(refs):
    """Deduplicated public web PDF URLs for unresolved, non-legacy blocked references."""
    urls = []
    for ref in refs or ():
        if ref.get("local_document_id") or ref.get("legacy"):
            continue
        result = current_retrieval(ref)
        if result.get("status") == "blocked" and (url := web_url(result.get("pdf_url"))):
            urls.append(url)
    return list(dict.fromkeys(urls))


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
        write_json_atomic(self.path, stored)


class ReferenceCheckBox(QCheckBox):
    """Keep native checkbox semantics while allowing complete, arbitrarily long titles to wrap."""
    def __init__(self, text):
        super().__init__(text)
        self.setAccessibleName(text.replace('&&', '&'))
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _layout(self, width):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.rect = QRect(0, 0, width, super().sizeHint().height())
        content = self.style().subElementRect(QStyle.SE_CheckBoxContents, option, self)
        layout = QTextLayout(self.text().replace('&&', '&'), self.font())
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(text_option)
        height = 0
        layout.beginLayout()
        while (line := layout.createLine()).isValid():
            line.setLineWidth(max(1, content.width()))
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        return layout, content, height

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        _, content, height = self._layout(width)
        return max(super().sizeHint().height(), math.ceil(height + content.top() * 2))

    def sizeHint(self):
        width = min(super().sizeHint().width(), 400)
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self):
        return QSize(80, super().sizeHint().height())

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ''
        option.rect.setHeight(super().sizeHint().height())
        option.state &= ~QStyle.State_HasFocus
        painter = QStylePainter(self)
        painter.drawControl(QStyle.CE_CheckBox, option)
        layout, content, _ = self._layout(self.width())
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        painter.setPen(self.palette().color(group, QPalette.WindowText))
        layout.draw(painter, QPointF(content.topLeft()))
        if self.hasFocus():
            focus = QStyleOptionFocusRect()
            focus.initFrom(self)
            focus.rect = self.rect().adjusted(0, 0, -1, -1)
            painter.drawPrimitive(QStyle.PE_FrameFocusRect, focus)


class AcquisitionDialog(QDialog):
    def __init__(self, controller, document_id, refs):
        super().__init__(controller.window)
        self.setAttribute(Qt.WA_DeleteOnClose)  # a closed offer would otherwise keep a checkbox per reference alive
        self.controller, self.document_id, self.refs, self.boxes, self.rows = controller, document_id, list(refs), [], {}
        self.resize(680, 560)
        column, _ = setup_dialog(
            self, "Acquire references",
            subtitle="Find open-access PDFs for this paper's references.",
        )
        column.addWidget(QLabel(provider_summary(controller.workspace.get_settings()), objectName="muted-note", wordWrap=True))

        row = QHBoxLayout()
        row.setSpacing(8)
        self.count = QLabel(objectName="muted-note")
        for label, selected in (("Select all", True), ("Clear all", False)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=selected: self.select_all(value))
            row.addWidget(button)
        row.addWidget(self.count, 1)
        column.addLayout(row)

        content = QWidget()
        scroll = QScrollArea(objectName="dialog-scroll", widgetResizable=True)
        scroll.setAccessibleName("References to acquire")
        entries = QVBoxLayout(content)
        entries.setContentsMargins(0, 0, 0, 0)
        entries.setSpacing(0)
        self.entries = entries
        self._populate(refs, controller.selections.load(document_id))
        scroll.setWidget(content)
        column.addWidget(scroll, 1)

        self.message = QLabel(wordWrap=True)
        self.message.setProperty("tone", "error")
        column.addWidget(self.message)

        self.manual_bar = QWidget()
        manual = FlowLayout(self.manual_bar, margin=0, h_spacing=8, v_spacing=6)
        self.open_blocked_button = QPushButton("Open blocked in browser")
        self.open_blocked_button.setToolTip("Open blocked open-access PDF links in your browser")
        self.check_folder_button = QPushButton("Check folder")
        self.check_folder_button.setToolTip("Verify and link matching PDFs from your manual downloads folder")
        manual.addWidget(self.open_blocked_button)
        manual.addWidget(self.check_folder_button)
        self.open_blocked_button.clicked.connect(lambda: self.controller.open_blocked(self.document_id))
        self.check_folder_button.clicked.connect(self._check_folder)
        column.addWidget(self.manual_bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.all_button = QPushButton("Acquire all missing")
        self.all_button.setProperty("primary", True)
        self.selected_button = QPushButton("Acquire selected")
        continue_button = QPushButton("Continue reading")
        for button in (self.all_button, self.selected_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(continue_button)
        self.all_button.clicked.connect(lambda: self.start(True))
        self.selected_button.clicked.connect(lambda: self.start(False))
        continue_button.clicked.connect(self.close)
        column.addLayout(buttons)
        self.update_count()

    def _populate(self, refs, saved):
        for ref in refs:
            local = bool(ref.get("local_document_id"))
            tone = reference_tone(ref)
            frame = QFrame(objectName="acquire-row")
            frame.setProperty("tone", "local" if local and not ref.get("local_conflict") else tone)
            item_layout = QVBoxLayout(frame)
            item_layout.setContentsMargins(10, 8, 10, 8)
            item_layout.setSpacing(3)
            text = f"{ref.get('label') or ''} {ref.get('title') or ref.get('raw') or 'Unidentified reference'}"
            box = ReferenceCheckBox(text.strip().replace("&", "&&"))
            box.setToolTip(text)
            box.setEnabled(not local and not ref.get("legacy"))
            box.setChecked(box.isEnabled() and str(ref["id"]) in saved)
            box.setProperty("reference_id", str(ref["id"]))
            box.toggled.connect(self.selection_changed)
            item_layout.addWidget(box)
            status = QLabel("In library — no download needed" if local else reference_status(ref), wordWrap=True)
            status.setProperty("tone", tone)
            item_layout.addWidget(status)
            self.entries.addWidget(frame)
            self.boxes.append(box)
            self.rows[str(ref["id"])] = (box, status)
        if not refs:
            empty = QLabel("No references were found in this paper.", objectName="muted-note", wordWrap=True)
            empty.setContentsMargins(12, 12, 12, 12)
            self.entries.addWidget(empty)
        self.entries.addStretch()

    def selected(self):
        return [box.property("reference_id") for box in self.boxes if box.isEnabled() and box.isChecked()]

    def update_count(self):
        values = self.selected()
        has_missing = any(box.isEnabled() for box in self.boxes)
        blocked = blocked_urls(self.refs)
        self.count.setText(f"{len(values)} selected")
        self.selected_button.setEnabled(bool(values))
        self.all_button.setEnabled(has_missing)
        self.check_folder_button.setEnabled(has_missing)
        self.open_blocked_button.setText(f"Open {len(blocked)} blocked in browser" if blocked else "Open blocked in browser")
        self.open_blocked_button.setVisible(bool(blocked))
        self.manual_bar.setVisible(has_missing or bool(blocked))
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
        self.refs = list(refs)
        selected = set(self.selected())
        while self.entries.count():
            if widget := self.entries.takeAt(0).widget():
                widget.hide()
                widget.deleteLater()
        self.boxes, self.rows = [], {}
        self._populate(refs, selected)
        self.selection_changed()

    def _check_folder(self):
        if self.controller.check_folder_batch(self.document_id):
            self.close()

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
        self.data, self.pending, self.dialogs, self.jobs, self.expanded = {}, set(), {}, {}, set()
        self.status = QLabel(wordWrap=True)
        self.cancel_button = QPushButton("Cancel search")
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(self.cancel)
        self.window.statusBar().addWidget(self.status, 1)
        self.window.statusBar().addPermanentWidget(self.cancel_button)

    def evict(self, document_id):
        self.data.pop(document_id, None)
        self.pending.discard(document_id)
        self.expanded = {item for item in self.expanded if item[0] != document_id}

    def set_reference_data(self, document_id, refs, status="ready"):
        if self._closed:
            return
        self.data[document_id] = refs
        if document_id in self.dialogs:
            self.dialogs[document_id].update_references(refs)
        if document_id in self.pending:
            self.pending.remove(document_id)
            QTimer.singleShot(0, self, lambda: self._offer_if_active(document_id))

    def main_opened(self, document_id):
        if self._closed or self.workspace is None or document_id is None:
            return
        if document_id in self.data:
            QTimer.singleShot(0, self, lambda: self._offer_if_active(document_id))
        else:
            self.pending.add(document_id)

    def _offer_if_active(self, document_id):
        if self._closed:
            return
        if hasattr(self.window, "tab"):
            tab = self.window.tab()
            if tab is None or tab.main.view.doc.source.document_id != document_id:
                return
        self.offer(document_id)

    def offer(self, document_id, refs=None):
        if self._closed or self.workspace is None:
            return
        if refs is not None:
            self.data[document_id] = refs
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
        self.window.statusBar().clearMessage()
        self.status.setText(text)
        self.status.setToolTip(text)

    def _open_for_source(self, document_id, target):
        if hasattr(self.window, "tab"):
            tab = self.window.tab()
            if tab is None or tab.main.view.doc.source.document_id != document_id:
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
        if self.jobs:
            self._say(document_id, "Wait for the current reference task to finish.")
            return
        try:
            self._watch(document_id, start(), lambda value: finished(value.get("result") or {}))
        except Exception as error:
            self._say(document_id, str(error))

    def open_blocked(self, document_id):
        """Open blocked open-access PDF links in the user's browser strictly on explicit click."""
        urls = blocked_urls(self.data.get(document_id, []))
        if not urls:
            self._say(document_id, "No blocked PDF links to open.")
            return 0
        batch = urls[:MAX_BROWSER_TABS]
        for url in batch:
            QDesktopServices.openUrl(QUrl(url))
        shown = f"{len(batch)} of {len(urls)}" if len(urls) > len(batch) else str(len(batch))
        plural = "" if len(batch) == 1 and len(urls) == 1 else "s"
        self._say(document_id, f"Opened {shown} PDF link{plural} in your browser. Save to your downloads folder, then click Check folder.")
        return len(batch)

    def check_folder_batch(self, document_id, ids=None):
        if not self.workspace.get_settings().inbox:
            self._say(document_id, "No downloads folder is configured. Set it in Preferences.")
            self.preferences_requested.emit("inbox")
            return False
        if self.jobs:
            self._say(document_id, "Wait for the current reference task to finish.")
            return False
        self._say(document_id, "Checking the downloads folder…")
        self._task(document_id, lambda: self.workspace.check_folder_batch(document_id, ids),
                   lambda result: self._batch_folder_result(document_id, result))
        return bool(self.jobs)

    def _batch_folder_result(self, document_id, result):
        self._say(document_id, result.get("summary") or "Folder check finished.")
        if result.get("status") == "not_configured":
            self.preferences_requested.emit("inbox")

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
            dialog = PickerDialog(
                self.window, "Choose a downloaded PDF",
                subtitle=result.get("summary", "Choose the correct PDF."),
                headers=["File", "Size", "Modified"],
                table_name="Downloaded PDFs",
                confirm_text="Link selected PDF",
                size=(640, 360),
                on_choose=lambda name: self.link_folder_file(document_id, ref, name),
            )
            for candidate in result["candidates"]:
                item = QTreeWidgetItem([candidate["name"], f"{candidate.get('size', 0) / 1024:.0f} KB", str(candidate.get("modified", ""))[:10]])
                item.setData(0, Qt.UserRole, candidate["name"])
                dialog.table.addTopLevelItem(item)
            dialog.table.setColumnWidth(0, 340)
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
        dialog = PickerDialog(
            self.window, "Link local PDF — verify the paper",
            heading="Link local PDF",
            subtitle=ref.get("raw") or ref.get("title") or "Choose the cited paper.",
            headers=["Title", "Authors", "Year", "Match"],
            table_name="Local PDFs",
            confirm_text="Link selected PDF",
            size=(760, 440),
            searchable=True,
            search_placeholder="Search title, author, year or DOI",
            search_name="Search local PDFs",
            on_choose=lambda target: self.link(document_id, ref, target),
        )
        dialog.table.setColumnWidth(0, 370)
        candidates = ref.get("local_candidates") or []
        suggestions = {candidate["id"]: rank for rank, candidate in enumerate(candidates)}

        def fill():
            dialog.table.clear()
            documents = self.workspace.library.list_documents(dialog.search.text())
            for document in sorted(documents, key=lambda doc: suggestions.get(doc["id"], len(candidates))):
                if document["id"] == document_id:
                    continue
                item = QTreeWidgetItem([document.get("title") or document["filename"], document.get("authors") or "",
                                        str(document.get("year") or ""), "Possible match — verify" if document["id"] in suggestions else ""])
                item.setData(0, Qt.UserRole, document["id"])
                dialog.table.addTopLevelItem(item)

        typing = QTimer(dialog, singleShot=True, interval=150, timeout=fill)
        dialog.search.textChanged.connect(typing.start)
        fill()
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

    def card_actions(self, document_id, ref, parent=None):
        widget = CardActions(parent)
        column = QVBoxLayout(widget)
        column.setContentsMargins(0, 2, 0, 0)
        column.setSpacing(5)
        status = QLabel(reference_status(ref), wordWrap=True)
        set_props(status, tone=reference_tone(ref), strong=True)
        column.addWidget(status)
        if ref.get("local_conflict"):
            conflict = QLabel(ref["local_conflict"], wordWrap=True)
            conflict.setProperty("tone", "error")
            column.addWidget(conflict)
        details = retrieval_details(ref)
        if details:
            label = QLabel(details, objectName="muted-note", wordWrap=True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            column.addWidget(label)

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

        primary_flow = FlowLayout(margin=0, h_spacing=12, v_spacing=4)
        for text, callback in actions:
            button = QPushButton(text)
            button.setProperty("link", True)
            button.clicked.connect(callback)
            button.setEnabled(not (ref.get("legacy") and text == "Find open access"))
            primary_flow.addWidget(button)
        column.addLayout(primary_flow)

        if not local:
            extra = QWidget()
            more = FlowLayout(extra, margin=0, h_spacing=12, v_spacing=4)
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
            for text, url in links:
                button = QPushButton(text)
                button.setProperty("link", True)
                button.setToolTip(url)
                button.clicked.connect(lambda checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
                more.addWidget(button)
            if doi:
                button = QPushButton("Resolve DOI metadata")
                button.setProperty("link", True)
                button.clicked.connect(lambda: self.resolve(document_id, ref))
                more.addWidget(button)

            toggle_row = FlowLayout(margin=0, h_spacing=8, v_spacing=2)
            toggle = QPushButton("Browser searches and links", checkable=True)
            toggle.setProperty("link", True)
            key = (document_id, str(ref["id"]))
            toggle.setChecked(key in self.expanded)
            extra.setVisible(toggle.isChecked())

            def expand(checked):
                self.expanded.add(key) if checked else self.expanded.discard(key)
                extra.setVisible(checked)
                widget.layout_changed.emit()
            toggle.toggled.connect(expand)
            toggle_row.addWidget(toggle)
            column.addLayout(toggle_row)
            column.addWidget(extra)
        return widget

    def close(self):
        self._closed = True
        self.pending.clear()
        for watcher in self.jobs.values():
            watcher.stop()
        for dialog in list(self.dialogs.values()):
            dialog.close()
