"""Keyboard shortcuts of the native reader, and the help dialog that lists them.

One table installs the bindings and renders the help, so the two cannot drift. Plain keys
stay out of text fields because Qt lets a line edit claim printable keys before a shortcut
is matched, and window shortcuts do not fire while a modal dialog is the active window.
Ctrl+T, Ctrl+W, Ctrl+Tab and Alt+arrows are not bound here, and Preferences belongs to the app menu.
"""
import html
from weakref import WeakSet
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QAction, QIntValidator, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
                               QTextBrowser, QVBoxLayout, QWidget, QWidgetAction)

from paperassist.library import MAX_LABEL

from .positions import current_page
from .theme import DIALOG_BG, INK, LINE, MUTED, icon, serif_heading_font
from .toolbar import reserve_zoom_width


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
    dialog = QDialog(window)
    dialog.setObjectName("shortcuts-help")
    dialog.setWindowTitle("Keyboard shortcuts")
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.resize(640, 640)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(10)
    heading = QLabel("Keyboard shortcuts")
    heading.setProperty("headingScale", "dialog")
    heading.setFont(serif_heading_font(dialog.font()))
    browser = QTextBrowser(html=body)
    browser.setAccessibleName("Keyboard shortcuts list")
    browser.setStyleSheet(f"QTextBrowser {{ background: {DIALOG_BG}; border: 0; }}")
    note = QLabel("Letter keys work when no text field has focus.", objectName="muted-note")
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    for widget in (heading, note, browser, buttons):
        layout.addWidget(widget, 1 if widget is browser else 0)
    dialog.open()
    return dialog


