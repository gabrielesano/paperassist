"""Keyboard shortcuts of the native reader, and the help dialog that lists them.

One table installs the bindings and renders the help, so the two cannot drift. Plain keys
stay out of text fields because Qt lets a line edit claim printable keys before a shortcut
is matched, and window shortcuts do not fire while a modal dialog is the active window.
Ctrl+T, Ctrl+W, Ctrl+Tab and Alt+arrows are not bound here, and Preferences belongs to the app menu.
"""
import html
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QAction, QIntValidator, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QTextBrowser, QVBoxLayout, QWidget

from paperassist.library import MAX_LABEL

from .library_source import display_title
from .positions import current_page


@dataclass(frozen=True)
class Entry:
    group: str
    keys: tuple  # alternative bindings, each a portable QKeySequence text
    label: str
    run: Callable | None = None  # called with the Shortcuts object; None means another part handles the keys


LAST = 1 << 30  # any page number past the end means the last page

TABLE = [
    Entry("General", ("?",), "Show keyboard shortcuts", lambda s: show_help(s.window)),
    Entry("General", ("L",), "Open the library", lambda s: s.window.show_library()),
    Entry("General", ("Esc",), "Close a dialog, or return from the Page and Filter fields to the document"),
    Entry("Navigation", ("N", "J"), "Next page", lambda s: s.step_page(1)),
    Entry("Navigation", ("P", "K"), "Previous page", lambda s: s.step_page(-1)),
    Entry("Navigation", ("Home",), "First page", lambda s: s.go_to_page(0)),
    Entry("Navigation", ("End",), "Last page", lambda s: s.go_to_page(LAST)),
    Entry("Navigation", ("G",), "Go to page: type a number, then Enter", lambda s: s.page_field.focus()),
    Entry("Navigation", ("Up", "Down"), "Scroll the document (click it first)"),
    Entry("Navigation", ("Space", "Shift+Space"), "Scroll a screen down or up"),
    Entry("Navigation", ("PgUp", "PgDown"), "Scroll a screen up or down"),
    Entry("Annotations", ("C",), "Comment on the selected text or the current page"),
    Entry("Annotations", ("Delete", "Backspace"), "Delete the selected annotation or comment"),
    Entry("Annotations", ("Ctrl+Z",), "Undo"),
    Entry("Annotations", ("Ctrl+Shift+Z", "Ctrl+Y"), "Redo"),
    Entry("Tabs and panels", (",",), "Previous paper tab", lambda s: s.step_tab(-1)),
    Entry("Tabs and panels", (".",), "Next paper tab", lambda s: s.step_tab(1)),
    Entry("Tabs and panels", ("X",), "Close the paper tab (asks about unsaved changes)", lambda s: s.close_tab()),
    Entry("Tabs and panels", ("F2",), "Rename the focused paper tab; double-click works too"),
    Entry("Tabs and panels", ("B",), "Show or hide both side columns", lambda s: s.window.toggle_columns()),
    Entry("Tabs and panels", ("O",), "Show or hide the outline", lambda s: s.toggle_outline()),
    Entry("Tabs and panels", ("1", "2"), "Show the references or the comments"),
    Entry("Tabs and panels", ("/",), "Filter the references (Esc clears, Esc again leaves)", lambda s: s.focus_filter()),
]


def sequence_text(key):
    return QKeySequence(key).toString(QKeySequence.NativeText)


def other_commands(window, own):
    """Toolbar and menu actions with shortcuts that the table does not mention, as (keys, name) rows."""
    listed = {QKeySequence(key).toString() for entry in TABLE for key in entry.keys}
    rows = {}
    for action in window.findChildren(QAction):
        keys = [seq.toString(QKeySequence.NativeText) for seq in action.shortcuts() if seq.toString() not in listed]
        name = action.text().replace("&", "")
        if action not in own and keys and name:
            rows.setdefault(name, keys)
    return [(keys, name) for name, keys in rows.items()]


