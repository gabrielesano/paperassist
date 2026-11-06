"""A pane is one PDF view with its own find bar, and a title bar when it is a quick view or read-only."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget

from .document import PdfDocument
from .view import PdfView


class FindBar(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view, self._last = view, None
        self.field, self.status = QLineEdit(placeholderText="Find in document"), QLabel()
        previous, following = QPushButton("Previous"), QPushButton("Next")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        for widget in (self.field, previous, following, self.status):
            row.addWidget(widget)
        self.field.returnPressed.connect(self._enter)
        following.clicked.connect(lambda: self._run(1))
        previous.clicked.connect(lambda: self._run(-1))
        self.hide()

    def _enter(self):
        self._run(-1 if QGuiApplication.keyboardModifiers() & Qt.ShiftModifier else 1)

    def _run(self, direction):
        if self.field.text() != self._last:
            self._last = self.field.text()
            count = self.view.find(self._last)
        else:
            self.view.step_hit(direction)
            count = len(self.view.hits)
        self.status.setText(f"{self.view.hit + 1} / {count}" if count else "No matches")

    def open(self):
        self.show()
        self.field.setFocus()
        self.field.selectAll()

    def close_bar(self):
        self.hide()
        self.view.find("")
        self.view.setFocus()
        self._last = None
        self.status.clear()


class Pane(QFrame):
    quick_layout_toggled = Signal()
    promote_requested = Signal()
    close_requested = Signal()
    released = Signal()  # the document is about to close; last chance to store its reading position

    def __init__(self, source, client):
        super().__init__()
        self.setObjectName("pane")
        self.view = PdfView(PdfDocument(source), client)
        self.find_bar = FindBar(self.view)
        self.quick = False
        self.title_label = QLabel()
        self.title_label.setMinimumWidth(40)  # long titles are clipped, never widen the pane
        self.layout_button = QToolButton(toolTip="Stack the papers or put them side by side")
        self.layout_button.clicked.connect(self.quick_layout_toggled)
        promote, close = QToolButton(text="Open in tab"), QToolButton(text="Close", toolTip="Close the quick view (Esc)")
        promote.clicked.connect(self.promote_requested)
        close.clicked.connect(self.close_requested)
        self.bar = QWidget()
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(6, 2, 6, 2)
        row.addWidget(self.title_label, 1)
        self.quick_buttons = [self.layout_button, promote, close]
        for button in self.quick_buttons:
            row.addWidget(button)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        for widget in (self.bar, self.view, self.find_bar):
            column.addWidget(widget)
        self.view.edited.connect(self.refresh_bar)
        self.set_quick(False)

    # ---- what the rules and the window need to know -------------------------------------
    @property
    def key(self):
        return self.view.doc.source.key

    @property
    def title(self):
        return self.view.doc.source.title

    @property
    def state(self):
        return self.view.doc.state

    @property
    def modified(self):
        return self.view.doc.modified

    def save(self):
        return self.view.save()

    def set_locked(self, locked):
        self.view.locked = locked
        self.refresh_bar()

    def set_quick(self, quick):
        self.quick = quick
        for button in self.quick_buttons:
            button.setVisible(quick)
        self.refresh_bar()

    def set_vertical_split(self, vertical):
        self.layout_button.setText("Side by side" if vertical else "Stacked")

    def refresh_bar(self):
        notes = [("Quick view: " + self.title) if self.quick else ""]
        if self.view.locked:
            notes.append("Last saved version")
        self.title_label.setText(" · ".join(n for n in notes if n))
        self.title_label.setToolTip(self.title + ("\nAnother view of this paper has unsaved changes; save it there." if self.view.locked else ""))
        self.bar.setVisible(self.quick or self.view.locked)

    def set_active(self, active):
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def keyPressEvent(self, event):
        if event.key() != Qt.Key_Escape:
            return super().keyPressEvent(event)
        if self.find_bar.isVisible():
            self.find_bar.close_bar()
        elif self.quick:
            self.close_requested.emit()

    def release(self):
        self.released.emit()
        self.view.release()
        self.view.doc.close()