class PageField(QWidget):
    """Current page and total for one document, grouped with page step buttons."""

    def __init__(self, shortcuts, prefix="Main paper"):
        super().__init__()
        self.shortcuts, self.view, self.prefix = shortcuts, None, prefix
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.toolbar_actions = []
        self.edit = QLineEdit(alignment=Qt.AlignCenter, toolTip="Go to page (G)")
        self.edit.setAccessibleName(f"{prefix}: page number")
        self.edit.setValidator(QIntValidator(1, 99999))
        self.edit.setMinimumWidth(46)
        self.edit.setMaximumWidth(62)
        self.total = QLabel(objectName="muted-note")
        self.total.setFixedWidth(self.total.fontMetrics().horizontalAdvance('/ 99999') + 4)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(5)
        row.addWidget(self.edit)
        row.addWidget(self.total)
        self.edit.returnPressed.connect(self.commit)
        self.edit.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.edit and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.leave()
            return True
        return super().eventFilter(watched, event)

    def show_page(self, view):
        self.view = view
        self.total.setFixedWidth(self.total.fontMetrics().horizontalAdvance('/ 99999') + 4)
        shown = view is not None
        for action in self.toolbar_actions:
            action.setVisible(shown)
        self.setVisible(shown)
        self.setEnabled(shown)
        if not shown:
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

    def add_overflow_actions(self, menu):
        submenu = menu.addMenu(f'{self.prefix}: page number')
        field = PageField(self.shortcuts, self.prefix)
        field.show_page(self.view)
        action = QWidgetAction(submenu)
        action.setDefaultWidget(field)
        submenu.addAction(action)
        return submenu


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
        self.window, self.editor, self.bound = window, None, WeakSet()
        self.actions = []
        for entry in TABLE:
            if entry.run:
                action = QAction(entry.label, window)
                action.setShortcuts([QKeySequence(key) for key in entry.keys])
                action.triggered.connect(lambda _=False, run=entry.run: run(self))
                window.addAction(action)
                self.actions.append(action)
        self.page_field = PageField(self)
        self._place_page_controls(window)
        window.details.filter.installEventFilter(self)
        bar = window.tabs.tabBar()
        bar.installEventFilter(self)
        bar.setFocusPolicy(Qt.StrongFocus)  # a clicked title holds the focus that F2 needs, as a web tab button does
        rename = QShortcut(QKeySequence("F2"), bar, lambda: self.rename_tab(window.tabs.currentIndex()))
        rename.setContext(Qt.WidgetShortcut)  # only while the tab bar itself has focus
        window.refreshed.connect(self.refresh_page)
        window.tabs.currentChanged.connect(self.refresh_page)
        self.refresh_page()

    def _place_page_controls(self, window):
        main_bar = getattr(window, "main_bar", None)
        anchor = getattr(window, "page_anchor_action", None)
        if main_bar is None or anchor is None:
            window.statusBar().addPermanentWidget(self.page_field)
            return
        prev = QAction(icon('chevron_left', INK, 15), 'Main paper: previous page', window)
        prev.triggered.connect(lambda: self.step_page(-1, self.main_view()))
        following = QAction(icon('chevron_right', INK, 15), 'Main paper: next page', window)
        following.triggered.connect(lambda: self.step_page(1, self.main_view()))
        self.page_field.toolbar_actions = [
            main_bar.insertAction(anchor, prev) or prev,
            main_bar.insertWidget(anchor, self.page_field),
            main_bar.insertAction(anchor, following) or following,
            main_bar.insertSeparator(anchor),
        ]

    # ---- pages -----------------------------------------------------------------------------
    def view(self):
        pane = self.window.pane()
        return pane.view if pane else None

    def main_view(self):
        return self.window.tab().main.view if self.window.tab() else None

    def focus_page(self):
        pane = self.window.pane()
        if pane is None:
            return
        field = getattr(pane, 'navigation_field', None) if pane and pane.quick else self.page_field
        if field:
            bar = pane.navigation_bar if pane and pane.quick else self.window.main_bar
            if bar.overflow.button.isVisible() and bar.overflow.is_hidden(field):
                bar.overflow.popup()
                submenu = next((menu for menu in bar.overflow.generated_menus if menu.title() == f'{field.prefix}: page number'), None)
                if submenu:
                    submenu.popup(bar.overflow.menu.mapToGlobal(bar.overflow.menu.rect().topRight()))
                    submenu.actions()[0].defaultWidget().focus()
            else:
                field.focus()

    def _make_quick_controls(self, pane):
        bar, view = pane.navigation_bar, pane.view
        before = set(bar.actions())
        anchor = pane.quick_buttons[0].defaultAction()
        def button(text, glyph, slot):
            action = QAction(icon(glyph, INK, 15), f'Second paper: {text}', bar)
            action.triggered.connect(slot)
            bar.insertAction(anchor, action)
            widget = bar.widgetForAction(action)
            widget.setToolButtonStyle(Qt.ToolButtonIconOnly)
            widget.setAccessibleName(action.text())
            return action

        button('previous page', 'chevron_left', lambda: self.step_page(-1, view))
        pane.navigation_field = PageField(self, 'Second paper')
        bar.insertWidget(anchor, pane.navigation_field)
        button('next page', 'chevron_right', lambda: self.step_page(1, view))
        button('find', 'find', pane.find_bar.open)
        button('zoom out', 'minus', lambda: view.zoom_by(0.8))
        pane.navigation_fit = QAction('Fit width', bar)
        pane.navigation_fit.triggered.connect(view.fit_width)
        bar.insertAction(anchor, pane.navigation_fit)
        pane.navigation_fit.setToolTip('Second paper: fit width')
        fit_button = bar.widgetForAction(pane.navigation_fit)
        fit_button.setAccessibleName('Second paper: fit width')
        button('zoom in', 'plus', lambda: view.zoom_by(1.25))
        pane.navigation_actions = [action for action in bar.actions() if action not in before]
        pane._fit_title()

    def _refresh_quick_controls(self, pane):
        if not hasattr(pane, 'navigation_field'):
            self._make_quick_controls(pane)
        pane.navigation_field.show_page(pane.view)
        text = 'Fit width' if pane.view.fit else f'{round(pane.view.zoom * 100)}%'
        button = pane.navigation_bar.widgetForAction(pane.navigation_fit)
        reserve_zoom_width(pane.navigation_fit, button, text)

    def refresh_page(self, *_):
        for pane in self.window.all_panes():
            if pane.view not in self.bound:
                self.bound.add(pane.view)
                pane.view.moved.connect(self._on_view_moved)
            if pane.quick:
                self._refresh_quick_controls(pane)
        self.page_field.show_page(self.main_view())

    def _on_view_moved(self):
        self.refresh_page()
        if hasattr(self.window, "sync_zoom_label"):
            self.window.sync_zoom_label()

    def go_to_page(self, page, view=None):
        """Zero-based page; numbers outside the document land on the first or last page."""
        view = view or self.view()
        if view:
            view.goto(max(0, min(page, len(view.doc) - 1)))

    def step_page(self, step, view=None):
        if view := view or self.view():
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
        self.editor = RenameEdit(bar, tab.title)
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
            self.set_label(tab, text)

    def set_label(self, tab, text):
        """Set a session-only name for this tab without changing its paper."""
        tab.label = " ".join(text.split())
        self.window.refresh()

    # ---- fields ----------------------------------------------------------------------------
    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonDblClick and watched is self.window.tabs.tabBar():
            index = watched.tabAt(event.position().toPoint())
            if index >= 0:
                self.rename_tab(index)
                return True
        elif event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
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
