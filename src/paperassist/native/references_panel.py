"""The references column: the main paper's bibliography, filterable, with local availability."""
import unicodedata
import weakref
from html import escape

from PySide6.QtCore import QRect, QSize, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QProgressBar, QPushButton, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QVBoxLayout, QWidget)

from paperassist import references

from .acquisition import reference_status, status_tone
from .jobs import JobWatcher
from .citations import (citation_matches, citation_position, locate_citations, next_citation_index,
                        reading_position, reveal_citation)

SHOWN_CHARS = 240  # one bibliography entry can run to a page; the tooltip keeps the whole text
PAD = 6
TASKS = set()  # threads outlive a closed panel until their independent read has finished


class ReadTask(QThread):
    done = Signal(object)

    def __init__(self, operation, context):
        super().__init__()
        self.operation, self.context = operation, context
        self.result, self.error = None, None
        TASKS.add(self)
        self.finished.connect(lambda: TASKS.discard(self))
        self.finished.connect(self.deleteLater)

    def run(self):
        try:
            self.result = self.operation()
        except Exception as error:
            self.error = str(error) or type(error).__name__
        self.done.emit(self)


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


class CardDelegate(QStyledItemDelegate):
    """Paints the collapsed cards from their three text lines, so a long bibliography owns no widget trees."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def _lines(self, index):
        title, count, status = index.data().split("\n")
        return title, count, status

    def _heights(self, font, width, title):
        metrics = QFontMetrics(font)
        wrapped = metrics.boundingRect(QRect(0, 0, width, 10000), Qt.TextWordWrap, title).height()
        return wrapped, metrics.lineSpacing()

    def sizeHint(self, option, index):
        if (expanded := index.data(Qt.SizeHintRole)) is not None:  # set while the card holds its widgets
            return expanded
        width = max(self.view.viewport().width() - 2 * PAD, 40)
        title, _, _ = self._lines(index)
        wrapped, line = self._heights(option.font, width, title)
        return QSize(width + 2 * PAD, wrapped + 2 * line + 2 * PAD + 4)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)  # background, selection and focus frame
        selected = bool(opt.state & QStyle.State_Selected)
        palette = option.palette
        title, count, status = self._lines(index)
        area = option.rect.adjusted(PAD, PAD, -PAD, -PAD)
        wrapped, line = self._heights(option.font, area.width(), title)
        painter.save()
        painter.setFont(option.font)
        rows = ((title, palette.link().color(), wrapped), (count, palette.text().color(), line),
                (status, QColor(index.data(Qt.UserRole + 2)), line))
        top = area.top()
        for text, color, height in rows:
            painter.setPen(palette.highlightedText().color() if selected else color)
            painter.drawText(QRect(area.left(), top, area.width(), height), Qt.TextWordWrap, text)
            top += height + 2
        painter.restore()


class Card(QWidget):
    """The one expanded card: title link, citation count and the acquisition actions."""
    collapse_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.collapse_requested.emit()
        else:
            super().keyPressEvent(event)


class ReferencesPanel(QWidget):
    open_requested = Signal(int)  # id of a reference's PDF that is in the library
    references_loaded = Signal(int, object)
    refs_changed = Signal(int, object)
    reveal_requested = Signal()  # a citation needs the right column, possibly in Comments mode

    def __init__(self, workspace):
        super().__init__()
        self.workspace, self.document_id, self.refs = workspace, None, []
        self.view, self.acquisition = None, None
        self.loaded, self.stored = {}, {}
        self.series, self.located = weakref.WeakKeyDictionary(), weakref.WeakKeyDictionary()
        self.loading, self.stepping = set(), set()
        self.reading = set()  # library reads in flight; closing waits for them, they write into the library folder
        self.payload_status = "not_analyzed"
        self.filters, self.analyzing = {}, {}  # filter text and running jobs, by document id
        self.filter = QLineEdit(placeholderText="Filter references", clearButtonEnabled=True)
        self.filter.setAccessibleName("Filter references")
        self.status = QLabel(wordWrap=True)
        self.analysis_note = QLabel("Detected citations only; bibliography and counts may be incomplete.", wordWrap=True)
        self.analysis_note.hide()
        self.progress = QProgressBar(maximum=0, textVisible=False, maximumHeight=6)  # no progress figures: busy only
        self.retry = QPushButton("Analyze again")
        self.acquire = QPushButton("Acquire references")
        self.repair = QPushButton("Repair downloads")
        self.acquire.clicked.connect(lambda: self.acquisition and self.acquisition.offer(self.document_id, self.refs))
        self.repair.clicked.connect(lambda: self.acquisition and self.acquisition.repair(self.document_id))
        self.choices = QWidget()
        self.choice_layout = QVBoxLayout(self.choices)
        self.choice_layout.setContentsMargins(0, 0, 0, 0)
        self.choices.hide()
        self.list = QListWidget(horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOff, resizeMode=QListView.Adjust,
                                verticalScrollMode=QListWidget.ScrollPerPixel)
        self.list.setAccessibleName("References")
        self.list.setItemDelegate(CardDelegate(self.list))
        self.expanded = None  # id of the single card that owns widgets
        column = QVBoxLayout(self)
        column.setContentsMargins(6, 0, 6, 6)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.retry)
        column.addWidget(self.filter)
        column.addLayout(row)
        column.addWidget(self.analysis_note)
        actions = QHBoxLayout()
        actions.addWidget(self.acquire)
        actions.addWidget(self.repair)
        column.addLayout(actions)
        column.addWidget(self.progress)
        column.addWidget(self.choices)
        column.addWidget(self.list, 1)
        self.filter.textChanged.connect(self._filtered)
        self.retry.clicked.connect(lambda: self.analyze(self.document_id))
        self.list.itemClicked.connect(self._expand)
        self.list.itemActivated.connect(lambda item: self._expand(item, focus=True))  # Enter, from the keyboard
        self.list.itemDoubleClicked.connect(self._open)
        self._set_busy(False)
        self.acquire.hide()
        self.repair.hide()

    def set_acquisition(self, acquisition):
        self.acquisition = acquisition
        acquisition.changed.connect(self.reload)
        self.acquire.show()
        self.repair.show()

    def bind_view(self, view):
        self.view = view
        if view is not None:
            view.internal_link_handler = lambda link, target=weakref.ref(view): self._citation_link(target(), link)

    def reload(self, document_id):
        self.loaded.pop(document_id, None)
        if document_id == self.document_id:
            self.show_for(document_id)

    # ---- what is shown ------------------------------------------------------------------
    def show_for(self, document_id):
        """Show the bibliography of a library paper; anything else gets an explanation."""
        self.filters[self.document_id] = self.filter.text()
        if document_id != self.document_id:
            self.expanded = None
        self.document_id = document_id
        self.choices.hide()
        self.filter.blockSignals(True)
        self.filter.setText(self.filters.get(document_id, ""))
        self.filter.blockSignals(False)
        self.refs = []
        self.list.clear()
        self.retry.hide()
        if document_id is None:
            self.analysis_note.hide()
            return self._message("References are available for papers in the library.")
        if document_id in self.analyzing:
            return self._message("Analyzing references…", busy=True)
        try:
            payload = references._load(self.workspace.library, document_id)
        except Exception as error:  # a broken PDF or catalog must leave the column usable
            return self._message(f"Could not read the references: {error}", retry=True)
        if (payload["status"] == "not_analyzed" or payload.get("extraction_version") != references.EXTRACTION_VERSION
                or payload.get("destination_version") != references.DESTINATION_VERSION):
            return self.analyze(document_id)
        if document_id in self.loaded and payload == self.stored.get(document_id):
            cached = self.loaded[document_id]
            self.refs, self.payload_status = cached["references"], cached["status"]
            self._fill()
            self.references_loaded.emit(document_id, self.refs)
            return
        self.stored[document_id] = payload
        self.refs = payload["references"]
        self.payload_status = payload["status"]
        self._fill()
        if document_id not in self.loading:
            self.loading.add(document_id)
            library = self.workspace.library
            def read():
                return references.get(library, document_id)
            self._start(read, ("load", document_id))

    def _start(self, operation, context):
        task = ReadTask(operation, context)
        task.done.connect(self._read_done)
        if context[0] == "load":
            self.reading.add(task)
        task.start()

    def _read_done(self, task):
        self.reading.discard(task)
        kind, document_id, *extra = task.context
        if kind == "load":
            self.loading.discard(document_id)
            if task.error:
                if document_id == self.document_id:
                    self._message(f"Could not read the references: {task.error}", retry=True)
                return
            payload = task.result
            self.loaded[document_id] = payload
            if document_id == self.document_id and (payload["references"], payload["status"]) != (self.refs, self.payload_status):
                self.refs, self.payload_status = payload["references"], payload["status"]
                self._fill()
            self.references_loaded.emit(document_id, payload["references"])
            self.refs_changed.emit(document_id, payload["references"])
        else:
            target, ref = extra
            view = target()
            if view is None:
                return
            self.stepping.discard(view)
            if task.error:
                if view is self.view:
                    self._message(f"Citations could not be read from this PDF: {task.error}")
                return
            self.located.setdefault(view, {})[ref["id"]] = task.result
            if view is self.view:
                self._show_citation(view, ref, task.result)

    def _message(self, text, busy=False, retry=False):
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self._set_busy(busy)
        self.retry.setVisible(retry)

    def _set_busy(self, busy):
        self.progress.setVisible(busy)

    def _text(self, ref):
        text = " ".join((ref.get("title") or ref.get("raw") or "Unidentified reference").split())
        label = f"[{ref['label']}] " if str(ref.get("label")).isdigit() else ""  # author-year labels repeat the entry
        return f"{label}{text[:SHOWN_CHARS]}\n{self._count(ref)}\n{reference_status(ref)}"

    def _fill(self):
        if self.acquisition:
            self.acquisition.set_reference_data(self.document_id, self.refs, self.payload_status)
        matches = reference_matcher(self.filter.text())
        self.list.clear()
        self.analysis_note.setVisible(self.payload_status == "partial")
        shown = [ref for ref in self.refs if matches(ref)]
        shown.sort(key=lambda ref: -(ref["citation_count"] if ref.get("citation_count") is not None else -1))
        for ref in shown:
            item = QListWidgetItem(self._text(ref))
            item.setToolTip(ref.get("raw") or ref.get("title") or "")
            item.setData(Qt.UserRole, ref.get("local_document_id"))
            item.setData(Qt.UserRole + 1, ref["id"])
            item.setData(Qt.UserRole + 2, status_tone(ref))
            self.list.addItem(item)
        if self.expanded is not None:
            item = self._item(self.expanded)
            if item:
                self._expand(item)
            else:
                self.expanded = None
        if not self.refs:
            self._message("No references were found in this paper.", retry=True)
        elif not shown:
            self._message("No matching references.")
        else:
            self._message(f"{len(shown)} of {len(self.refs)} references" if len(shown) != len(self.refs) else f"{len(shown)} references")
            if self.payload_status == "partial":
                self.status.setToolTip("Detected citations only; bibliography and counts may be incomplete.")
        self.acquire.setEnabled(self.document_id is not None and bool(self.refs))
        self.repair.setEnabled(self.document_id is not None)

    def _item(self, ref_id):
        return next((self.list.item(i) for i in range(self.list.count())
                     if self.list.item(i).data(Qt.UserRole + 1) == ref_id), None)

    def _ref(self, ref_id):
        return next((ref for ref in self.refs if ref["id"] == ref_id), None)

    def _expand(self, item, focus=False):
        """Only the card being worked on gets buttons; the others stay painted text."""
        ref = self._ref(item.data(Qt.UserRole + 1))
        if ref is None:
            return
        if self.expanded not in (None, ref["id"]) and (previous := self._item(self.expanded)):
            self._collapse(previous)
        self.expanded = ref["id"]
        self.list.setCurrentItem(item)
        if self.list.itemWidget(item) is None:
            card = Card()
            card.setAutoFillBackground(True)
            card.collapse_requested.connect(self._collapse_current)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(PAD, 5, PAD, 8)
            card.title = QLabel(f'<a href="open">{escape(item.text().split(chr(10))[0])}</a>', wordWrap=True)
            card.title.setToolTip(item.toolTip())
            card.title.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
            card.title.linkActivated.connect(lambda _=False, target=ref: self._reference_open(target))
            layout.addWidget(card.title)
            card.count = QPushButton(self._count(ref))
            card.count.setAccessibleName("Go to the next citation in the text")
            card.count.clicked.connect(lambda _=False, target=ref: self.step(target))
            layout.addWidget(card.count)
            if self.acquisition:
                actions = self.acquisition.card_actions(self.document_id, ref, card)
                layout.addWidget(actions)
                actions.layout_changed.connect(lambda: self._fit(item))
            self.list.setItemWidget(item, card)
            self._fit(item)
        if focus:
            self.list.itemWidget(item).title.setFocus()

    def _fit(self, item):
        card = self.list.itemWidget(item)
        width = max(self.list.viewport().width(), 40)
        card.ensurePolished()  # unpolished buttons report smaller hints and the card would be clipped
        item.setSizeHint(QSize(width, max(card.layout().totalHeightForWidth(width), card.sizeHint().height())))
        self.list.doItemsLayout()

    def _collapse(self, item):
        self.list.removeItemWidget(item)
        item.setSizeHint(QSize())  # back to the delegate's size
        self.list.doItemsLayout()

    def _collapse_current(self):
        if self.expanded is not None and (item := self._item(self.expanded)):
            self._collapse(item)
            self.list.setFocus()
        self.expanded = None

    def _update_counts(self):
        """Stepping only changes count lines; nothing is rebuilt, so keyboard focus stays on the button."""
        for index in range(self.list.count()):
            item = self.list.item(index)
            ref = self._ref(item.data(Qt.UserRole + 1))
            item.setText(self._text(ref))
            if ref["id"] == self.expanded and (card := self.list.itemWidget(item)):
                card.count.setText(self._count(ref))

    def _count(self, ref):
        series = self.series.get(self.view) if self.view else None
        if series and series[0] == ref["id"]:
            return f"{series[1] + 1} of {series[2]}"
        located = self.located.get(self.view, {}).get(ref["id"]) if self.view else None
        count = len(located) if located is not None else ref.get("citation_count")
        return "Count unavailable" if count is None else f"{count} {'citations' if ref.get('count_status') == 'complete' else 'detected'}"

    def _filtered(self):
        if self.refs:
            self._fill()

    def _open(self, item):
        if (target := item.data(Qt.UserRole)) is not None:
            self.open_requested.emit(target)

    def _reference_open(self, ref):
        self.choices.hide()
        if ref.get("local_document_id") and not ref.get("local_invalid"):
            self.open_requested.emit(ref["local_document_id"])
        elif self.acquisition:
            self.acquisition.show_reference(self.document_id, ref)

    def step(self, ref):
        self.choices.hide()
        view = self.view
        if view is None or view in self.stepping:
            return
        cached = self.located.get(view, {}).get(ref["id"])
        if cached is not None:
            return self._show_citation(view, ref, cached)
        self.stepping.add(view)
        data = view.doc.pdf.data
        self._start(lambda: locate_citations(data, ref),
                    ("locate", self.document_id, weakref.ref(view), ref))

    def _show_citation(self, view, ref, citations):
        if not citations:
            return self._message("No citations found in the text")
        previous = self.series.get(view)
        last = previous[1] if previous and previous[0] == ref["id"] else None
        number = next_citation_index([citation_position(view, item) for item in citations], last, reading_position(view))
        self.series[view] = (ref["id"], number, len(citations))
        exact = reveal_citation(view, citations[number])
        self._update_counts()
        if not exact:
            self._message(f"Exact text was not located; showing page {citations[number]['page']}")

    def _citation_link(self, view, link):
        if view is None or view is not self.view or not self.refs:
            return False
        page = next((page for page, links in view.doc._links.items() if any(item is link for item in links)), None)
        if page is None:
            return False
        destination = link.destination
        words = view.doc.words(page)
        text = " ".join(word[4] for word in words if min(word[2], link.rect.x1) > max(word[0], link.rect.x0)
                        and min(word[3], link.rect.y1) > max(word[1], link.rect.y0))
        matches = citation_matches(self.refs, text, destination)
        if not matches:
            return False
        if len(matches) == 1:
            self._reference_open(matches[0])
        else:
            while self.choice_layout.count():
                self.choice_layout.takeAt(0).widget().deleteLater()
            self.choice_layout.addWidget(QLabel(f"Citation {text}: choose a reference", wordWrap=True))
            for ref in matches:
                button = QPushButton(f"{ref.get('label', '')} {ref.get('title') or ref.get('raw') or 'Reference'}")
                button.clicked.connect(lambda _=False, target=ref: self._reference_open(target))
                self.choice_layout.addWidget(button)
            self.choices.show()
        self.reveal_requested.emit()
        return True

    # ---- analysis job ---------------------------------------------------------------------
    def analyze(self, document_id):
        if document_id is None or document_id in self.analyzing:
            return
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
        if job["status"] == "completed":
            if document_id == self.document_id:
                self.show_for(document_id)
            else:
                library = self.workspace.library
                self._start(lambda: references.get(library, document_id), ("load", document_id))
        elif document_id == self.document_id:
            self._message(f"Could not analyze the references: {job.get('error') or job['status']}", retry=True)

    def close(self):
        for watcher in self.analyzing.values():
            watcher.stop()
        for task in [task for task in self.reading if task in TASKS]:  # the others are already deleted
            task.wait()  # a read may still be writing into the library folder
        return super().close()
