"""Main window: one paper per tab, save state everywhere, and the Save/Discard/Cancel rules."""
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QMessageBox, QTabWidget

from .document import SaveState
from .pane import Pane
from .rules import CANCEL, DISCARD, SAVE, apply_decisions, collect_decisions, locked_by_other
from .source import FileSource
from .tab import DocTab

MARKERS = {SaveState.UNSAVED: " ●", SaveState.SAVING: " …", SaveState.FAILED: " !"}
WORST_FIRST = [SaveState.FAILED, SaveState.SAVING, SaveState.UNSAVED, SaveState.SAVED]
STYLE = """
QToolBar { background: #ece9e3; border: 0; border-bottom: 1px solid #cfcac1; spacing: 2px; }
QToolButton { padding: 3px 8px; border: 1px solid transparent; border-radius: 4px; }
QToolButton:hover { background: #dfdbd3; }
QToolButton:checked { background: #cfe3e0; border-color: #2f8f86; }
QTabBar::tab { padding: 5px 12px; }
#pane { border-top: 2px solid transparent; }
#pane[active="true"] { border-top-color: #2f8f86; }
"""


class MainWindow(QMainWindow):
    def __init__(self, client, settings=None):
        super().__init__()
        self.client = client
        self.settings = settings or QSettings("PaperAssist", "native")
        self.resize(1400, 900)
        self.setStyleSheet(STYLE)
        self.tabs = QTabWidget(tabsClosable=True, movable=True, documentMode=True)
        self.tabs.tabCloseRequested.connect(lambda index: self.close_tab(index))
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        self.setCentralWidget(self.tabs)
        self.state_label = QLabel()
        self.statusBar().addWidget(self.state_label)
        self._build_actions()
        self.refresh()

    def _build_actions(self):
        bar = self.addToolBar("Main")
        bar.setMovable(False)
        self.highlight_action = self._action(bar, "Highlight", ["H"], self._set_highlighting, checkable=True)
        for text, keys, slot in [
            ("Open", ["Ctrl+O"], self.open_dialog), ("Save", ["Ctrl+S"], self.save),
            ("Find", ["Ctrl+F"], lambda: self._on_pane(lambda p: p.find_bar.open())),
            ("Zoom out", ["Ctrl+-"], lambda: self._on_pane(lambda p: p.view.zoom_by(0.8))),
            ("Zoom in", ["Ctrl+=", "Ctrl++"], lambda: self._on_pane(lambda p: p.view.zoom_by(1.25))),
            ("Fit width", ["Ctrl+0"], lambda: self._on_pane(lambda p: p.view.fit_width())),
            ("Back", ["Alt+Left"], lambda: self._on_pane(lambda p: p.view.go_back())),
            ("Quick view", ["Ctrl+E"], self.quick_dialog), ("Close tab", ["Ctrl+W"], lambda: self.close_tab(self.tabs.currentIndex())),
        ]:
            self._action(bar, text, keys, slot)

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
        return pane

    def _add_tab(self, tab):
        self.tabs.setCurrentIndex(self.tabs.addTab(tab, ""))
        self.refresh()
        return tab

    def open_source(self, source):
        """Open a paper in a tab; a paper that already has a tab is selected instead of opened twice."""
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).main.key == source.key:
                self.tabs.setCurrentIndex(i)
                return self.tabs.widget(i)
        return self._add_tab(self._wrap(self._new_pane(source)))

    def _wrap(self, main):
        return DocTab(main, self.settings.value("quick_side_by_side", False, bool))

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
        if tab.quick:
            self.close_quick(ask=False)
        pane = self._new_pane(source)
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
        side_by_side = not self.tab().side_by_side
        self.settings.setValue("quick_side_by_side", side_by_side)  # remembered for the next session
        for i in range(self.tabs.count()):
            self.tabs.widget(i).set_side_by_side(side_by_side)

    def promote_quick(self):
        """Move the quick pane into its own tab with its page, zoom and unsaved edits."""
        tab = self.tab()
        owner = next((t for t in map(self.tabs.widget, range(self.tabs.count())) if t is not tab and t.main.key == tab.quick.key), None)
        if owner and not tab.quick.modified:  # that tab already is the editable owner
            self.close_quick(ask=False)
            self.tabs.setCurrentWidget(owner)
        else:
            self._add_tab(self._wrap(tab.take_quick()))

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
            self.tabs.setTabText(i, tab.main.title[:28] + MARKERS.get(state, ""))
            self.tabs.setTabToolTip(i, f"{tab.main.title} — {state.value}")
        pane = self.pane()
        if pane is None:
            self.setWindowTitle("PaperAssist")
            self.state_label.clear()
            return
        detail = f"{pane.state.value}: {pane.view.doc.error}. Use Save to retry." if pane.state is SaveState.FAILED else pane.state.value
        if pane.view.locked:
            detail = "Read-only, last saved version"
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
        decisions = collect_decisions(panes, self.ask_decision)
        return decisions is not None and apply_decisions(decisions, self._save_pane)

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
            pane.release()
        event.accept()
