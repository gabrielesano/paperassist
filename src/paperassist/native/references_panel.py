"""The references column: the main paper's bibliography, filterable, with local availability."""
import unicodedata
import weakref
from html import escape

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QProgressBar, QPushButton, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QVBoxLayout, QWidget)

from paperassist import references

from .acquisition import reference_status, status_tone
from .citations import (bibliography_destination, citation_matches, citation_position, next_citation_index,
                        reading_position, reveal_citation)
from .jobs import JobWatcher
from .theme import FlowLayout, INK, LINE, TEAL, TONE_NEUTRAL, reference_palette

SHOWN_CHARS = 240  # one bibliography entry can run to a page; the tooltip keeps the whole text
PAD_X = 11
PAD_Y = 8


def _load_or_analyze(library, document_id):
    payload = references.get(library, document_id)
    return references.analyze(library, document_id) if payload["status"] == "not_analyzed" else payload


def fold(text):
    """Case- and accent-insensitive, so "Antolín" is found by typing "antolin"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.category(c).startswith("M")).lower()


def reference_matcher(query):
    """Every word must match somewhere in the entry, in any order."""
    words = fold(query).split()

    def matches(ref):
        text = fold(" ".join(str(ref.get(key)) for key in ("label", "authors", "year", "doi", "title", "raw") if ref.get(key)))
        return all(word in text for word in words)
    return matches


class CardDelegate(QStyledItemDelegate):
    """Paints collapsed cards with warm status tints and a 3px left bar, keeping zero widget trees."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def _lines(self, index):
        title, count, status = index.data().split("\n")
        return title, count, status

    def _fonts(self, base):
        meta = QFont(base)
        meta.setBold(True)
        title = QFont(base)
        title.setWeight(QFont.DemiBold)
        return meta, title

    def _heights(self, base_font, width, title):
        meta_font, title_font = self._fonts(base_font)
        title_metrics = QFontMetrics(title_font)
        meta_metrics = QFontMetrics(meta_font)
        body_metrics = QFontMetrics(base_font)
        wrapped = title_metrics.boundingRect(QRect(0, 0, width, 10000), Qt.TextWordWrap, title).height()
        return wrapped, meta_metrics.lineSpacing(), body_metrics.lineSpacing()

    def sizeHint(self, option, index):
        if (expanded := index.data(Qt.SizeHintRole)) is not None:  # set while the card holds its widgets
            return expanded
        width = max(self.view.viewport().width() - PAD_X - 8, 40)
        title, _, _ = self._lines(index)
        wrapped, meta_line, status_line = self._heights(option.font, width, title)
        return QSize(width + PAD_X + 8, wrapped + meta_line + status_line + 2 * PAD_Y + 6)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        border_hex, fill_hex, status_hex = index.data(Qt.UserRole + 3) or TONE_NEUTRAL
        hovered = bool(opt.state & QStyle.State_MouseOver)
        selected = bool(opt.state & QStyle.State_Selected)

        painter.save()
        bg = QColor(fill_hex)
        if selected or hovered:
            bg = bg.darker(104 if hovered and not selected else 107)
        painter.fillRect(option.rect, bg)
        painter.fillRect(QRect(option.rect.left(), option.rect.top(), 3, option.rect.height()), QColor(border_hex))
        painter.fillRect(QRect(option.rect.left(), option.rect.bottom(), option.rect.width(), 1), QColor(LINE))

        title, count, status = self._lines(index)
        area = option.rect.adjusted(PAD_X, PAD_Y, -8, -PAD_Y)
        meta_font, title_font = self._fonts(option.font)
        wrapped, meta_line, status_line = self._heights(option.font, area.width(), title)

        top = area.top()
        painter.setFont(meta_font)
        painter.setPen(QColor(TEAL))
        painter.drawText(QRect(area.left(), top, area.width(), meta_line), Qt.AlignLeft | Qt.AlignVCenter, count)
        top += meta_line + 3

        painter.setFont(title_font)
        painter.setPen(QColor(INK))
        painter.drawText(QRect(area.left(), top, area.width(), wrapped), Qt.TextWordWrap, title)
        top += wrapped + 3

        painter.setFont(option.font)
        painter.setPen(QColor(status_hex))
        painter.drawText(QRect(area.left(), top, area.width(), status_line), Qt.AlignLeft | Qt.AlignVCenter, status)
        if opt.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor(TEAL), 2, Qt.DashLine))
            painter.drawRect(option.rect.adjusted(3, 2, -3, -3))
        painter.restore()


