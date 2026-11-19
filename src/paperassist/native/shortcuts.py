"""Keyboard shortcuts of the native reader, and the help dialog that lists them.

One table installs the bindings and renders the help, so the two cannot drift. Plain keys
stay out of text fields because Qt lets a line edit claim printable keys before a shortcut
is matched, and window shortcuts do not fire while a modal dialog is the active window.
Ctrl+T, Ctrl+W, Ctrl+Tab and Alt+arrows are not bound here, and Preferences belongs to the app menu.
"""
import html
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser

from .dialogs import setup_dialog
from .theme import INK, LINE, MUTED
from .toolbar import go_to_page, step_page


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
    Entry("Navigation", ("G",), "Go to page: type a number, then Enter", lambda s: s.focus_page()),
    Entry("Navigation", ("Up", "Down"), "Scroll the document (click it first)"),
    Entry("Navigation", ("Space", "Shift+Space"), "Scroll a screen down or up; hold Space + drag or middle-drag to pan"),
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


def _format_keys(keys):
    badges = [
        f"<span style='background:#ffffff; border:1px solid #c9d4ca; padding:1px 5px; font-weight:600;'>"
        f"{html.escape(k)}</span>"
        for k in keys
    ]
    return " / ".join(badges)


def show_help(window):
    sections = {}
    for entry in TABLE:
        sections.setdefault(entry.group, []).append(([sequence_text(k) for k in entry.keys], entry.label))
    sections["Toolbar and menu"] = other_commands(window, window.shortcuts.actions)
    body = f"<div style='color:{INK};'>" + "".join(
        f"<p style='margin:14px 0 4px 0; color:{MUTED}; font-weight:700; text-transform:uppercase;'>"
        f"{html.escape(group)}</p>"
        f"<hr style='border:0; background:{LINE}; height:1px; margin:0 0 6px 0;'/>"
        f"<table width='100%' cellspacing='0' cellpadding='4'>" + "".join(
            f"<tr><td>{html.escape(label)}</td>"
            f"<td align='right' style='white-space:nowrap;'>{_format_keys(keys)}</td></tr>"
            for keys, label in rows
        ) + "</table>"
        for group, rows in sections.items()
    ) + "</div>"
    dialog = QDialog(window, objectName="shortcuts-help")
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.resize(640, 640)
    layout, _ = setup_dialog(dialog, "Keyboard shortcuts",
                             subtitle="Letter keys work when no text field has focus.")
    browser = QTextBrowser(html=body, objectName="shortcuts-list")
    browser.setAccessibleName("Keyboard shortcuts list")
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(browser, 1)
    layout.addWidget(buttons)
    dialog.open()
    return dialog


class Shortcuts(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.page_field = window.page_field
        self.actions = []
        for entry in TABLE:
            if entry.run:
                action = QAction(entry.label, window)
                action.setShortcuts([QKeySequence(key) for key in entry.keys])
                action.triggered.connect(lambda _=False, run=entry.run: run(self))
                window.addAction(action)
                self.actions.append(action)
        window.details.filter.installEventFilter(self)

    # ---- pages -----------------------------------------------------------------------------
    def view(self):
        pane = self.window.pane()
        return pane.view if pane else None

    def focus_page(self):
        pane = self.window.pane()
        if pane is None:
            return
        field = pane.navigation_field if pane.quick else self.window.page_field
        bar = pane.navigation_bar if pane.quick else self.window.main_bar
        if bar.overflow.button.isVisible() and bar.overflow.is_hidden(field):
            bar.overflow.popup()
            submenu = next((menu for menu in bar.overflow.generated_menus if menu.title() == f'{field.prefix}: page number'), None)
            if submenu:
                submenu.popup(bar.overflow.menu.mapToGlobal(bar.overflow.menu.rect().topRight()))
                submenu.actions()[0].defaultWidget().focus()
        else:
            field.focus()

    def go_to_page(self, page, view=None):
        go_to_page(view or self.view(), page)

    def step_page(self, step, view=None):
        step_page(view or self.view(), step)

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

    # ---- fields ----------------------------------------------------------------------------
    def eventFilter(self, watched, event):
        if (event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape
                and event.modifiers() == Qt.NoModifier and watched is self.window.details.filter):
            if watched.text():
                watched.clear()
            else:
                self.focus_document()
            return True
        return False
