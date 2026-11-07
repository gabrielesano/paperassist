"""A pane is one PDF view with its own find bar, and a title bar when it is a quick view or read-only."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolBar, QVBoxLayout, QWidget

from .document import PdfDocument
from .view import PdfView
from .toolbar import install_overflow


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
    split_changed = Signal()  # the tab stacked or unstacked its panes

    def __init__(self, source, client):
        super().__init__()
        self.setObjectName("pane")
        self.view = PdfView(PdfDocument(source), client)
        self.find_bar = FindBar(self.view)
        self.quick, self.vertical = False, True  # vertical: the other pane sits above or below
        self.title_label = QLabel()
        self.head, self.note = "", ""  # the label elides the head, never the note: a toolbar widget needs an exact width or it overlaps the buttons
        self.bar = QToolBar("Quick view", objectName="pane-bar")
        self.bar.setMovable(False)
        self.title_action = self.bar.addWidget(self.title_label)
        self.quick_buttons = []
        for text, tooltip, signal in (
            ("Side by side", "Stack the papers or put them side by side", self.quick_layout_toggled),
            ("Open in tab", "Move the quick view to a tab", self.promote_requested),
            ("Close", "Close the quick view (Esc)", self.close_requested),
        ):
            action = self.bar.addAction(text)
            action.setToolTip(tooltip)
            action.triggered.connect(signal)
            self.quick_buttons.append(self.bar.widgetForAction(action))
        self.layout_button = self.quick_buttons[0]
        install_overflow(self.bar)
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
            button.defaultAction().setVisible(quick)
        self.refresh_bar()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.title_action.setVisible(self.width() >= 500)
        self._fit_title()
        self.bar.setToolTip(self.title_label.toolTip())

    def _fit_title(self):
        label = self.title_label
        tail = (" · " if self.head and self.note else "") + self.note
        room = self.width() - sum(button.sizeHint().width() for button in self.quick_buttons) - 60 - label.fontMetrics().horizontalAdvance(tail)
        text = label.fontMetrics().elidedText(self.head, Qt.ElideRight, max(room, 60)) + tail
        label.setText(text)
        label.setFixedWidth(label.fontMetrics().horizontalAdvance(text) + 12)

    def set_vertical_split(self, vertical, narrow=False):
        """narrow: the reader is too small for two columns, so the saved preference waits."""
        action = self.layout_button.defaultAction()
        action.setText("Side by side" if vertical else "Stacked")
        action.setEnabled(not narrow)
        action.setToolTip("The reader is too narrow for two columns" if narrow else "Stack the papers or put them side by side")
        changed, self.vertical = vertical != self.vertical, vertical
        if changed:
            self.split_changed.emit()

    def refresh_bar(self):
        self.head = ("Quick view: " + self.title) if self.quick else ""
        self.note = "Last saved version" if self.view.locked else ""
        self._fit_title()
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
