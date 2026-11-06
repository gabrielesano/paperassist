"""Top-toolbar annotation controls. The host calls install(window), then refresh()."""
from weakref import WeakSet

from PySide6.QtCore import QSignalBlocker, Qt, Slot
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QComboBox, QLabel, QSpinBox, QToolBar

COLORS = [('Yellow', '#F5D76E'), ('Green', '#8FD3AA'), ('Blue', '#79C5DF'),
          ('Pink', '#E9A9BC'), ('Dark green', '#243F3A'), ('Black', '#000000')]


class AnnotationBar(QToolBar):
    def __init__(self, window):
        super().__init__('Annotations', window)
        self.window, self.bound = window, WeakSet()
        self.setMovable(False)
        self.select = self.addAction('Select')
        self.select.setCheckable(True)
        self.select.setShortcut('V')
        self.select.triggered.connect(lambda: self.tool('select'))
        self.draw = self.addAction('Draw')
        self.draw.setCheckable(True)
        self.draw.setShortcut('D')
        self.draw.triggered.connect(lambda: self.tool('ink'))
        self.comment = self.addAction('Comment')
        self.comment.setCheckable(True)
        self.comment.setShortcut('C')
        self.comment.setToolTip('Comment on selected text or the current page (C)')
        self.comment.triggered.connect(lambda: window.comments.compose())
        self.label = QLabel()
        self.addWidget(self.label)
        self.colors = QComboBox(objectName='annotation-color')
        self.colors.setAccessibleName('Annotation color')
        for name, color in COLORS:
            pixmap = QPixmap(16, 16)
            pixmap.fill(QColor(color))
            self.colors.addItem(QIcon(pixmap), name, color)
        self.colors.activated.connect(self.color)
        self.addWidget(self.colors)
        self.thickness = QSpinBox(objectName='annotation-width')
        self.thickness.setRange(1, 20)
        self.thickness.setPrefix('Width ')
        self.thickness.setAccessibleName('Ink thickness')
        self.thickness.valueChanged.connect(lambda value: self.call('set_annotation_width', value))
        self.width_action = self.addWidget(self.thickness)
        self.remove = self.addAction('Delete', lambda: self.call('delete_annotation'))
        self.undo = self.addAction('Undo', lambda: self.call('undo'))
        self.redo = self.addAction('Redo', lambda: self.call('redo'))
        window.highlight_action.toggled.connect(self.highlight)
        window.tabs.currentChanged.connect(self.refresh)
        self.refresh()

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
        item = None
        if view and view.selected_annotation:
            page, index = view.selected_annotation
            item = next((a for a in view.doc.annotations(page) if a['index'] == index), None)
        active = bool(view and not view.locked)
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
        self.label.setText('Selected comment' if comment is not None else ('Selected ' + item['subtype']) if item else '')
        self.colors.setEnabled(notes if comment is not None else active and bool(item or view.drawing or view.highlighting))
        if comment is not None:
            from .comments import note_color
            color = QColor(note_color(comment)).getRgbF()[:3]
        else:
            color = item['color'] if item else (view.ink_color if view and view.drawing else view.highlight_color if view else (0, 0, 0))
        hex_color = QColor.fromRgbF(*color).name().lower()
        with QSignalBlocker(self.colors):
            self.colors.setCurrentIndex(next((i for i, (_, value) in enumerate(COLORS) if value.lower() == hex_color), -1))
        self.width_action.setVisible(bool(view and (item['subtype'] == 'ink' if item else view.drawing)))
        self.thickness.setEnabled(active)
        with QSignalBlocker(self.thickness):
            self.thickness.setValue(round(item['width'] if item else view.ink_width if view else 2))
        self.remove.setEnabled(notes if comment is not None else active and item is not None)
        self.undo.setEnabled(bool(view and ((view.notes_model and view.notes_model.deleted is not None) or active and view.doc._undo)))
        self.redo.setEnabled(active and bool(view.doc._redo))


def install(window):
    bar = AnnotationBar(window)
    window.addToolBarBreak(Qt.TopToolBarArea)
    window.addToolBar(Qt.TopToolBarArea, bar)
    return bar
