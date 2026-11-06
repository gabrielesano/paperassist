"""Exact-file sidecar notes and the right-column comment composer."""
from copy import deepcopy
import math
import re
from weakref import WeakSet

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget


DEFAULT_COLOR = "#aa700f"


def quote(note):
    return note.get("quote") or note.get("selection") or ""


def note_color(note):
    value = note.get("color", "")
    return value if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else DEFAULT_COLOR


def line_bands(rects, vertical=False):
    def inside(a, b):
        return a.x0 >= b.x0 - .5 and a.y0 >= b.y0 - .5 and a.x1 <= b.x1 + .5 and a.y1 <= b.y1 + .5
    rects = [rect for i, rect in enumerate(rects) if not any(
        j != i and inside(rect, other) and (not inside(other, rect) or j < i) for j, other in enumerate(rects))]
    along, across = (1, 0) if vertical else (0, 1)
    lines = []
    for rect in sorted(rects, key=lambda rect: rect[along]):
        for i, line in enumerate(lines):
            thickness, other = rect[across + 2] - rect[across], line[across + 2] - line[across]
            if (abs((rect[across] + rect[across + 2] - line[across] - line[across + 2]) / 2) < min(thickness, other) * .4
                    and rect[along] - line[along + 2] <= max(thickness, other) * 2):
                lines[i] = line.union(rect)
                break
        else:
            lines.append(rect)
    return lines


def note_rects(note, pdf):
    """Read PDF.js user-space anchors through the same crop/rotation map as text."""
    anchor = note.get("anchor")
    if not isinstance(anchor, dict) or anchor.get("version") != 1:
        return []
    result = []
    parts = anchor.get("pages")
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict):
            continue
        page = part.get("page")
        if type(page) is not int or not 1 <= page <= len(pdf.sizes):
            continue
        bands, frame = [], pdf.frame(page - 1)
        rects = part.get("rects")
        for rect in rects if isinstance(rects, list) else []:
            if (isinstance(rect, (list, tuple)) and len(rect) == 4 and
                    all(type(value) in (int, float) and math.isfinite(value) for value in rect)):
                band = frame.rect(*rect)
                if band.width > 0 and band.height > 0:
                    bands.append(band)
        result.extend((page - 1, rect) for rect in line_bands(bands, vertical=frame.matrix[0] == 0))
    return result


def capture_anchor(view):
    pages = {}
    for page, rect, _ in view.selection_lines:
        frame = view.doc.pdf.frame(page)
        a, b = frame.to_pdf(rect.x0, rect.y0), frame.to_pdf(rect.x1, rect.y1)
        pages.setdefault(page + 1, []).append([min(a[0], b[0]), min(a[1], b[1]),
                                               max(a[0], b[0]), max(a[1], b[1])])
    if not pages or not view.selected_text().strip():
        return None
    return {"page": min(pages), "quote": view.selected_text().strip(),
            "anchor": {"version": 1, "pages": [{"page": page, "rects": rects} for page, rects in pages.items()]}}


class NotesModel(QObject):
    changed = Signal()
    failed = Signal(str)

    def __init__(self, library, document_id, parent=None):
        super().__init__(parent)
        self.library, self.document_id = library, document_id
        self.notes, self.deleted = [], None
        self.error = ""
        self.reload()

    def reload(self):
        try:
            self.notes = self.library.get_notes(self.document_id)
            self.error = ""
            self.changed.emit()
            return True
        except Exception as error:
            return self._fail(error)

    def _fail(self, error):
        self.error = str(error)
        self.failed.emit(self.error)
        return False

    def _write(self, change):
        try:
            notes = self.library.get_notes(self.document_id)
            result = change(notes)
            self.library.save_notes(self.document_id, notes)
            self.notes, self.error = notes, ""
            return result
        except Exception as error:
            self._fail(error)
            return None

    def append(self, note):
        result = self._write(lambda notes: notes.append(deepcopy(note)) or True)
        if result:
            self.deleted = None
            self.changed.emit()
        return bool(result)

    def update(self, note, color=None):
        def change(notes):
            try:
                index = notes.index(note)
            except ValueError:
                raise ValueError("Comment changed; refresh and try again.") from None
            if color is None:
                removed = notes.pop(index)
                return index, removed
            notes[index] = {**notes[index], "color": color}
            return notes[index]
        result = self._write(change)
        if result is None:
            return False
        self.deleted = result if color is None else None
        self.changed.emit()
        return True

    def undo_delete(self):
        if self.deleted is None:
            return False
        index, note = self.deleted
        if self._write(lambda notes: notes.insert(min(index, len(notes)), deepcopy(note)) or True):
            self.deleted = None
            self.changed.emit()
            return True
        return False