def show_help(window):
    sections = {}
    for entry in TABLE:
        sections.setdefault(entry.group, []).append(([sequence_text(k) for k in entry.keys], entry.label))
    sections["Toolbar and menu"] = other_commands(window, window.shortcuts.actions)
    body = "".join(
        f"<h3>{html.escape(group)}</h3><table cellpadding='3'>" + "".join(
            f"<tr><td><b>{html.escape(' / '.join(keys))}</b></td><td>{html.escape(label)}</td></tr>" for keys, label in rows)
        + "</table>" for group, rows in sections.items())
    dialog = QDialog(window)
    dialog.setObjectName("shortcuts-help")
    dialog.setWindowTitle("Keyboard shortcuts")
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.resize(560, 620)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QTextBrowser(html=body))
    layout.addWidget(QLabel("Letter keys work when no text field has focus."))
    dialog.open()
    return dialog


class PageField(QWidget):
    """Current page and total for the active document, in the status bar so it never covers the PDF."""

    def __init__(self, shortcuts):
        super().__init__()
        self.shortcuts, self.view = shortcuts, None
        self.edit = QLineEdit(alignment=Qt.AlignRight, toolTip="Go to page (G)")
        self.edit.setAccessibleName("Page number")
        self.edit.setValidator(QIntValidator(1, 99999))
        self.edit.setFixedWidth(52)
        self.total = QLabel()
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 6, 0)
        row.addWidget(self.edit)
        row.addWidget(self.total)
        self.edit.returnPressed.connect(self.commit)
        self.edit.installEventFilter(shortcuts)

    def show_page(self, view):
        self.view = view
        self.setEnabled(view is not None)
        if view is None:
            self.edit.clear()
            self.total.clear()
            return
        self.total.setText(f"/ {len(view.doc)}")
        if not self.edit.hasFocus():  # never rewrite a number the user is typing
            self.edit.setText(str(current_page(view) + 1))

    def focus(self):
        if self.view:
            self.edit.setFocus()
            self.edit.selectAll()

    def commit(self):
        if self.view and self.edit.text():
            self.shortcuts.go_to_page(int(self.edit.text()) - 1, self.view)
        self.leave()

    def leave(self):
        """Back to the document; the field shows the real page again."""
        view = self.view
        if view:
            view.setFocus()
        self.show_page(view)


class RenameEdit(QLineEdit):
    done = Signal(bool)  # True keeps the text

    def __init__(self, bar, text):
        super().__init__(text, bar, maxLength=MAX_LABEL)
        self.setAccessibleName("Paper tab name")
        self.returnPressed.connect(lambda: self.done.emit(True))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.done.emit(False)
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.done.emit(True)


