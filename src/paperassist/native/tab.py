"""One tab: the main paper and, optionally, one quick-view paper in a resizable split."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QSplitter, QSplitterHandle

NARROW = 720  # below this width the quick view always goes underneath
KEY_STEP = 40


class Handle(QSplitterHandle):
    """Separator that can be moved with the arrow keys and Home/End, as the specification asks."""

    def __init__(self, orientation, splitter):
        super().__init__(orientation, splitter)
        self.setFocusPolicy(Qt.TabFocus)

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and event.key() in (Qt.Key_Home, Qt.Key_End):
            event.accept()  # the separator moves with these keys; the page shortcuts must not take them
        return super().event(event)

    def keyPressEvent(self, event):
        before, after = (Qt.Key_Up, Qt.Key_Down) if self.orientation() == Qt.Vertical else (Qt.Key_Left, Qt.Key_Right)
        splitter = self.splitter()
        index, position, total = splitter.indexOf(self), splitter.sizes()[0], sum(splitter.sizes())
        target = {before: position - KEY_STEP, after: position + KEY_STEP, Qt.Key_Home: round(total * 0.8), Qt.Key_End: round(total * 0.25)}.get(event.key())
        if target is None:
            return super().keyPressEvent(event)
        splitter.moveSplitter(max(0, min(target, total)), index)
        splitter.splitterMoved.emit(splitter.sizes()[0], index)


class DocTab(QSplitter):
    active_changed = Signal()

    def __init__(self, main, side_by_side=False):
        super().__init__(Qt.Vertical)
        self.main, self.quick, self.active = main, None, main
        self.side_by_side, self._narrow = side_by_side, False
        self.shares = {Qt.Vertical: 0.4, Qt.Horizontal: 0.5}
        self.setChildrenCollapsible(False)
        self.addWidget(main)
        main.view.activated.connect(lambda: self.activate(main))
        self.splitterMoved.connect(self._remember_share)

    def createHandle(self):
        return Handle(self.orientation(), self)

    def panes(self):
        return [self.main] + ([self.quick] if self.quick else [])

    def activate(self, pane):
        if pane not in self.panes():  # a promoted or closed pane may still emit
            return
        self.active = pane
        for each in self.panes():
            each.set_active(pane is each and len(self.panes()) > 1)
        self.active_changed.emit()

    # ---- quick view -----------------------------------------------------------------
    def set_quick(self, pane):
        self.quick = pane
        pane.set_quick(True)
        pane.view.activated.connect(lambda: self.activate(pane))
        pane.setMinimumSize(160, 120)
        self.main.setMinimumSize(160, 120)
        self.addWidget(pane)
        self._arrange()
        self.activate(pane)

    def take_quick(self):
        """Detach the quick pane with its document, scroll, zoom and edits, for promotion to a tab."""
        pane, self.quick = self.quick, None
        pane.setParent(None)
        pane.set_quick(False)
        self.activate(self.main)
        return pane

    def close_quick(self):
        pane = self.take_quick()
        pane.release()
        pane.deleteLater()

    def set_side_by_side(self, side_by_side):
        self.side_by_side = side_by_side
        self._arrange()

    def _vertical(self):
        return self.quick is None or not self.side_by_side or self.width() < NARROW

    def _arrange(self):
        vertical, self._narrow = self._vertical(), self.width() < NARROW
        self.setOrientation(Qt.Vertical if vertical else Qt.Horizontal)
        for pane in self.panes():
            pane.set_vertical_split(vertical, narrow=self._narrow)
        if self.quick:
            total = (self.height() if vertical else self.width()) or 1000
            self.setSizes([round(total * (1 - self.quick_share)), round(total * self.quick_share)])

    @property
    def quick_share(self):
        return self.shares[self.orientation()]

    def _remember_share(self):
        sizes = self.sizes()
        if len(sizes) == 2 and sum(sizes):
            share = max(0.2, min(0.75, sizes[1] / sum(sizes)))
            self.shares[self.orientation()] = share
            if share != sizes[1] / sum(sizes):
                total = sum(sizes)
                self.setSizes([round(total * (1 - share)), round(total * share)])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.quick and (self._vertical() != (self.orientation() == Qt.Vertical) or (self.width() < NARROW) != self._narrow):
            self._arrange()
