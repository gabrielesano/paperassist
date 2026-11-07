"""Main window: one paper per tab, save state everywhere, and the Save/Discard/Cancel rules."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QMainWindow, QMessageBox, QSplitter, QStackedWidget, QTabBar, QTabWidget, QToolBar

from paperassist import settings

from .annotation_bar import install as install_annotation_bar
from .acquisition import AcquisitionController
from .app_menu import install as install_app_menu
from .comments import install as install_comments
from .document import SaveState
from .library_dialog import LibraryDialog
from .library_source import LibrarySource
from .outline import OutlinePanel, read_outline
from .pane import Pane
from .positions import PositionKeeper, current_page
from .references_panel import ReferencesPanel
from .rules import CANCEL, DISCARD, SAVE, apply_decisions, collect_decisions, locked_by_other
from .shortcuts import install as install_shortcuts
from .sidebar import DetailsPanel, SideColumn
from .source import FileSource
from .tab import DocTab
from .toolbar import install_overflow

MARKERS = {SaveState.UNSAVED: " ●", SaveState.SAVING: " …", SaveState.FAILED: " !"}
WORST_FIRST = [SaveState.FAILED, SaveState.SAVING, SaveState.UNSAVED, SaveState.SAVED]
STYLE = """
QToolBar { background: #ece9e3; border: 0; border-bottom: 1px solid #cfcac1; spacing: 2px; }
QToolButton { padding: 3px 8px; border: 1px solid transparent; border-radius: 4px; }
QToolButton#qt_toolbar_ext_button { padding: 0px; border: 0px; }
QToolButton:hover { background: #dfdbd3; }
QToolButton:checked { background: #cfe3e0; border-color: #2f8f86; }
QTabBar::tab { padding: 5px 12px; }
#pane { border-top: 2px solid transparent; }
#pane[active="true"] { border-top-color: #2f8f86; }
"""


class MainWindow(QMainWindow):
    refreshed = Signal()  # documents, panes or save states changed; the page field follows it

    def __init__(self, client, workspace=None):
        """workspace is the opened library, or None for loose files."""
        super().__init__()
        self.client, self.workspace, self.library_dialog, self.switch_target = client, workspace, None, None
        saved = settings.preferences()  # shared with the web reader
        self.side_by_side = saved.get("quick_view_layout") == "side"
        self.resize(1400, 900)
        self.setMinimumSize(800, 600)
        self.setStyleSheet(STYLE)
        self.tabs = QTabWidget(tabsClosable=True, movable=True, documentMode=True)
        self.tabs.tabCloseRequested.connect(lambda index: self.close_tab(index))
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        self._build_columns(saved)
        self.state_label = QLabel()
        self.statusBar().addWidget(self.state_label)
        self._build_actions()
        self.annotation_bar = install_annotation_bar(self)
        for toolbar in self.findChildren(QToolBar):
            install_overflow(toolbar)
        install_shortcuts(self)
        install_app_menu(self)  # also Preferences, which applies the saved interface size, and About
        self.comments.draft_changed.connect(self.refresh)
        QApplication.instance().focusChanged.connect(self._focus_pane)
        self.refresh()

    def _build_actions(self):
        bar = self.addToolBar("Main")
        bar.setMovable(False)
        self.highlight_action = self._action(bar, "Highlight", ["H"], self._set_highlighting, checkable=True)
        for text, keys, slot in [
            ("Open", ["Ctrl+O"], self.open_dialog), ("Library", ["Ctrl+L"], self.show_library),
            ("Panels", ["Ctrl+B"], self.toggle_columns), ("Save", ["Ctrl+S"], self.save),
            ("Find", ["Ctrl+F"], lambda: self._on_pane(lambda p: p.find_bar.open())),
            ("Zoom out", ["Ctrl+-"], lambda: self._on_pane(lambda p: p.view.zoom_by(0.8))),
            ("Zoom in", ["Ctrl+=", "Ctrl++"], lambda: self._on_pane(lambda p: p.view.zoom_by(1.25))),
            ("Fit width", ["Ctrl+0"], lambda: self._on_pane(lambda p: p.view.fit_width())),
            ("Back", ["Alt+Left"], lambda: self._on_pane(lambda p: p.view.go_back())),
            ("Quick view", ["Ctrl+E"], self.quick_dialog), ("Close tab", ["Ctrl+W"], lambda: self.close_tab(self.tabs.currentIndex())),
        ]:
            self._action(bar, text, keys, slot)

    # ---- side columns ----------------------------------------------------------------------
    def _build_columns(self, saved):
        self.outline = OutlinePanel()
        self.outline.navigate.connect(lambda page, y: self.tab() and self.tab().main.view.goto(page, y))
        self.details = ReferencesPanel(self.workspace)
        self.details.open_requested.connect(self.open_library_document_in_quick_view)
        self.comments = install_comments(self)
        self.detail_switch = DetailsPanel(self.details, self.comments.panel)
        self.acquisition = AcquisitionController(self)
        self.details.set_acquisition(self.acquisition)
        self.details.references_loaded.connect(self.acquisition.set_reference_data)
        self.acquisition.open_requested.connect(self.open_library_document_in_quick_view)
        self.acquisition.preferences_requested.connect(self._acquisition_preferences)
        self.details.reveal_requested.connect(lambda: self.comments.show_mode("references"))
        self.outline_column, self.details_column = SideColumn("Outline", self.outline), SideColumn("References / Comments", self.detail_switch)
        self.outline_column.hide_requested.connect(lambda: self.set_columns(outline=False))
        self.details_column.hide_requested.connect(lambda: self.set_columns(details=False))
        self.outline_column.setVisible(saved.get("outline_expanded") is not False)
        self.details_column.setVisible(saved.get("details_expanded") is not False)
        self._shown_for, self.outlines = {}, {}  # column -> key of the paper it shows; bookmarks by paper key
        splitter = QSplitter(Qt.Horizontal, childrenCollapsible=False)
        self.reader_stack = QStackedWidget()
        self.welcome = QLabel("Open a PDF or choose a paper from Library to start reading.",
                              alignment=Qt.AlignCenter, wordWrap=True)
        self.welcome.setAccessibleName("Welcome to PaperAssist")
        self.reader_stack.addWidget(self.welcome)
        self.reader_stack.addWidget(self.tabs)
        for widget in (self.outline_column, self.reader_stack, self.details_column):
            splitter.addWidget(widget)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 840, 330])
        self.setCentralWidget(splitter)

    def set_columns(self, outline=None, details=None):
        changes = {}
        for column, name, visible in ((self.outline_column, "outline_expanded", outline),
                                      (self.details_column, "details_expanded", details)):
            if visible is not None:
                column.setVisible(visible)
                changes[name] = visible
        self._remember(**changes)
        self._update_columns()

    def toggle_columns(self):
        """Hide both columns when both show; otherwise show both, which also restores a lone collapsed one."""
        both = self.outline_column.isVisibleTo(self) and self.details_column.isVisibleTo(self)
        self.set_columns(outline=not both, details=not both)

    def _remember(self, **changes):
        try:
            settings.update_preferences(changes)
        except (OSError, ValueError) as error:
            self.statusBar().showMessage(f"Could not save the interface preferences: {error}", 8000)

    def _update_columns(self):
        """Fill the visible columns for the main paper of the current tab, once per paper."""
        main = self.tab().main if self.tab() else None
        key = main.key if main else None
        if self.outline_column.isVisibleTo(self) and self._shown_for.get("outline", False) != key:
            self._shown_for["outline"] = key
            self.outline.show_outline(self._outline_entries(main) if main else [], len(main.view.doc) if main else 0)
        self.details.bind_view(main.view if main else None)
        if self._shown_for.get("details", False) != key:
            self._shown_for["details"] = key
            self.details.show_for(getattr(main.view.doc.source, "document_id", None) if main else None)
        self.comments.refresh()

    def _acquisition_preferences(self, field):
        callback = getattr(self, "show_preferences", None)
        if callback:
            callback(field)
        else:
            self.statusBar().showMessage("Configure the downloads folder in Preferences before checking it.", 8000)

    def _outline_entries(self, main):
        if main.key not in self.outlines:  # reading a big PDF's bookmarks takes a moment; edits never change them
            self.outlines[main.key] = read_outline(main.view.doc.pdf.data)
        return self.outlines[main.key]

    # ---- library --------------------------------------------------------------------------
    def show_library(self):
        if self.workspace is None:
            self.statusBar().showMessage("Start PaperAssist without file arguments to open the library.", 6000)
            return
        if self.library_dialog is None:
            self.library_dialog = LibraryDialog(self.workspace, self)
            self.library_dialog.open_requested.connect(self.open_library_document)
            self.library_dialog.quick_requested.connect(self.open_library_document_in_quick_view)
        self.library_dialog.show()
        self.library_dialog.raise_()
        self.library_dialog.activateWindow()

    def library_source(self, document_id):
        return LibrarySource(self.workspace.library, self.workspace.library.get_document(document_id))

    def open_library_document(self, document_id):
        return self.open_source(self.library_source(document_id))

    def open_library_document_in_quick_view(self, document_id):
        source = self.library_source(document_id)
        if not self.tab():
            return self.open_source(source)
        if self.tab().quick and self.tab().quick.key == source.key:
            return self.tab().quick  # clicking the paper that is already there changes nothing
        return self.open_quick(source)

    def _focus_pane(self, previous, current):
        if current is not None and (tab := self.tab()):
            for pane in tab.panes():
                if pane is current or pane.isAncestorOf(current):
                    tab.activate(pane)
                    break

    def _on_pane(self, action):
        if self.pane():
            action(self.pane())

    def _action(self, bar, text, keys, slot, checkable=False):
        action = QAction(text, self, checkable=checkable)
        action.setShortcuts([QKeySequence(key) for key in keys])
        action.setToolTip(f"{text} ({keys[0]})")
        action.toggled.connect(slot) if checkable else action.triggered.connect(lambda _=False: slot())
        bar.addAction(action)
        return action

    # ---- what is open --------------------------------------------------------------------
    def tab(self):
        return self.tabs.currentWidget()

    def pane(self):
        """The pane the toolbar acts on: the one last clicked in the current tab, or None."""
        return self.tab().active if self.tab() else None

    def all_panes(self):
        return [pane for i in range(self.tabs.count()) for pane in self.tabs.widget(i).panes()]

    def _new_pane(self, source):
        pane = Pane(source, self.client)
        pane.view.highlighting = self.highlight_action.isChecked()
        pane.view.edited.connect(self.refresh)
        self.comments.bind(pane)
        if self.workspace and hasattr(source, "document_id"):
            PositionKeeper(pane, self.workspace).failed.connect(lambda message: self.statusBar().showMessage(message, 8000))
        return pane

    def _add_tab(self, tab):
        self.tabs.setCurrentIndex(self.tabs.addTab(tab, ""))
        self.refresh()
        document_id = getattr(tab.main.view.doc.source, "document_id", None)
        if document_id is not None:
            self.acquisition.main_opened(document_id)
        return tab

    def open_source(self, source):
        """Open a paper in a tab; a paper that already has a tab is selected instead of opened twice."""
        matches = [(self.tabs.widget(i), pane) for i in range(self.tabs.count())
                   for pane in self.tabs.widget(i).panes() if pane.key == source.key]
        if matches:
            tab, pane = next((pair for pair in matches if pair[1].modified), matches[0])
            self.tabs.setCurrentWidget(tab)
            tab.activate(pane)
            pane.view.setFocus()
            return tab
        pane = self._open_pane(source)
        return self._add_tab(self._wrap(pane)) if pane else None

    def _open_pane(self, source):
        try:
            pane = self._new_pane(source)
        except Exception as error:
            self.statusBar().showMessage(f"Could not open {source.title}: {error}")
            return None
        self.statusBar().clearMessage()  # an earlier failure no longer describes what is open
        return pane

    def _wrap(self, main):
        tab = DocTab(main, self.side_by_side)
        tab.active_changed.connect(self.refresh)
        return tab

    def _set_highlighting(self, on):
        for pane in self.all_panes():
            pane.view.highlighting = on

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF (*.pdf)")
        if path:
            self.open_source(FileSource(path))

    # ---- quick view -----------------------------------------------------------------------
    def quick_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Quick view", "", "PDF (*.pdf)")
        if path and self.tab():
            self.open_quick(FileSource(path))

    def open_quick(self, source):
        tab = self.tab()
        if tab.quick and not self.confirm([tab.quick]):  # replacing a modified quick view asks first
            return None
        pane = self._open_pane(source)
        if pane is None:
            return None
        if tab.quick:
            self.close_quick(ask=False)
        pane.quick_layout_toggled.connect(self.toggle_quick_layout)
        pane.promote_requested.connect(self.promote_quick)
        pane.close_requested.connect(self.close_quick)
        tab.set_quick(pane)
        self.refresh()
        return pane

    def close_quick(self, ask=True):
        tab = self.tab()
        if tab and tab.quick and (not ask or self.confirm([tab.quick])):
            tab.close_quick()
            self.refresh()

    def toggle_quick_layout(self):
        self.side_by_side = not self.tab().side_by_side
        self._remember(quick_view_layout="side" if self.side_by_side else "stacked")
        for i in range(self.tabs.count()):
            self.tabs.widget(i).set_side_by_side(self.side_by_side)

    def promote_quick(self):
        """Move the quick pane into its own tab with its page, zoom and unsaved edits.

        A clean view of an already-open paper returns to its owner at the preview's page,
        fitted to the owner's width, without creating a second tab.
        """
        quick = self.tab().quick
        others = [pane for pane in self.all_panes() if pane is not quick and pane.key == quick.key]
        if others and not quick.modified and not self.comments.has_draft(quick):
            source = quick.view.doc.source
            page = current_page(quick.view)
            self.close_quick(ask=False)
            self.open_source(source)
            view = self.pane().view
            view.fit_width()
            view.goto(page)
        else:
            self._add_tab(self._wrap(self.tab().take_quick()))

    # ---- save state -----------------------------------------------------------------------
    def refresh(self, *_):
        """Recompute locks, tab labels, window title and status line from the documents."""
        panes = self.all_panes()
        for pane in panes:
            locked = locked_by_other(pane, panes)
            if pane.view.locked and not locked:
                pane.view.reload()  # the other view saved: this one showed an older version
            pane.set_locked(locked)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            state = min((p.state for p in tab.panes()), key=WORST_FIRST.index)
            draft = any(self.comments.has_draft(p) for p in tab.panes())
            self.tabs.setTabText(i, tab.main.title[:28].replace("&", "&&") + (MARKERS.get(state, "") or (" ●" if draft else "")))
            self.tabs.setTabToolTip(i, f"{tab.main.title} — {state.value}")
            self.tabs.tabBar().setAccessibleTabName(i, f"{tab.main.title} — {state.value}"
                                                   + (" — Comment draft" if draft else ""))
            for side in (QTabBar.LeftSide, QTabBar.RightSide):
                if button := self.tabs.tabBar().tabButton(i, side):
                    button.setAccessibleName(f"Close {tab.main.title}")
        if hasattr(self, "annotation_bar"):  # refresh can run while the window is being built
            self.annotation_bar.refresh()
        self._update_columns()
        self.refreshed.emit()
        pane = self.pane()
        self.reader_stack.setCurrentWidget(self.tabs if pane else self.welcome)
        if pane is None:
            self.setWindowTitle("PaperAssist")
            self.state_label.clear()
            return
        detail = f"{pane.state.value}: {pane.view.doc.error}. Use Save to retry." if pane.state is SaveState.FAILED else pane.state.value
        if pane.view.locked:
            detail = "Read-only, last saved version"
        if self.comments.has_draft(pane):
            detail += " · Comment draft"
        self.state_label.setText(f"{pane.title} — {detail}")
        marker = MARKERS.get(pane.state, "").strip()
        self.setWindowTitle(f"{marker} {pane.title} — PaperAssist".strip() if marker else f"{pane.title} — PaperAssist")

    def save(self):
        pane = self.pane()
        if pane and pane.modified and not pane.view.locked:
            self._save_pane(pane)

    def _save_pane(self, pane):
        saved = pane.save()
        if not saved:
            self.report_failure(pane)
        return saved

    def report_failure(self, pane):
        QMessageBox.warning(self, "Save failed", f"{pane.title} could not be saved:\n{pane.view.doc.error}\n\n"
                            "Your changes are still open. Fix the problem and save again.")

    # ---- closing ----------------------------------------------------------------------------
    def ask_decision(self, pane):
        box = QMessageBox(QMessageBox.Question, "Unsaved changes", f"Save changes to {pane.title}?",
                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, self)
        box.setInformativeText("Your changes will be lost if you discard them.")
        return {QMessageBox.Save: SAVE, QMessageBox.Discard: DISCARD}.get(box.exec(), CANCEL)

    def confirm(self, panes):
        """True when closing these panes may go ahead: every modified one was saved or discarded."""
        for pane in panes:
            pane.view.finish_interaction()
        if any(pane.state is SaveState.SAVING for pane in panes):  # a close request can arrive while the save repaints
            self.statusBar().showMessage("Wait for the save to finish before closing.", 6000)
            return False
        decisions = collect_decisions(panes, self.ask_decision,
                                      lambda p: p.modified or self.comments.has_draft(p))
        if decisions is None or not apply_decisions(decisions, self._save_pending):
            return False
        for pane, answer in decisions:
            if answer == DISCARD:
                self.comments.discard_draft(pane)
        return True

    def _save_pending(self, pane):
        if pane.modified and not self._save_pane(pane):
            return False
        return not self.comments.has_draft(pane) or self.comments.save_draft(pane)

    def close_tab(self, index):
        tab = self.tabs.widget(index)
        if tab is None or not self.confirm(tab.panes()):
            return False
        self.tabs.removeTab(index)
        for pane in tab.panes():
            pane.release()
        tab.deleteLater()
        self.refresh()
        return True

    def closeEvent(self, event):
        if not self.confirm(self.all_panes()):
            return event.ignore()
        for pane in self.all_panes():
            pane.release()  # also stores the reading positions
        self.details.close()
        self.acquisition.close()
        for dialog in self.findChildren(QDialog):
            dialog.close()
        event.accept()