class Shortcuts(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.editor, self.bound = window, None, set()
        self.actions = []
        for entry in TABLE:
            if entry.run:
                action = QAction(entry.label, window)
                action.setShortcuts([QKeySequence(key) for key in entry.keys])
                action.triggered.connect(lambda _=False, run=entry.run: run(self))
                window.addAction(action)
                self.actions.append(action)
        self.page_field = PageField(self)
        window.statusBar().addPermanentWidget(self.page_field)
        window.details.filter.installEventFilter(self)
        bar = window.tabs.tabBar()
        bar.installEventFilter(self)
        bar.setFocusPolicy(Qt.StrongFocus)  # a clicked title holds the focus that F2 needs, as a web tab button does
        rename = QShortcut(QKeySequence("F2"), bar, lambda: self.rename_tab(window.tabs.currentIndex()))
        rename.setContext(Qt.WidgetShortcut)  # only while the tab bar itself has focus
        window.refreshed.connect(self.refresh_page)
        window.tabs.currentChanged.connect(self.refresh_page)
        self.refresh_page()

    # ---- pages -----------------------------------------------------------------------------
    def view(self):
        pane = self.window.pane()
        return pane.view if pane else None

    def refresh_page(self, *_):
        for pane in self.window.all_panes():
            if pane.view not in self.bound:
                self.bound.add(pane.view)
                pane.view.moved.connect(lambda view=pane.view: view is self.view() and self.page_field.show_page(view))
        self.page_field.show_page(self.view())

    def go_to_page(self, page, view=None):
        """Zero-based page; numbers outside the document land on the first or last page."""
        view = view or self.view()
        if view:
            view.goto(max(0, min(page, len(view.doc) - 1)))

    def step_page(self, step):
        if view := self.view():
            self.go_to_page(current_page(view) + step, view)

    # ---- tabs and columns ------------------------------------------------------------------
    def step_tab(self, step):
        tabs = self.window.tabs
        if 0 <= tabs.currentIndex() + step < tabs.count():  # the ends do not wrap
            tabs.setCurrentIndex(tabs.currentIndex() + step)

    def close_tab(self):
        if self.window.tab():
            self.window.close_tab(self.window.tabs.currentIndex())

    def focus_document(self, view=None):
        view = view or (self.window.tab().main.view if self.window.tab() else None)
        if view:
            view.setFocus()

    def toggle_outline(self):
        window = self.window
        show = not window.outline_column.isVisibleTo(window)
        window.set_columns(outline=show)
        if show:
            window.outline.setFocus()
        else:
            self.focus_document()

    def focus_filter(self):
        window = self.window
        window.set_columns(details=True)
        window.comments.show_mode("references")
        window.details.filter.setFocus()
        window.details.filter.selectAll()

    # ---- renaming a tab --------------------------------------------------------------------
    def rename_tab(self, index):
        tab = self.window.tabs.widget(index)
        if tab is None or self.editor:
            return
        bar = self.window.tabs.tabBar()
        self.editor = RenameEdit(bar, tab.main.title)
        self.editor.setGeometry(bar.tabRect(index))
        self.editor.done.connect(lambda keep, editor=self.editor: self._renamed(editor, tab, keep))
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()

    def _renamed(self, editor, tab, keep):
        if editor is not self.editor:  # Enter hides the editor, whose focus loss reports a second time
            return
        self.editor = None
        text, had_focus = editor.text(), editor.hasFocus()
        editor.hide()
        editor.deleteLater()
        if had_focus:
            self.window.tabs.tabBar().setFocus()
        if keep and self.window.tabs.indexOf(tab) >= 0:
            self.set_label(tab.main, text)

    def set_label(self, pane, text):
        """Name a paper's tab. Library papers share the label with the web reader; loose files keep it for the session."""
        window, source, text = self.window, pane.view.doc.source, " ".join(text.split())
        try:
            if window.workspace and hasattr(source, "document_id"):
                library = window.workspace.library
                default = display_title({**library.get_document(source.document_id), "label": ""})
                title = display_title(library.set_label(source.document_id, "" if text == default else text))
            else:
                title = text or source.path.name
        except Exception as error:  # a failed rename must not interrupt reading
            window.statusBar().showMessage(f"Could not rename the tab: {error}", 8000)
            return
        for each in window.all_panes():
            if each.key == pane.key:
                each.view.doc.source.title = title
                each.refresh_bar()
        window.refresh()

    # ---- fields ----------------------------------------------------------------------------
    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonDblClick and watched is self.window.tabs.tabBar():
            index = watched.tabAt(event.position().toPoint())
            if index >= 0:
                self.rename_tab(index)
                return True
        elif event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
            if watched is self.page_field.edit:
                self.page_field.leave()
                return True
            if watched is self.window.details.filter:
                if watched.text():
                    watched.clear()
                else:
                    self.focus_document()
                return True
        return False


def install(window):
    """Install once; the maintainer's call in MainWindow.__init__ and a test may both ask."""
    if getattr(window, "shortcuts", None) is None:
        window.shortcuts = Shortcuts(window)
    return window.shortcuts
