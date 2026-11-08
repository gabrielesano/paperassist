"""Annotation tool switchers in the Main bar and a contextual options strip below it."""
from weakref import WeakSet

from PySide6.QtCore import QRectF, QSignalBlocker, QSize, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSpinBox, QToolBar, QToolButton, QWidget

from .theme import INK, TEAL, icon

COLORS = [('Yellow', '#F5D76E'), ('Green', '#8FD3AA'), ('Blue', '#79C5DF'),
          ('Pink', '#E9A9BC'), ('Dark green', '#243F3A'), ('Black', '#000000')]


class ColorChip(QToolButton):
    """Circular color swatch with a crisp outer ring when selected."""

    def __init__(self, name, hex_color, parent=None):
        super().__init__(parent)
        self.hex_color = hex_color
        self.setCheckable(True)
        self.setFocusPolicy(Qt.TabFocus)
        self.setToolTip(f"{name} color")
        self.setAccessibleName(f"{name} annotation color")

    def sizeHint(self):
        side = max(22, round(self.fontMetrics().height() * 1.35))
        return QSize(side, side)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height())
        box = QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s)
        if self.isChecked():
            painter.setPen(QPen(QColor(TEAL), 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(box.adjusted(1.5, 1.5, -1.5, -1.5))
        if self.hasFocus():
            painter.setPen(QPen(QColor(TEAL), 1.0, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
        swatch = box.adjusted(4.0, 4.0, -4.0, -4.0)
        fill = QColor(self.hex_color)
        if not self.isEnabled():
            fill.setAlpha(110)
        painter.setPen(QPen(QColor(82, 100, 91, 110), 1.0))
        painter.setBrush(fill)
        painter.drawEllipse(swatch)


class ColorSwatches(QWidget):
    """Row of circular swatches replacing the old combo box while keeping index-based signals."""
    activated = Signal(int)

    def __init__(self, colors, parent=None):
        super().__init__(parent, objectName='annotation-color')
        self._index = -1
        self.buttons = []
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(5)
        for index, (name, hex_color) in enumerate(colors):
            chip = ColorChip(name, hex_color, self)
            chip.clicked.connect(lambda _=False, idx=index: self.pick(idx))
            row.addWidget(chip)
            self.buttons.append((hex_color, chip))

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        self._index = index
        for idx, (_, chip) in enumerate(self.buttons):
            chip.setChecked(idx == index)
            chip.update()

    def itemData(self, index):
        return self.buttons[index][0] if 0 <= index < len(self.buttons) else COLORS[0][1]

    def pick(self, index):
        self.setCurrentIndex(index)
        self.activated.emit(index)

    def add_overflow_actions(self, menu):
        colors = menu.addMenu('Annotation color')
        colors.setEnabled(self.isEnabled())
        for index, (name, _) in enumerate(COLORS):
            action = colors.addAction(name)
            action.setCheckable(True)
            action.setChecked(index == self._index)
            action.triggered.connect(lambda _=False, idx=index: self.pick(idx))
        return colors


class AnnotationBar(QToolBar):
    def __init__(self, window):
        super().__init__('Annotations', window)
        self.setObjectName('annotation-toolbar')
        self.window, self.bound = window, WeakSet()
        self.setMovable(False)
        self.setIconSize(QSize(16, 16))

        # Mode switchers live together in the Main toolbar right next to Highlight.
        self.select = QAction('Select', window, checkable=True)
        self.select.setShortcut('V')
        self.select.setToolTip('Select text or annotations (V)')
        self.select.triggered.connect(lambda: self.tool('select'))

        self.draw = QAction('Draw', window, checkable=True)
        self.draw.setShortcut('D')
        self.draw.setToolTip('Draw freehand ink (D)')
        self.draw.triggered.connect(lambda: self.tool('ink'))

        self.comment = QAction('Comment', window, checkable=True)
        self.comment.setShortcut('C')
        self.comment.setToolTip('Comment on selected text or the current page (C)')
        self.comment.triggered.connect(lambda: window.comments.compose())

        main_bar = getattr(window, 'main_bar', None)
        anchor = getattr(window, 'highlight_action', None)
        if main_bar is not None and anchor is not None:
            main_bar.insertAction(anchor, self.select)
            main_bar.insertAction(anchor, self.draw)
            after = getattr(window, 'after_tools_action', None)
            if after is not None:
                main_bar.insertAction(after, self.comment)
            else:
                main_bar.addAction(self.comment)
        else:
            for action in (self.select, self.draw, self.comment):
                self.addAction(action)

        self.label = QLabel(objectName='column-title')
        self.label_action = self.addWidget(self.label)
        self.colors = ColorSwatches(COLORS, self)
        self.colors.activated.connect(self.color)
        self.addWidget(self.colors)

        self.thickness = QSpinBox(objectName='annotation-width')
        self.thickness.setRange(1, 20)
        self.thickness.setPrefix('Width ')
        self.thickness.setAccessibleName('Ink thickness')
        self.thickness.valueChanged.connect(lambda value: self.call('set_annotation_width', value))
        self.width_action = self.addWidget(self.thickness)

        self.addSeparator()
        self.remove = self._icon_action('Delete', 'trash', 'Delete selected annotation (Del)', lambda: self.call('delete_annotation'))
        self.undo = self._icon_action('Undo', 'undo', 'Undo (Ctrl+Z)', lambda: self.call('undo'))
        self.redo = self._icon_action('Redo', 'redo', 'Redo (Ctrl+Shift+Z)', lambda: self.call('redo'))
        self.visibility = QAction('Show highlights', window)
        if main_bar is not None:
            main_bar.addAction(self.visibility)
        else:
            self.addAction(self.visibility)
        self.visibility.setCheckable(True)
        self.visibility.setChecked(True)
        self.visibility.toggled.connect(lambda visible: self.call('set_highlights_visible', visible))

        window.highlight_action.toggled.connect(self.highlight)
        window.tabs.currentChanged.connect(self.refresh)
        self.refresh()

    def _icon_action(self, title, glyph, tooltip, slot):
        action = self.addAction(icon(glyph, INK, 16), title, slot)
        action.setToolTip(tooltip)
        if btn := self.widgetForAction(action):
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
            btn.setAccessibleName(title)
        return action

    def view(self):
        pane = self.window.pane()
        return pane.view if pane else None

    def call(self, name, *args):
        if view := self.view():
            getattr(view, name)(*args)
        self.refresh()

    def tool(self, name):
        self.window.highlight_action.setChecked(False)
        self.call('set_tool', name)

    def highlight(self, on):
        if on:
            self.call('set_tool', 'highlight')
        self.refresh()

    def color(self, index):
        color = QColor(self.colors.itemData(index))
        self.call('set_annotation_color', color.getRgbF()[:3])
        if view := self.view():
            view.setFocus()  # Delete and Ctrl+Z keep working after picking a color

    @Slot()
    def refresh(self, *_):
        for pane in self.window.all_panes():
            view = pane.view
            if view not in self.bound:
                self.bound.add(view)
                view.edited.connect(self.refresh)
                view.activated.connect(self.refresh)
                view.annotation_selection_changed.connect(self.refresh)
        view = self.view()
        self.visibility.setEnabled(view is not None)
        with QSignalBlocker(self.visibility):
            self.visibility.setChecked(view.highlights_visible if view else True)
        item = None
        if view and view.selected_annotation:
            page, index = view.selected_annotation
            item = next((a for a in view.doc.annotations(page) if a['index'] == index), None)
        active = bool(view and not view.read_only)
        comment = view.selected_comment if view else None
        notes = bool(view and view.notes_model)
        self.select.setEnabled(active)
        self.draw.setEnabled(active)
        self.comment.setEnabled(notes)
        for action, checked in ((self.select, bool(view and not view.highlighting and not view.drawing and not view.commenting)),
                                (self.draw, bool(view and view.drawing)),
                                (self.comment, bool(view and view.commenting)),
                                (self.window.highlight_action, bool(view and view.highlighting))):
            with QSignalBlocker(action):
                action.setChecked(checked)
        kind = item and ('highlight' if item['freehand'] else item['subtype'])
        self.label.setText('Selected comment' if comment is not None else f'Selected {kind}' if item else '')
        self.label_action.setVisible(bool(self.label.text()))
        self.colors.setEnabled(notes if comment is not None else active and bool(item or view.drawing or view.highlighting))
        if comment is not None:
            from .comments import note_color
            color = QColor(note_color(comment)).getRgbF()[:3]
        else:
            color = item['color'] if item else (view.ink_color if view and view.drawing else view.highlight_color if view else (0, 0, 0))
        hex_color = QColor.fromRgbF(*color).name().lower()
        with QSignalBlocker(self.colors):
            self.colors.setCurrentIndex(next((i for i, (_, value) in enumerate(COLORS) if value.lower() == hex_color), -1))
        self.width_action.setVisible(bool(view and (item['subtype'] == 'ink' if item else view.drawing or view.highlighting)))
        self.thickness.setEnabled(active)
        with QSignalBlocker(self.thickness):
            self.thickness.setValue(round(item['width'] if item else (view.highlight_width if view.highlighting else view.ink_width) if view else 2))
        self.remove.setEnabled(notes if comment is not None else active and item is not None)
        has_undo = bool(view and ((view.notes_model and view.notes_model.deleted is not None) or active and view.doc._undo))
        has_redo = active and bool(view.doc._redo)
        self.undo.setEnabled(has_undo)
        self.redo.setEnabled(has_redo)

        contextual = bool(view and (
            view.highlighting or view.drawing or view.commenting
            or item is not None or comment is not None
            or has_undo or has_redo or not view.highlights_visible
        ))
        self.setVisible(contextual)


def install(window):
    if existing := getattr(window, 'annotation_bar', None):
        return existing
    bar = AnnotationBar(window)
    window.addToolBarBreak(Qt.TopToolBarArea)
    window.addToolBar(Qt.TopToolBarArea, bar)
    window.annotation_bar = bar
    return bar
