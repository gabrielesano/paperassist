"""Continuous vertical PDF view: lazy rendering, selection, links, highlights, search hits."""
import time
from urllib.parse import urlparse

from PySide6.QtCore import QEventLoop, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QScrollBar

from .engine import HIGHLIGHT, Rect
from .layout import MARGIN, PageCache, PageLayout
from .trim import trim_heap

CACHE_BYTES = 120 * 1024 * 1024  # about five fit-width pages at 1.5x HiDPI
ZOOM_RANGE = (0.2, 4.0)
LINK_SCHEMES = {"http", "https", "mailto"}  # a PDF must not be able to launch local files
BACKGROUND = QColor("#dad7d1")
SELECTION, HIT, CURRENT_HIT, MARKER = QColor(40, 110, 255, 80), QColor(255, 150, 0, 90), QColor(255, 90, 0, 160), QColor(255, 235, 0)


class PdfView(QAbstractScrollArea):
    annotation_selection_changed = Signal()
    edited = Signal()     # content or save state changed
    activated = Signal()  # the user clicked or focused this view
    moved = Signal()      # scrolled or zoomed, for reading-position persistence
    selection_changed = Signal()
    comment_selection_changed = Signal(object)

    def __init__(self, doc, client):
        super().__init__()
        self.doc, self.client = doc, client
        self.doc_id = client.open(doc.source.path)
        client.page_ready.connect(self._page_ready)
        self.zoom, self.fit = 1.0, True
        self.cache = PageCache(CACHE_BYTES)  # page -> (scale, render id, QPixmap)
        self.render_seconds = []  # worker-side time of every page this view received
        self.locked = False  # another view of this PDF has unsaved edits
        self.highlighting = False
        self.drawing = False
        self.highlight_color, self.ink_color = HIGHLIGHT, (0, 0, 0)
        self.ink_width = 2
        self.selected_annotation = None
        self.notes_model = None
        self.selected_comment = None
        self.comment_draft = None
        self.commenting = False
        self._comment_bands, self._comment_paths, self._comment_focus = {}, {}, []
        self._comment_pen = QPen(QColor("#d42020"), 1.5, Qt.DashLine)
        self._comment_focus_pen = QPen(QColor("#aa700f"), 1)
        self._stroke = []
        self.selection, self.selection_lines = None, []  # ((page, word), (page, word))
        self.hits, self.hit = [], -1
        self.history = []
        self.internal_link_handler = None  # return True when a citation consumes an internal link
        self._outlines = []
        self._outline_pen = QPen(QColor("#b24732"), 2, Qt.DashLine)
        self._outline_timer = QTimer(self, singleShot=True, timeout=self.clear_outlines)
        self.first_paint = None
        self._press, self._anchor_word, self._drag = None, None, QPointF()
        self._autoscroll = QTimer(self, interval=40, timeout=self._autoscroll_step)
        self.setFocusPolicy(Qt.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().setSingleStep(48)
        self.pages = PageLayout(doc.sizes, self.zoom, self.viewport().width())
        self._update_scrollbars()

    @property
    def highlighting(self):
        return self._highlighting

    @highlighting.setter
    def highlighting(self, value):
        self._highlighting = value
        if value:
            self.drawing = False

    # ---- geometry -------------------------------------------------------------------
    def _scale(self):
        return round(self.zoom * self.devicePixelRatioF(), 3)

    def _update_scrollbars(self):
        view = self.viewport().size()
        for bar, total, extent in ((self.verticalScrollBar(), self.pages.height, view.height()),
                                   (self.horizontalScrollBar(), self.pages.width, view.width())):
            bar.setRange(0, max(0, int(total) - extent))
            bar.setPageStep(extent)

    def _relayout(self):
        self.pages = PageLayout(self.doc.sizes, self.zoom, self.viewport().width())
        self._update_scrollbars()

    def _scroll_top(self):
        return self.verticalScrollBar().value()

    def visible_pages(self, margin=0):
        top = self._scroll_top()
        return self.pages.visible(top - margin, top + self.viewport().height() + margin)

    def _set_zoom(self, zoom, fit=False):
        page, fraction = self.pages.anchor(self._scroll_top())
        self.zoom, self.fit = max(ZOOM_RANGE[0], min(zoom, ZOOM_RANGE[1])), fit
        self._relayout()
        self.verticalScrollBar().setValue(int(self.pages.top_of(page, fraction)))
        self._after_move()

    def zoom_by(self, factor):
        self._set_zoom(self.zoom * factor)

    def fit_width(self):
        widest = max(w for w, _ in self.doc.sizes)
        self._set_zoom((self.viewport().width() - 2 * MARGIN) / widest, fit=True)

    def goto(self, page, y=0.0):
        """Scroll so that the point y (PDF points) of a page sits just below the top edge."""
        self.verticalScrollBar().setValue(int(self.pages.top_of(page, 0) + y * self.zoom - (40 if y else MARGIN)))

    def follow(self, link):
        if link.uri:
            if urlparse(link.uri).scheme in LINK_SCHEMES:
                QDesktopServices.openUrl(QUrl(link.uri))
        else:
            if self.internal_link_handler and self.internal_link_handler(link):
                return
            self.history.append(self._scroll_top())
            self.goto(link.page, link.y)

    def outline_rects(self, rectangles, milliseconds=1800, color=None):
        """Briefly mark (zero-based page, display-space Rect) pairs without editing PDF bytes."""
        self._outlines = list(rectangles)
        self._outline_pen.setColor(QColor(color or "#b24732"))
        self._outline_timer.start(milliseconds)
        self.viewport().update()

    def clear_outlines(self):
        self._outlines.clear()
        self.viewport().update()

    def go_back(self):
        if self.history:
            self.verticalScrollBar().setValue(self.history.pop())

    # ---- rendering ------------------------------------------------------------------
    def _after_move(self):
        self.viewport().update()
        self._want()
        self.moved.emit()

    def _fresh(self, page):
        entry = self.cache.get(page)
        return entry is not None and entry[0] == self._scale() and entry[1] == self.doc_id

    def _want(self):
        """Ask the render process for what is visible plus half a screen; hidden views ask for nothing."""
        jobs = []
        if self.isVisible():
            on_screen = self.visible_pages()
            near = self.visible_pages(self.viewport().height() // 2)
            jobs = [(page, self._scale()) for page in sorted(near, key=lambda p: p not in on_screen) if not self._fresh(page)]
        self.client.want(self.doc_id, jobs)

    def _page_ready(self, doc_id, page, scale, width, height, rgb, seconds):
        if doc_id != self.doc_id or scale != self._scale() or not self.isVisible():
            return  # late pixels for a view that was hidden meanwhile must not refill its cache
        pixmap = QPixmap.fromImage(QImage(rgb, width, height, width * 3, QImage.Format_RGB888))
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self.cache.put(page, (scale, doc_id, pixmap), width * height * 4)
        if not self.doc.modified:
            self.doc.pending.pop(page, None)  # the saved highlight is part of the pixels now
        self.render_seconds.append(seconds)
        self.viewport().update()

    def has_page(self, page):
        return self._fresh(page)

    def refresh_render(self, pages=None):
        """After the file changed: render from the new bytes, keep showing the old pixels meanwhile."""
        old, self.doc_id = self.doc_id, self.client.open(self.doc.pdf.data)
        if pages is not None:
            for page, ((scale, _, pixmap), size) in list(self.cache.entries.items()):
                if page not in pages:
                    self.cache.entries[page] = ((scale, self.doc_id, pixmap), size)
        self.client.close(old)
        self._want()

    def reload(self):
        self.doc.reload()
        self.refresh_render()
        self.viewport().update()

    def release(self):
        self.cache.clear()
        self.client.close(self.doc_id)
        trim_heap()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_width() if self.fit else self._want()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.cache.clear()  # a hidden tab must not keep ~100 MB of pages
        self.client.want(self.doc_id, [])
        trim_heap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit and self.isVisible():
            self.fit_width()
        else:
            self._relayout()
            self._want()

    def scrollContentsBy(self, dx, dy):
        self._after_move()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.1 if event.angleDelta().y() > 0 else 1 / 1.1)
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        bar = self.verticalScrollBar()
        steps = {Qt.Key_Down: QScrollBar.SliderSingleStepAdd, Qt.Key_Up: QScrollBar.SliderSingleStepSub,
                 Qt.Key_PageDown: QScrollBar.SliderPageStepAdd, Qt.Key_PageUp: QScrollBar.SliderPageStepSub,
                 Qt.Key_Home: QScrollBar.SliderToMinimum, Qt.Key_End: QScrollBar.SliderToMaximum}
        if event.matches(QKeySequence.Copy):
            self.copy()
        elif event.matches(QKeySequence.Undo):
            self.undo()
        elif event.matches(QKeySequence.Redo):
            self.redo()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_annotation()
        elif event.key() == Qt.Key_Escape and self.selected_annotation:
            self.select_annotation(None)
        elif event.key() == Qt.Key_Escape and self.selected_comment is not None:
            self.select_comment(None)
        elif event.key() in steps:
            bar.triggerAction(steps[event.key()])
        else:
            super().keyPressEvent(event)

    # ---- painting -------------------------------------------------------------------
    def _offset(self):
        return QPointF(-self.horizontalScrollBar().value(), -self._scroll_top())

    def _band(self, page, rect):
        x, y = self.pages.to_content(page, rect.x0, rect.y0)
        return QRectF(x + self._offset().x(), y + self._offset().y(), rect.width * self.zoom, rect.height * self.zoom)

    def _target(self, page, pixmap):
        """Where to draw a page. Snapping to device pixels and using the pixmap's own size keeps
        text crisp at fractional pixel ratios, where a stretched pixmap would be resampled."""
        x, y, w, h = self.pages.rect(page)
        dpr = self.devicePixelRatioF()
        x, y = x + self._offset().x(), y + self._offset().y()
        if pixmap is not None and abs(pixmap.width() / dpr - w) < 2:
            w, h = pixmap.width() / dpr, pixmap.height() / dpr
        return QRectF(round(x * dpr) / dpr, round(y * dpr) / dpr, w, h)

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), BACKGROUND)
        for page in self.visible_pages():
            entry = self.cache[page] if page in self.cache else None
            pixmap = entry[2] if entry else None
            box = self._target(page, pixmap)
            if pixmap:
                painter.setRenderHint(QPainter.SmoothPixmapTransform, entry[0] != self._scale())  # stale: smooth stretch
                painter.drawPixmap(box, pixmap, QRectF(pixmap.rect()))
                self.first_paint = self.first_paint or time.perf_counter()
            else:
                painter.fillRect(box, Qt.white)
        if self.selected_annotation:
            page, index = self.selected_annotation
            item = next((a for a in self.doc.annotations(page) if a['index'] == index), None)
            if item:
                painter.setPen(QColor('#286eb8'))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self._band(page, item['rect']).adjusted(-2, -2, 2, 2))
        if self._stroke:
            from PySide6.QtGui import QPen
            painter.setPen(QPen(QColor.fromRgbF(*self.ink_color), self.ink_width * self.zoom,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            page = self._stroke_page
            for a, b in zip(self._stroke, self._stroke[1:]):
                painter.drawLine(self._band(page, Rect(*a, *a)).topLeft(), self._band(page, Rect(*b, *b)).topLeft())
        for page, rect, _ in self.selection_lines:
            painter.fillRect(self._band(page, rect), SELECTION)
        for i, (page, rect) in enumerate(self.hits):
            painter.fillRect(self._band(page, rect), CURRENT_HIT if i == self.hit else HIT)
        painter.setBrush(Qt.NoBrush)
        self._comment_pen.setWidthF(1.5 / self.zoom)
        painter.setPen(self._comment_pen)
        for page in self.visible_pages():
            if page in self._comment_paths:
                x, y, _, _ = self.pages.rect(page)
                offset = self._offset()
                painter.save()
                painter.translate(x + offset.x(), y + offset.y())
                painter.scale(self.zoom, self.zoom)
                painter.drawPath(self._comment_paths[page])
                painter.restore()
        painter.setPen(self._comment_focus_pen)
        for page, rect in self._comment_focus:
            painter.drawRect(self._band(page, rect))
        if self._outlines:
            painter.setPen(self._outline_pen)
            painter.setBrush(Qt.NoBrush)
            for page, rect in self._outlines:
                painter.drawRect(self._band(page, rect))

    # ---- mouse: selection, links, highlight -------------------------------------------
    def _locate(self, pos):
        """(page, x, y in points) for a viewport position, snapped to the nearest page."""
        return self.pages.to_page(pos.x() + self.horizontalScrollBar().value(), pos.y() + self._scroll_top())

    def mousePressEvent(self, event):
        self.setFocus()
        self.activated.emit()
        if event.button() != Qt.LeftButton:
            return
        page, x, y = self._locate(event.position())
        if self.drawing and not self.locked:
            self.select_annotation(None)
            self._stroke_page, self._stroke = page, [(x, y)]
            return
        picked = self.doc.annotation_at(page, x, y, tolerance=4 / self.zoom) if not self.highlighting else None
        comment = self.comment_at(page, x, y) if not self.highlighting and not self.drawing else None
        self._press = {"link": self.doc.link_at(page, x, y), "moved": False, "annotation": picked, "page": page, "comment": comment}
        self.selection, self.selection_lines = None, []
        word = self.doc.word_at(page, x, y)
        self._anchor_word = None if word is None else (page, word)
        self.viewport().update()

    def _extend_selection(self, pos):
        page, x, y = self._locate(pos)
        word = self.doc.word_at(page, x, y)
        if self._anchor_word and word is not None:
            self.selection = (self._anchor_word, (page, word))
            self.selection_lines = self.doc.selection_lines(*self.selection)
            self.viewport().update()

    def _autoscroll_step(self):
        overshoot = min(0, self._drag.y()) or max(0, self._drag.y() - self.viewport().height())
        if overshoot:  # dragging past an edge keeps scrolling and growing the selection
            self.verticalScrollBar().setValue(self._scroll_top() + int(overshoot / 2) + (1 if overshoot > 0 else -1))
            self._extend_selection(self._drag)

    def mouseMoveEvent(self, event):
        page, x, y = self._locate(event.position())
        if self._stroke:
            if page == self._stroke_page:
                self._stroke.append((x, y))
                self.viewport().update()
            return
        if self._press is None:
            self.viewport().setCursor(Qt.PointingHandCursor if self.doc.link_at(page, x, y) else Qt.IBeamCursor)
            return
        self.select_annotation(None)
        self._press["moved"] = True
        self._drag = event.position()
        if not self._autoscroll.isActive():
            self._autoscroll.start()
        self._extend_selection(event.position())

    def mouseReleaseEvent(self, event):
        if self._stroke:
            stroke, self._stroke = self._stroke, []
            if not self.locked and len(stroke) > 1:
                self.doc.add_ink(self._stroke_page, [stroke], self.ink_color, self.ink_width)
                self._annotation_changed([self._stroke_page])
            return
        press, self._press = self._press, None
        self._autoscroll.stop()
        if press is None:
            return
        if not press['moved'] and press['comment'] is not None:
            self.select_comment(press['comment'])
        elif not press['moved'] and press['annotation']:
            self.select_annotation((press['page'], press['annotation']['index']))
        elif not press["moved"] and press["link"]:
            self.follow(press["link"])
        elif press["moved"] and self.highlighting and not self.locked:
            self.highlight_selection()
        elif not press['moved']:
            self.select_annotation(None)
            self.select_comment(None)
        if press['moved']:
            self.selection_changed.emit()

    def selected_text(self):
        return "\n".join(text for _, _, text in self.selection_lines)

    def copy(self):
        if self.selection_lines:
            QGuiApplication.clipboard().setText(self.selected_text())

    def highlight_selection(self):
        pages = {}
        for page, rect, _ in self.selection_lines:
            pages.setdefault(page, []).append(rect)
        if self.locked or not pages:
            return
        self.doc.add_highlights(pages, self.highlight_color)
        self.selection, self.selection_lines = None, []
        self._annotation_changed(list(pages))

    def set_tool(self, tool):
        self.highlighting, self.drawing = tool == 'highlight', tool == 'ink'
        self.commenting = tool == 'comment'
        self.select_annotation(None)
        self.select_comment(None)

    def select_annotation(self, selection):
        self.selected_annotation = selection
        if selection and self.selected_comment is not None:
            self.select_comment(None)
        self.annotation_selection_changed.emit()
        self.viewport().update()

    def _annotation_changed(self, pages):
        self.doc.pending.clear()
        self.refresh_render(pages)
        self.viewport().update()
        self.annotation_selection_changed.emit()
        self.edited.emit()

    def set_annotation_color(self, color):
        if self.selected_comment is not None and self.notes_model:
            note = {**self.selected_comment, "color": QColor.fromRgbF(*color).name()}
            if self.notes_model.update(self.selected_comment, note["color"]):
                self.select_comment(note)
            return
        if self.locked:
            return
        if self.selected_annotation:
            page, index = self.selected_annotation
            if self.doc.update_annotation(page, index, color=color):
                self._annotation_changed([page])
        elif self.drawing:
            self.ink_color = color
        else:
            self.highlight_color = color

    def set_annotation_width(self, width):
        if self.locked:
            return
        if self.selected_annotation:
            page, index = self.selected_annotation
            if self.doc.update_annotation(page, index, width=width):
                self._annotation_changed([page])
        else:
            self.ink_width = width

    def delete_annotation(self):
        if self.selected_comment is not None and self.notes_model:
            if self.notes_model.update(self.selected_comment):
                self.select_comment(None)
            return
        if self.selected_annotation and not self.locked:
            page, index = self.selected_annotation
            self.doc.delete_annotation(page, index)
            self.select_annotation(None)
            self._annotation_changed([page])

    def undo(self):
        if self.notes_model and self.notes_model.deleted is not None:
            self.notes_model.undo_delete()
            return
        if not self.locked and (pages := self.doc.undo()):
            self.select_annotation(None)
            self._annotation_changed(pages)

    def redo(self):
        if not self.locked and (pages := self.doc.redo()):
            self.select_annotation(None)
            self._annotation_changed(pages)

    # ---- sidecar comments ------------------------------------------------------------
    def set_comment_model(self, model):
        if self.notes_model:
            self.notes_model.changed.disconnect(self._comments_changed)
        self.notes_model = model
        model.changed.connect(self._comments_changed)
        self._comments_changed()

    def _comments_changed(self):
        from .comments import note_rects
        self._comment_bands = {}
        for note in self.notes_model.notes:
            if isinstance(note, dict):
                for page, rect in note_rects(note, self.doc.pdf):
                    self._comment_bands.setdefault(page, []).append((note, rect))
        self._comment_paths = {}
        for page, bands in self._comment_bands.items():
            path = QPainterPath()
            for rect in dict.fromkeys(rect for _, rect in bands):
                path.addRect(*rect[:2], rect.width, rect.height)
            self._comment_paths[page] = path
        if self.selected_comment is not None and self.selected_comment not in self.notes_model.notes:
            self.select_comment(None)
        self.viewport().update()
        self.annotation_selection_changed.emit()

    def comment_at(self, page, x, y):
        best, area = None, float("inf")
        for note, rect in self._comment_bands.get(page, ()):
            size = rect.width * rect.height
            if size <= area and rect.contains(x, y):
                best, area = note, size
        return best

    def focus_comment(self, note, scroll=True):
        from .comments import note_color, note_rects
        self._comment_focus = note_rects(note, self.doc.pdf) if note else []
        self._comment_focus_pen.setColor(QColor(note_color(note)) if note else QColor("#aa700f"))
        if scroll and note:
            if self._comment_focus:
                page, rect = self._comment_focus[0]
                self.goto(page, max(0, rect.y0 - 40 / self.zoom))
                x, _ = self.pages.to_content(page, rect.x0, rect.y0)
                self.horizontalScrollBar().setValue(max(0, int(x - 40)))
            elif type(note.get("page")) is int and 1 <= note["page"] <= len(self.doc):
                self.goto(note["page"] - 1)
        self.viewport().update()

    def select_comment(self, note, scroll=False):
        self.selected_comment = note
        if note is not None:
            self.selected_annotation = None
        self.focus_comment(note, scroll)
        self.annotation_selection_changed.emit()
        self.comment_selection_changed.emit(note)

    def _show_saving(self):
        self.edited.emit()  # let "Saving…" paint before the possibly slow write blocks the loop
        QGuiApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def save(self):
        """Explicit save of this view's document. False keeps the unsaved state for a retry."""
        saved = self.doc.save(on_saving=self._show_saving)
        if saved:
            self.refresh_render()
        self.edited.emit()
        return saved

    # ---- search -----------------------------------------------------------------------
    def find(self, text):
        self.hits, self.hit = (self.doc.search(text) if text else []), -1
        self.step_hit(1)
        return len(self.hits)

    def step_hit(self, direction):
        if self.hits:
            self.hit = (self.hit + direction) % len(self.hits)
            page, rect = self.hits[self.hit]
            band = self._band(page, rect)
            if band.top() < 0 or band.bottom() > self.viewport().height():
                self.goto(page, max(0, rect.y0 - 120 / self.zoom))
        self.viewport().update()