class Card(QWidget):
    """The one expanded card: citation step button, title link, and inline acquisition actions."""
    collapse_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, objectName="expanded-card")
        self.setAttribute(Qt.WA_StyledBackground, True)

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
        self.loaded = {}
        self.series, self.located = weakref.WeakKeyDictionary(), weakref.WeakKeyDictionary()
        self.stepping, self.pending_reload = set(), set()
        self.followed_citations = set()
        self.payload_status = "not_analyzed"
        self.pending_citation = None
        self.filters, self.analyzing = {}, {}  # filter text and running jobs, by document id
        self.filter = QLineEdit(placeholderText="Filter references…", clearButtonEnabled=True)
        self.filter.setAccessibleName("Filter references")
        self.status = QLabel(objectName="muted-note", wordWrap=True)
        self.analysis_note = QLabel("Detected citations only; bibliography and counts may be incomplete.",
                                    objectName="muted-note", wordWrap=True)
        self.analysis_note.hide()
        self.progress = QProgressBar(maximum=0, textVisible=False, maximumHeight=4)  # no progress figures: busy only
        self.retry = QPushButton("Analyze again")
        self.retry.setProperty("link", True)
        self.acquire = QPushButton("Acquire references")
        self.acquire.setProperty("link", True)
        self.repair = QPushButton("Repair downloads")
        self.repair.setProperty("link", True)
        self.acquire.clicked.connect(lambda: self.acquisition and self.acquisition.offer(self.document_id, self.refs))
        self.repair.clicked.connect(lambda: self.acquisition and self.acquisition.repair(self.document_id))

        self.actions_bar = QWidget()
        actions_flow = FlowLayout(self.actions_bar, margin=0, h_spacing=14, v_spacing=4)
        actions_flow.addWidget(self.acquire)
        actions_flow.addWidget(self.repair)

        self.choices = QWidget()
        self.choice_layout = QVBoxLayout(self.choices)
        self.choice_layout.setContentsMargins(0, 4, 0, 4)
        self.choice_layout.setSpacing(4)
        self.choices.hide()

        self.list = QListWidget(horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOff, resizeMode=QListView.Adjust,
                                verticalScrollMode=QListWidget.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.setAccessibleName("References")
        self.list.installEventFilter(self)
        self.list.setItemDelegate(CardDelegate(self.list))
        self.expanded = None  # id of the single card that owns widgets

        column = QVBoxLayout(self)
        column.setContentsMargins(10, 8, 10, 8)
        column.setSpacing(6)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.status, 1)
        row.addWidget(self.retry)
        column.addWidget(self.filter)
        column.addLayout(row)
        column.addWidget(self.analysis_note)
        column.addWidget(self.actions_bar)
        column.addWidget(self.progress)
        column.addWidget(self.choices)
        column.addWidget(self.list, 1)

        self.filter.textChanged.connect(self._filtered)
        self.retry.clicked.connect(lambda: self.analyze(self.document_id))
        self.list.itemClicked.connect(self._expand)
        self.list.itemActivated.connect(lambda item: self._expand(item, focus=True))  # Enter, from the keyboard
        self.list.itemDoubleClicked.connect(self._open)
        self._set_busy(False)
        self._set_actions_visible(False)

    def _set_actions_visible(self, visible):
        self.acquire.setVisible(visible)
        self.repair.setVisible(visible)

    def eventFilter(self, watched, event):
        if (watched is self.list and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)):
            if item := self.list.currentItem():
                self._expand(item, focus=True)
            return True
        return super().eventFilter(watched, event)
        self.actions_bar.setVisible(visible)

    def minimumSizeHint(self):
        # An expanded card cannot shrink, so the panel must be wide enough for it. Only stable quantities
        # are used (never the current width), otherwise the hint would feed back into the layout.
        hint = super().minimumSizeHint()
        if self.expanded is not None and (item := self._item(self.expanded)) and (card := self.list.itemWidget(item)):
            card.ensurePolished()
            margins = self.layout().contentsMargins()
            around = margins.left() + margins.right() + self.list.frameWidth() * 2
            bar = self.list.verticalScrollBar().sizeHint().width()
            return QSize(max(hint.width(), card.minimumSizeHint().width() + around + bar), hint.height())
        return hint

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.expanded is not None and (item := self._item(self.expanded)):
            self._fit_item(item)  # a wrapped title or FlowLayout makes the card's height depend on its width

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            if self.expanded is not None and (item := self._item(self.expanded)):
                self._fit(item)

    def set_acquisition(self, acquisition):
        self.acquisition = acquisition
        acquisition.changed.connect(self.reload)
        self._set_actions_visible(self.document_id is not None and self.workspace is not None)

    def bind_view(self, view):
        self.view = view
        if view is not None:
            view.internal_link_handler = lambda link, target=weakref.ref(view): self._citation_link(target(), link)

    def reload(self, document_id):
        self.loaded.pop(document_id, None)
        if document_id in self.analyzing:
            self.pending_reload.add(document_id)
            return
        if document_id == self.document_id:
            self.show_for(document_id)

    def library_changed(self):
        """Forget bibliographies affected by library imports or metadata edits."""
        self.loaded.clear()
        self.pending_reload.update(self.analyzing)
        if self.document_id is not None:
            self.reload(self.document_id)

    # ---- what is shown ------------------------------------------------------------------
    def show_for(self, document_id):
        """Show the bibliography of a library paper; anything else gets an explanation."""
        same = document_id == self.document_id
        self.filters[self.document_id] = self.filter.text()
        if not same:
            self.expanded = None
            self.pending_citation = None
        self.document_id = document_id
        available = document_id is not None and self.workspace is not None
        self._set_actions_visible(available)
        self.acquire.setEnabled(available)
        self.repair.setEnabled(available)
        self.choices.hide()
        self.filter.blockSignals(True)
        self.filter.setText(self.filters.get(document_id, ""))
        self.filter.blockSignals(False)
        self.retry.hide()
        if not same:
            self.refs = []
            self.list.clear()
        if document_id is None or self.workspace is None:
            self.analysis_note.hide()
            return self._message("References are available for papers in the library.")
        if same:
            self.loaded.pop(document_id, None)
        cached = self.loaded.get(document_id)
        if (cached and cached.get("extraction_version") == references.EXTRACTION_VERSION
                and cached.get("destination_version") == references.DESTINATION_VERSION):
            self.refs, self.payload_status = cached["references"], cached["status"]
            self._fill()
            self.references_loaded.emit(document_id, self.refs)
            return
        if document_id in self.analyzing:
            return self._message("Loading references…", busy=True)
        self._load(document_id)

    def _load(self, document_id):
        library = self.workspace.library
        self._run_job(document_id, lambda: self.workspace.submit(
            lambda job, cancel: _load_or_analyze(library, document_id)), "load")

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
            item.setData(Qt.UserRole + 3, reference_palette(ref))
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
            border_hex, fill_hex, _ = reference_palette(ref)
            card = Card()
            card.setStyleSheet(
                f"QWidget#expanded-card {{ background: {fill_hex}; border: 1px solid #7e968d; "
                f"border-left: 3px solid {border_hex}; }}"
            )
            card.collapse_requested.connect(self._collapse_current)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(PAD_X, PAD_Y, 8, PAD_Y)
            layout.setSpacing(5)

            top_row = QHBoxLayout()
            top_row.setContentsMargins(0, 0, 0, 0)
            card.count = QPushButton(self._count(ref))
            card.count.setProperty("link", True)
            card.count.setAccessibleName("Go to the next citation in the text")
            card.count.clicked.connect(lambda _=False, target=ref: self.step(target))
            top_row.addWidget(card.count)
            top_row.addStretch(1)
            layout.addLayout(top_row)

            title_text = escape(item.text().split("\n")[0])
            card.title = QLabel(
                f'<a href="open" style="color:{INK}; font-weight:600; text-decoration:none;">{title_text}</a>',
                wordWrap=True,
            )
            card.title.setToolTip(item.toolTip())
            card.title.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
            card.title.linkActivated.connect(lambda _=False, target=ref: self._reference_open(target))
            layout.addWidget(card.title)

            if self.acquisition:
                actions = self.acquisition.card_actions(self.document_id, ref, card)
                layout.addWidget(actions)
                actions.layout_changed.connect(lambda: self._fit(item))
            self.list.setItemWidget(item, card)
            self._fit(item)
        if focus:
            self.list.itemWidget(item).title.setFocus()

    def _fit(self, item):
        self.updateGeometry()
        self._fit_item(item)

    def _fit_item(self, item):
        card = self.list.itemWidget(item)
        if card is None:
            return
        width = max(self.list.viewport().width(), 40)
        card.ensurePolished()  # unpolished buttons report smaller hints and the card would be clipped
        height = card.layout().totalHeightForWidth(width) if card.layout().hasHeightForWidth() else card.sizeHint().height()
        size = QSize(width, height)
        if size != item.sizeHint():
            item.setSizeHint(size)
            self.list.doItemsLayout()

    def _collapse(self, item):
        if card := self.list.itemWidget(item):
            card.hide()  # removeItemWidget schedules deletion; do not leave it visible until then
        self.list.removeItemWidget(item)
        item.setSizeHint(QSize())  # back to the delegate's size
        self.list.doItemsLayout()
        self.updateGeometry()

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
            if item := self._item(ref["id"]):
                self._collapse(item)
                self._expand(item)
                self.list.scrollToItem(item)

    def step(self, ref):
        self.choices.hide()
        view = self.view
        if view is None or view in self.stepping:
            return
        cached = self.located.get(view, {}).get(ref["id"])
        if cached is not None:
            return self._show_citation(view, ref, cached)
        self.stepping.add(view)
        target = weakref.ref(view)
        view.client.locate(view.doc_id, ref, lambda result, error: self._located(target(), ref, result, error))

    def _located(self, view, ref, result, error):
        if view is None:
            return
        self.stepping.discard(view)
        if error:
            if view is self.view:
                self._message(f"Citations could not be read from this PDF: {error}")
            return
        self.located.setdefault(view, {})[ref["id"]] = result
        if view is self.view:
            self._show_citation(view, ref, result)

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
        if view is None or view is not self.view:
            return False
        if not self.refs:
            if bibliography_destination(link.destination):
                self.reveal_requested.emit()
                if self.document_id in self.analyzing:
                    self.pending_citation = (self.document_id, weakref.ref(view), link)
                    self._message("Loading references to reveal this citation…", busy=True)
                else:
                    self._message("This citation was not found among the extracted references.", retry=True)
                return True
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
            if bibliography_destination(destination):
                self.reveal_requested.emit()
                self._message("This citation was not found among the extracted references.", retry=True)
                return True
            return False
        self.reveal_requested.emit()
        if len(matches) == 1:
            self._follow_citation(matches[0])
        else:
            while self.choice_layout.count():
                self.choice_layout.takeAt(0).widget().deleteLater()
            self.choice_layout.addWidget(QLabel(f"Citation {text}: choose a reference", objectName="muted-note", wordWrap=True))
            for ref in matches:
                label = f"{ref.get('label', '')} {ref.get('title') or ref.get('raw') or 'Reference'}"
                button = QPushButton(label.replace("&", "&&"))
                button.clicked.connect(lambda _=False, target=ref: self._follow_citation(target))
                self.choice_layout.addWidget(button)
            self.choices.show()
        return True

    def _follow_citation(self, ref):
        key = (self.document_id, ref["id"])
        first = key not in self.followed_citations
        self.followed_citations.add(key)
        if first and ref.get("local_document_id") and not ref.get("local_invalid"):
            self._reference_open(ref)
        else:
            self._reveal_reference(ref)

    def _reveal_reference(self, ref):
        self.choices.hide()
        item = self._item(ref["id"])
        if item is None and self.filter.text():
            self.filter.clear()
            item = self._item(ref["id"])
        if item is not None:
            self._expand(item)
            self.list.scrollToItem(item, QListView.PositionAtCenter)

    # ---- analysis job ---------------------------------------------------------------------
    def analyze(self, document_id):
        if document_id is None or self.workspace is None or document_id in self.analyzing:
            return
        self.loaded.pop(document_id, None)
        self._run_job(document_id, lambda: self.workspace.analyze_references(document_id), "analyze")

    def _run_job(self, document_id, start, operation):
        try:
            job = start()
        except Exception as error:  # too many jobs, or the document is gone
            return self._message(f"Could not {operation} the references: {error}", retry=True)
        watcher = self.analyzing[document_id] = JobWatcher(self.workspace, job, self)
        watcher.finished.connect(lambda result, doc_id=document_id: self._job_done(doc_id, result, operation))
        if document_id == self.document_id:
            self._message("Loading references…" if operation == "load" else "Analyzing references…", busy=True)

    def _job_done(self, document_id, job, operation):
        if watcher := self.analyzing.pop(document_id, None):
            watcher.deleteLater()
        if document_id in self.pending_reload:
            self.pending_reload.remove(document_id)
            self._load(document_id)
            return
        if job["status"] != "completed":
            if document_id == self.document_id:
                self._message(f"Could not {operation} the references: {job.get('error') or job['status']}", retry=True)
            return
        payload = job["result"]
        self.loaded[document_id] = payload
        if document_id == self.document_id:
            self.refs, self.payload_status = payload["references"], payload["status"]
            self._fill()
            if self.pending_citation:
                _, target, link = self.pending_citation
                self.pending_citation = None
                if (view := target()) is not None and view is self.view and not self._citation_link(view, link):
                    self._message("This citation was not found among the extracted references.")
        self.references_loaded.emit(document_id, payload["references"])
        self.refs_changed.emit(document_id, payload["references"])

    def close(self):
        for watcher in self.analyzing.values():
            watcher.stop()
        return super().close()