class CommentsPanel(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller, self.view = controller, None
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(6, 0, 6, 6)
        self.status = QLabel(wordWrap=True)
        self.column.addWidget(self.status)
        self.add = QPushButton("Add comment")
        self.add.clicked.connect(controller.compose)
        self.column.addWidget(self.add)
        self.composer = QWidget()
        form = QVBoxLayout(self.composer)
        form.setContentsMargins(0, 0, 0, 0)
        self.head = QLabel(wordWrap=True, textFormat=Qt.PlainText)
        self.text = QTextEdit(placeholderText="Write a comment…", objectName="comment-text")
        self.text.setAccessibleName("Comment text")
        self.text.setMaximumHeight(160)
        self.text.textChanged.connect(self._typed)
        row = QHBoxLayout()
        self.save_button = QPushButton("Save comment")
        cancel = QPushButton("Cancel")
        self.save_button.clicked.connect(lambda: controller.save_draft(controller.window.pane()))
        cancel.clicked.connect(lambda: controller.discard_draft(controller.window.pane()))
        row.addWidget(self.save_button)
        row.addWidget(cancel)
        form.addWidget(self.head)
        form.addWidget(self.text)
        form.addLayout(row)
        self.column.addWidget(self.composer)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.column.addWidget(self.scroll, 1)
        self.undo = QPushButton("Comment deleted · Undo")
        self.undo.clicked.connect(lambda: self.view and self.view.notes_model.undo_delete())
        self.column.addWidget(self.undo)
        self.refresh(None)

    def _typed(self):
        if self.view and self.view.comment_draft is not None:
            self.view.comment_draft["text"] = self.text.toPlainText()
            self.save_button.setEnabled(bool(self.text.toPlainText().strip()))
            self.controller.draft_changed.emit()

    def keyPressEvent(self, event):
        # Keys the list and buttons leave unhandled act on the selected comment, as in the PDF.
        if self.view and self.view.selected_comment is not None and event.modifiers() == Qt.NoModifier:
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                return self.view.delete_annotation()
            if event.key() == Qt.Key_Escape:
                return self.view.select_comment(None)
        super().keyPressEvent(event)

    def refresh(self, view):
        self.view = view
        model = view.notes_model if view else None
        self.status.setText(("Comments unavailable: " + model.error) if model and model.error else
                            "Comments belong to the active paper." if model else
                            "Comments are available for papers in the library.")
        self.add.setEnabled(model is not None)
        draft = view.comment_draft if view else None
        self.composer.setVisible(draft is not None)
        self.add.setVisible(draft is None)
        if draft is not None:
            self.head.setText(f"Comment · Page {draft['page']}\n" + (quote(draft) or "Page comment. Select PDF text to anchor it to a phrase."))
            if self.text.toPlainText() != draft.get("text", ""):
                self.text.blockSignals(True)
                self.text.setPlainText(draft.get("text", ""))
                self.text.blockSignals(False)
            self.save_button.setEnabled(bool(draft.get("text", "").strip()))
        host = QWidget()
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 0, 0)
        for note in model.notes if model else []:
            if not isinstance(note, dict):
                continue
            row = QWidget()
            row.setObjectName("comment-row")
            if view.selected_comment == note:
                row.setStyleSheet("#comment-row { background: #e6eeeb; }")
            column = QVBoxLayout(row)
            jump = QPushButton(f"Page {note.get('page', '?')}")
            jump.clicked.connect(lambda _=False, note=note: view.select_comment(note, scroll=True))
            column.addWidget(jump)
            if quote(note):
                selected = QLabel(str(quote(note)), wordWrap=True, textFormat=Qt.PlainText)
                selected.setTextInteractionFlags(Qt.TextSelectableByMouse)
                column.addWidget(selected)
            text = QLabel(str(note.get("text", "")), wordWrap=True, textFormat=Qt.PlainText)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            column.addWidget(text)
            remove = QPushButton("Delete comment")
            remove.clicked.connect(lambda _=False, note=note: model.update(note))
            column.addWidget(remove)
            rows.addWidget(row)
        if model and not model.notes:
            rows.addWidget(QLabel("No comments on this document yet.", wordWrap=True))
        rows.addStretch()
        old = self.scroll.takeWidget()
        self.scroll.setWidget(host)
        if old:
            old.deleteLater()
        self.undo.setVisible(bool(model and model.deleted is not None))


