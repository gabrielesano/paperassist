"""Reading position of a library paper, shared with the web reader.

The web reader stores a 1-based page and a PDF.js scale: 'page-width', 'page-fit', 'auto',
'page-actual' or a number. Positions belong to the exact PDF (the library document).
"""
from PySide6.QtCore import QObject, QTimer, Signal

from .layout import MARGIN

CSS_PER_POINT = 96 / 72  # PDF.js scale 1.0 draws a 72-per-inch point as 96-per-inch CSS pixels; native zoom is pixels per point
MAX_AUTO_SCALE = 1.25    # "auto" is the page width, but never larger than this PDF.js scale
SAVE_DELAY_MS = 1000     # a scroll gesture is one write, not hundreds


def to_zoom(scale, width_zoom, page_zoom):
    """(native zoom, follows the window width) for a stored PDF.js scale."""
    if scale == "page-width":
        return width_zoom, True
    if scale == "page-fit":
        return page_zoom, False
    if scale == "auto":
        return min(width_zoom, MAX_AUTO_SCALE * CSS_PER_POINT), False
    if scale == "page-actual":
        return CSS_PER_POINT, False
    return float(scale) * CSS_PER_POINT, False


def to_scale(zoom, fit):
    """Numbers are text, as PDF.js stores them."""
    return "page-width" if fit else str(round(zoom / CSS_PER_POINT, 4))


def current_page(view):
    """Index of the page showing the most, which is what PDF.js calls the current page."""
    top = view.verticalScrollBar().value()
    bottom = top + view.viewport().height()

    def visible(page):
        _, y, _, height = view.pages.rect(page)
        return round(min(bottom, y + height) - max(top, y))
    return max(view.visible_pages(), key=visible, default=0)


class PositionKeeper(QObject):
    failed = Signal(str)

    def __init__(self, pane, workspace):
        super().__init__(pane)
        self.pane, self.view, self.workspace, self.document_id = pane, pane.view, workspace, pane.view.doc.source.document_id
        self.active = self.dirty = False  # moves before the restore are the window settling, not the reader
        self.known = self.restored = None  # the stored (page, scale); the restored (scale, zoom, fit)
        self.timer = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS, timeout=self.save)
        self.view.moved.connect(self._moved)
        pane.released.connect(self.close)
        pane.split_changed.connect(self.refit)
        QTimer.singleShot(0, self.restore)  # the view needs its final size to place itself

    def restore(self):
        view = self.view
        try:
            saved = self.workspace.get_view(self.document_id)
        except Exception as error:
            saved = None
            self.failed.emit(f"Could not read the saved position: {error}")
        if saved:
            page = max(0, min(saved["page"], len(view.doc)) - 1)
            widest = max(width for width, _ in view.doc.sizes)
            width, height = view.doc.sizes[page]
            area = view.viewport()
            zoom, fit = to_zoom(saved["scale"], (area.width() - 2 * MARGIN) / widest,
                                min((area.width() - 2 * MARGIN) / width, (area.height() - 2 * MARGIN) / height))
            view.fit_width() if fit or self._beside() else view.zoom_by(zoom / view.zoom)
            view.goto(page)
            self.known = (saved["page"], saved["scale"])
            self.restored = (saved["scale"], view.zoom, view.fit)
        self.active = True

    def _beside(self):
        """A quick view in half the width fits it until the user zooms, whatever zoom was stored."""
        return self.pane.quick and not self.pane.vertical

    def refit(self):
        """Side by side after a stacked layout: refit unless the zoom is one the user chose."""
        if self._beside() and self.restored and self.restored[1:] == (self.view.zoom, self.view.fit) and not self.view.fit:
            self.view.fit_width()

    def _moved(self):
        if self.active:
            self.dirty = True
            self.timer.start()

    def scale(self):
        """A mode restored from the web reader stays itself until the user changes the zoom."""
        if self.restored and self.restored[1:] == (self.view.zoom, self.view.fit):
            return self.restored[0]
        return to_scale(self.view.zoom, self.view.fit)

    def save(self):
        if not (self.active and self.dirty):
            return
        self.dirty = False
        position = (current_page(self.view) + 1, self.scale())
        if position == self.known:
            return
        try:
            self.workspace.save_view(self.document_id, {"page": position[0], "scale": position[1]})
        except Exception as error:  # the reading position is a convenience; say so, keep reading
            self.failed.emit(f"Could not save the reading position: {error}")
        else:
            self.known = position

    def close(self):
        self.timer.stop()
        self.save()
        self.active = False