class CommentsController(QObject):
    draft_changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window, self.models, self.bound = window, {}, WeakSet()
        self.panel = CommentsPanel(self)
        for key, mode in (("1", "references"), ("2", "comments")):
            action = QAction(window, shortcut=key)  # plain keys: text fields claim them first
            action.triggered.connect(lambda _=False, mode=mode: self.show_mode(mode))
            window.addAction(action)

    def bind(self, pane):
        if pane.view in self.bound:
            return
        self.bound.add(pane.view)
        source = pane.view.doc.source
        if not self.window.workspace or not hasattr(source, "document_id"):
            return
        if pane.key not in self.models:
            model = self.models[pane.key] = NotesModel(self.window.workspace.library, source.document_id, self)
            model.changed.connect(self.refresh)
            model.failed.connect(lambda message: self.window.statusBar().showMessage(f"Could not save comment: {message}", 8000))
        pane.view.set_comment_model(self.models[pane.key])
        pane.view.comment_selection_changed.connect(self._selected)
        pane.view.selection_changed.connect(lambda: self._selection(pane.view))

    def refresh(self, *_):
        pane = self.window.pane()
        self.panel.refresh(pane.view if pane else None)
        if hasattr(self.window, "annotation_bar"):
            self.window.annotation_bar.refresh()

    def show_mode(self, mode):
        self.window.set_columns(details=True)
        self.window.detail_switch.show_mode(mode)
        self.refresh()

    def _selected(self, note):
        if note is not None:
            self.show_mode("comments")
        else:
            self.refresh()

    def compose(self):
        pane = self.window.pane()
        if not pane or pane.view.notes_model is None:
            return
        view = pane.view
        anchor = capture_anchor(view)
        if view.comment_draft is None:
            page, _ = view.pages.anchor(view._scroll_top())
            view.comment_draft = {"page": page + 1, "text": ""}
        if anchor:
            view.comment_draft.update(anchor)
        view.set_tool("comment")
        view.select_comment(None)
        self.window.highlight_action.setChecked(False)
        view.focus_comment(view.comment_draft, scroll=False)
        self.show_mode("comments")
        self.panel.text.setFocus()
        self.draft_changed.emit()

    def _selection(self, view):
        if view.commenting and view.comment_draft is not None and (anchor := capture_anchor(view)):
            view.comment_draft.update(anchor)
            view.focus_comment(view.comment_draft, scroll=False)
            self.refresh()

    def has_draft(self, pane):
        return bool(pane.view.comment_draft and pane.view.comment_draft.get("text", "").strip())

    def save_draft(self, pane):
        view = pane.view
        if not self.has_draft(pane):
            return True
        note = deepcopy(view.comment_draft)
        note["text"] = note["text"].strip()
        if not view.notes_model.append(note):
            self.refresh()
            return False
        self.discard_draft(pane)
        return True

    def discard_draft(self, pane):
        pane.view.comment_draft = None
        pane.view.commenting = False
        pane.view.focus_comment(None, scroll=False)
        self.refresh()
        self.draft_changed.emit()


def install(window):
    return CommentsController(window)
