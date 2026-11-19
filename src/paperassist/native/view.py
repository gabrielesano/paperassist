"""Continuous vertical PDF view: lazy rendering, selection, links, highlights, search hits."""
from collections import deque
import gc
import math
import time
from urllib.parse import urlparse

from PySide6.QtCore import QEvent, QEventLoop, QKeyCombination, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QContextMenuEvent, QCursor, QDesktopServices, QGuiApplication, QImage,
                         QKeySequence, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QAbstractScrollArea, QMenu, QScrollBar

from .document import SaveState
from .engine import HIGHLIGHT, Rect
from .layout import MARGIN, PageCache, PageLayout
from .platform import trim_heap
from .positions import current_page

CACHE_BYTES = 120 * 1024 * 1024  # about five fit-width pages at 1.5x HiDPI
ZOOM_RANGE = (0.2, 4.0)
LINK_SCHEMES = {"http", "https", "mailto"}  # a PDF must not be able to launch local files
BACKGROUND = QColor("#dce2dc")
PAGE_SHADOW = QColor(39, 59, 57, 28)
PAGE_BORDER = QColor("#c4cec4")
SELECTION, HIT, CURRENT_HIT = QColor(40, 110, 255, 80), QColor(255, 150, 0, 90), QColor(255, 90, 0, 160)


class PdfView(QAbstractScrollArea):
    annotation_selection_changed = Signal()
    edited = Signal()     # content or save state changed
    activated = Signal()  # the user clicked or focused this view
    moved = Signal()      # scrolled or zoomed, for reading-position persistence
    selection_changed = Signal()
    comment_selection_changed = Signal(object)
    comment_requested = Signal(object)  # 1-based page number at cursor when opened without a text selection

    def __init__(self, doc, client):
        super().__init__()
        self.doc, self.client = doc, client
        self.highlights_visible = True
        self._rendered_data_id = id(doc.pdf.data)
        self.doc_id = client.open(self._render_source())
        client.page_ready.connect(self._page_ready)
        self.zoom, self.fit = 1.0, True
        self.cache = PageCache(CACHE_BYTES)  # page -> (scale, render id, QPixmap)
        self.render_seconds = deque(maxlen=256)  # worker-side time of recent pages this view received
        self.locked = False  # another view of this PDF has unsaved edits
        self.tool = 'select'  # 'select', 'pan', 'highlight', 'ink', or 'comment'
        self.highlight_color, self.ink_color = HIGHLIGHT, (0, 0, 0)
        self.ink_width, self.highlight_width = 2, 12  # a highlight's width only matters for freehand ones
        self.selected_annotation = None
        self.notes_model = None
        self.selected_comment = None
        self.comment_draft = None
        self._comment_bands, self._comment_paths, self._comment_focus = {}, {}, []
        self._comment_pen = QPen(QColor("#d42020"), 1.5, Qt.DashLine)
        self._comment_focus_pen = QPen(QColor("#aa700f"), 1)
        self._stroke, self._stroke_freehand = [], False
        self.selection, self.selection_lines = None, []  # ((page, word), (page, word))
        self.hits, self.hit = [], -1
        self.history = []
        self.internal_link_handler = None  # return True when a citation consumes an internal link
        self._outlines = []
        self._outline_pen = QPen(QColor("#b24732"), 2, Qt.DashLine)
        self._outline_timer = QTimer(self, singleShot=True, timeout=self.clear_outlines)
        self.first_paint = None
        self._press, self._anchor_word, self._drag = None, None, QPointF()
        self._pan = None
        self._space_pan, self._space_panned = False, False
        self._transform = None
        self._autoscroll = QTimer(self, interval=40, timeout=self._autoscroll_step)
        self._context_menu = QMenu(self)
        self._context_menu.setToolTipsVisible(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().setSingleStep(48)
        self.pages = PageLayout(doc.sizes, self.zoom, self.viewport().width())
        self._update_scrollbars()

    @property
    def panning(self):
        return self.tool == 'pan'

    @panning.setter
    def panning(self, value):
        if value:
            self.tool = 'pan'
        elif self.tool == 'pan':
            self.tool = 'select'
        self._update_cursor()

    @property
    def highlighting(self):
        return self.tool == 'highlight'

    @highlighting.setter
    def highlighting(self, value):
        if value:
            self.tool = 'highlight'
        elif self.tool == 'highlight':
            self.tool = 'select'
        self._update_cursor()

    @property
    def drawing(self):
        return self.tool == 'ink'

    @drawing.setter
    def drawing(self, value):
        if value:
            self.tool = 'ink'
        elif self.tool == 'ink':
            self.tool = 'select'
        self._update_cursor()

    @property
    def commenting(self):
        return self.tool == 'comment'

    @commenting.setter
    def commenting(self, value):
        if value:
            self.tool = 'comment'
        elif self.tool == 'comment':
            self.tool = 'select'
        self._update_cursor()

    def _render_source(self):
        # Library documents pass their hash-verified byte snapshot so the worker cannot read a tampered file.
        if self.doc.source.document_id is None and not self.doc.modified and self.highlights_visible:
            path = getattr(self.doc.source, "path", None)
            if path is not None and path.is_file():
                return path
        return self.doc.pdf.render_data(self.highlights_visible)

    @property
    def read_only(self):
        """No edit may start while another view holds unsaved edits or this one is being written."""
        return self.locked or self.doc.state is SaveState.SAVING

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
        widest = max((w for w, _ in self.doc.sizes), default=0)
        if widest <= 0:
            return
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

    def _render_plan(self):
        """Keep all visible pages within the cache budget before considering prefetch."""
        visible = self.visible_pages()
        if not visible:
            return {}
        area = sum(self.doc.sizes[p][0] * self.doc.sizes[p][1] for p in visible)
        # Leave room for PDFium's pixel rounding. Multiple visible pages share one scale.
        scale = min(self._scale(), math.sqrt(self.cache.budget / (4 * area)) * 0.99)
        scale = math.floor(scale * 1000) / 1000
        plan = {page: scale for page in visible}
        used = sum(math.ceil(self.doc.sizes[p][0] * scale) * math.ceil(self.doc.sizes[p][1] * scale) * 4
                   for p in visible)
        for page in self.visible_pages(self.viewport().height() // 2):
            if page in plan:
                continue
            width, height = self.doc.sizes[page]
            size = math.ceil(width * scale) * math.ceil(height * scale) * 4
            if used + size <= self.cache.budget:
                plan[page] = scale
                used += size
        return plan

    def _fresh(self, page, scale=None):
        entry = self.cache.get(page)
        scale = self._render_plan().get(page) if scale is None else scale
        return entry is not None and entry[0] == scale and entry[1] == self.doc_id

    def _want(self):
        """Ask the render process for what is visible plus half a screen; hidden views ask for nothing."""
        jobs = []
        if self.isVisible():
            jobs = [(page, scale) for page, scale in self._render_plan().items() if not self._fresh(page, scale)]
        self.client.want(self.doc_id, jobs)

    def _page_ready(self, doc_id, page, scale, width, height, rgb, seconds):
        if doc_id != self.doc_id or not self.isVisible() or self._render_plan().get(page) != scale:
            return  # late pixels for a view that was hidden meanwhile must not refill its cache
        pixmap = QPixmap.fromImage(QImage(rgb, width, height, width * 3, QImage.Format_RGB888))
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self.cache.put(page, (scale, doc_id, pixmap), width * height * 4, protected=self.visible_pages())
        self.render_seconds.append(seconds)
        self.viewport().update()

    def has_page(self, page):
        return self._fresh(page)

    def refresh_render(self, pages=None, *, preserve_citations=True):
        """After the file changed: render from the new bytes, keep showing the old pixels meanwhile."""
        old = self.doc_id
        self._rendered_data_id = id(self.doc.pdf.data)
        self.doc_id = self.client.open(self._render_source(), inherit_from=old if preserve_citations else None)
        if pages is not None:
            for page, ((scale, _, pixmap), size) in list(self.cache.entries.items()):
                if page not in pages:
                    self.cache.entries[page] = ((scale, self.doc_id, pixmap), size)
        self.client.close(old)
        self._want()

    def set_highlights_visible(self, visible):
        if visible != self.highlights_visible:
            self.highlights_visible = visible
            if not visible and self.selected_annotation:  # a hidden highlight must not stay selected
                page, index = self.selected_annotation
                item = next((a for a in self.doc.annotations(page) if a['index'] == index), None)
                if item and (item['subtype'] == 'highlight' or item['freehand']):
                    self.select_annotation(None)
            self.refresh_render()  # the old pixels stay on screen until the new ones arrive
            self.viewport().update()

    def reload(self):
        self.doc.reload()
        self.refresh_render(preserve_citations=False)
        self.viewport().update()

    def release(self):
        self.cache.clear()
        self.client.close(self.doc_id)
        gc.collect()
        trim_heap()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_width() if self.fit else self._want()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._space_pan, self._space_panned, self._pan = False, False, None
        self._update_cursor()
        self.cache.clear()  # a hidden tab must not keep ~100 MB of pages
        self.doc.hibernate()
        self.client.want(self.doc_id, [])
        gc.collect()
        trim_heap()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self._space_pan or self._pan:
            self._space_pan, self._space_panned, self._pan = False, False, None
            self._update_cursor()

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
        elif event.matches(QKeySequence.SelectAll):
            self.select_all()
        elif event.matches(QKeySequence.Undo):
            self.undo()
        elif event.matches(QKeySequence.Redo) or event.keyCombination() == QKeyCombination(Qt.ControlModifier, Qt.Key_Y):
            self.redo()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_annotation()
        elif event.key() == Qt.Key_Escape and self.selected_annotation:
            self.select_annotation(None)
        elif event.key() == Qt.Key_Escape and self.selected_comment is not None:
            self.select_comment(None)
        elif event.key() in steps:
            bar.triggerAction(steps[event.key()])
        elif event.key() == Qt.Key_Space:
            if event.modifiers() & Qt.ShiftModifier:
                bar.triggerAction(QScrollBar.SliderPageStepSub)
            elif event.modifiers() == Qt.NoModifier:
                if not event.isAutoRepeat() and not self._space_pan:
                    self._space_pan, self._space_panned = True, False
                    self._update_cursor()
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space and self._space_pan:
            if event.isAutoRepeat():
                return
            panned = self._space_panned
            self._space_pan, self._space_panned = False, False
            self._update_cursor()
            if not panned:
                self.verticalScrollBar().triggerAction(QScrollBar.SliderPageStepAdd)
            return
        super().keyReleaseEvent(event)

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
        visible = self.visible_pages()
        visible_set = set(visible)
        for page in visible:
            entry = self.cache[page] if page in self.cache else None
            pixmap = entry[2] if entry else None
            box = self._target(page, pixmap)
            painter.fillRect(box.adjusted(1, 2, 2, 3), PAGE_SHADOW)
            if pixmap:
                painter.setRenderHint(QPainter.SmoothPixmapTransform, entry[0] != self._scale())  # stale: smooth stretch
                painter.drawPixmap(box, pixmap, QRectF(pixmap.rect()))
                self.first_paint = self.first_paint or time.perf_counter()
            else:
                painter.fillRect(box, Qt.white)
            painter.setPen(PAGE_BORDER)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(box.adjusted(-0.5, -0.5, 0.5, 0.5))
        if self.selected_annotation and self.selected_annotation[0] in visible_set:
            page, index = self.selected_annotation
            item = next((a for a in self.doc.annotations(page) if a['index'] == index), None)
            if item:
                painter.setPen(QColor('#286eb8'))
                painter.setBrush(Qt.NoBrush)
                rect = self._transform['rect'] if self._transform else item['rect']
                box = self._band(page, rect).adjusted(-2, -2, 2, 2)
                painter.drawRect(box)
                if item['subtype'] == 'ink' and not self.read_only:
                    painter.setBrush(Qt.white)
                    for corner in (box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight()):
                        painter.drawRect(QRectF(corner.x() - 4, corner.y() - 4, 8, 8))
        if self._stroke:
            color, width = (self.highlight_color, self.highlight_width) if self._stroke_freehand else (self.ink_color, self.ink_width)
            painter.setCompositionMode(QPainter.CompositionMode_Multiply if self._stroke_freehand else QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor.fromRgbF(*color), width * self.zoom, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            page = self._stroke_page
            for a, b in zip(self._stroke, self._stroke[1:]):
                painter.drawLine(self._band(page, Rect(*a, *a)).topLeft(), self._band(page, Rect(*b, *b)).topLeft())
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        for page, rect, _ in self.selection_lines:
            if page in visible_set:
                painter.fillRect(self._band(page, rect), SELECTION)
        for i, (page, rect) in enumerate(self.hits):
            if page in visible_set:
                painter.fillRect(self._band(page, rect), CURRENT_HIT if i == self.hit else HIT)
        painter.setBrush(Qt.NoBrush)
        self._comment_pen.setWidthF(1.5 / self.zoom)
        painter.setPen(self._comment_pen)
        for page in visible:
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
            if page in visible_set:
                painter.drawRect(self._band(page, rect))
        if self._outlines:
            painter.setPen(self._outline_pen)
            painter.setBrush(Qt.NoBrush)
            for page, rect in self._outlines:
                if page in visible_set:
                    painter.drawRect(self._band(page, rect))

    # ---- mouse: pan, selection, links, highlight --------------------------------------
    def _locate(self, pos):
        """(page, x, y in points) for a viewport position, snapped to the nearest page."""
        return self.pages.to_page(pos.x() + self.horizontalScrollBar().value(), pos.y() + self._scroll_top())

    def _update_cursor(self, pos=None):
        if self._pan:
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if self._space_pan:
            self.viewport().setCursor(Qt.OpenHandCursor)
            return
        if pos is None:
            local = self.viewport().mapFromGlobal(QCursor.pos())
            if self.viewport().rect().contains(local):
                pos = QPointF(local)
        if pos is not None and len(self.doc):
            page, x, y = self._locate(pos)
            if self.doc.link_at(page, x, y):
                self.viewport().setCursor(Qt.PointingHandCursor)
                return
        self.viewport().setCursor(Qt.OpenHandCursor if self.panning else Qt.IBeamCursor)

    def mousePressEvent(self, event):
        self.setFocus()
        self.activated.emit()
        if self._pan or self._transform or self._stroke:
            return
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and (self._space_pan or self.panning)):
            link = None
            if event.button() == Qt.LeftButton and self.panning and not self._space_pan and len(self.doc):
                page, x, y = self._locate(event.position())
                link = self.doc.link_at(page, x, y)
            if self._space_pan:
                self._space_panned = True
            self._pan = {"pos": event.position(), "button": event.button(), "moved": False, "link": link}
            self._update_cursor(event.position())
            return
        if event.button() != Qt.LeftButton:
            return
        page, x, y = self._locate(event.position())
        if self.selected_annotation and not (self.highlighting or self.drawing or self.commenting or self.read_only):
            selected_page, index = self.selected_annotation
            item = next((a for a in self.doc.annotations(selected_page) if a['index'] == index), None)
            if item and item['subtype'] == 'ink':
                box = self._band(selected_page, item['rect']).adjusted(-2, -2, 2, 2)
                corners = (box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight())
                handle = next((i for i, corner in enumerate(corners)
                               if abs(event.position().x() - corner.x()) <= 7 and abs(event.position().y() - corner.y()) <= 7), None)
                if handle is not None or box.adjusted(-4, -4, 4, 4).contains(event.position()):
                    self._transform = dict(page=selected_page, index=index, start=event.position(),
                                           original=item['rect'], rect=item['rect'], handle=handle)
                    return
        # Without any text to select (a scan), the Highlight tool paints a freehand stroke instead.
        freehand = self.highlighting and not self.doc.words(page)
        if (self.drawing or freehand) and not self.read_only:
            self.select_annotation(None)
            self.selection, self.selection_lines = None, []
            self._stroke_page, self._stroke, self._stroke_freehand = page, [(x, y)], freehand
            return
        picked = self.doc.annotation_at(page, x, y, 4 / self.zoom, self.highlights_visible) if not self.highlighting else None
        comment = self.comment_at(page, x, y) if not self.highlighting and not self.drawing else None
        self._press = {"link": self.doc.link_at(page, x, y), "moved": False, "annotation": picked, "page": page, "comment": comment}
        word = self.doc.word_at(page, x, y)
        if event.modifiers() & Qt.ShiftModifier and self._anchor_word and word is not None:
            self._press["moved"] = True  # Shift+click grows the selection like a drag would
            self._extend_selection(event.position())
            return
        self.selection, self.selection_lines = None, []
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
        if self._pan:
            delta = event.position() - self._pan["pos"]
            dx, dy = round(delta.x()), round(delta.y())
            if dx or dy:
                self._pan["moved"] = True
                self._pan["pos"] = QPointF(self._pan["pos"].x() + dx, self._pan["pos"].y() + dy)
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - dx)
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - dy)
            return
        if self._transform:
            drag = self._transform
            dx = (event.position().x() - drag['start'].x()) / self.zoom
            dy = (event.position().y() - drag['start'].y()) / self.zoom
            rect = drag['original']
            if drag['handle'] is None:
                drag['rect'] = Rect(rect.x0 + dx, rect.y0 + dy, rect.x1 + dx, rect.y1 + dy)
            else:
                left, top = drag['handle'] in (0, 2), drag['handle'] in (0, 1)
                ax, ay = (rect.x1 if left else rect.x0), (rect.y1 if top else rect.y0)
                vx, vy = (-rect.width if left else rect.width), (-rect.height if top else rect.height)
                scale = max(0.05, 1 + (dx * vx + dy * vy) / (vx * vx + vy * vy or 1))
                drag['rect'] = Rect(ax + (rect.x0 - ax) * scale, ay + (rect.y0 - ay) * scale,
                                    ax + (rect.x1 - ax) * scale, ay + (rect.y1 - ay) * scale)
            self.viewport().update()
            return
        page, x, y = self._locate(event.position())
        if self._stroke:
            if page == self._stroke_page:
                self._stroke.append((x, y))
                self.viewport().update()
            return
        if self._press is None:
            self._update_cursor(event.position())
            return
        self.select_annotation(None)
        self._press["moved"] = True
        self._drag = event.position()
        if not self._autoscroll.isActive():
            self._autoscroll.start()
        self._extend_selection(event.position())

    def mouseReleaseEvent(self, event):
        if self._pan:
            if event.button() != self._pan["button"] and event.buttons() & self._pan["button"]:
                return
            pan, self._pan = self._pan, None
            self._update_cursor(event.position())
            if not pan["moved"] and pan["link"]:
                self.follow(pan["link"])
            return
        if self._transform:
            self._finish_transform()
            return
        if self._stroke:
            self._finish_stroke()
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
        elif press["moved"] and self.highlighting and not self.read_only:
            self.highlight_selection()
        elif not press['moved']:
            self.select_annotation(None)
            self.select_comment(None)
        if press['moved']:
            self.selection_changed.emit()

    def contextMenuEvent(self, event):
        self.finish_interaction()
        self.setFocus()
        self.activated.emit()
        page = None
        if event.reason() == QContextMenuEvent.Mouse and len(self.doc):
            page, x, y = self._locate(event.pos())
            if not self.selection_lines:
                comment = self.comment_at(page, x, y) if not self.highlighting and not self.drawing else None
                picked = self.doc.annotation_at(page, x, y, 4 / self.zoom, self.highlights_visible) if not self.highlighting else None
                if comment is not None:
                    self.select_comment(comment)
                elif picked:
                    self.select_annotation((page, picked["index"]))
        menu = self._build_context_menu(page)
        if event.reason() == QContextMenuEvent.Keyboard:
            first = next((action for action in menu.actions() if action.isEnabled() and not action.isSeparator()), None)
            if first is not None:
                menu.setActiveAction(first)
        # popup() uses the main event loop instead of blocking tests inside exec().
        menu.popup(event.globalPos())

    def _build_context_menu(self, page=None):
        menu = self._context_menu
        menu.clear()
        has_selection = bool(self.selection_lines)

        copy_action = menu.addAction("Copy")
        copy_action.setEnabled(has_selection)
        copy_action.triggered.connect(self.copy)

        highlight_action = menu.addAction("Highlight")
        highlight_action.setEnabled(has_selection and not self.read_only)
        if self.read_only:
            highlight_action.setToolTip("This view is read-only while another view has unsaved changes.")
        highlight_action.triggered.connect(self.highlight_selection)

        comment_action = menu.addAction("Comment")
        comment_action.setEnabled(self.notes_model is not None)
        if self.notes_model is None:
            comment_action.setToolTip("Comments are available for papers in the library.")
        comment_page = None if (has_selection or page is None) else page + 1
        comment_action.triggered.connect(lambda _=False, p=comment_page: self.comment_requested.emit(p))

        menu.addSeparator()
        select_all_action = menu.addAction("Select all")
        select_all_action.setEnabled(len(self.doc) > 0)
        select_all_action.triggered.connect(self.select_all)

        if self.selected_comment is not None:
            menu.addSeparator()
            delete_comment = menu.addAction("Delete comment")
            delete_comment.setEnabled(self.notes_model is not None)
            delete_comment.triggered.connect(self.delete_annotation)
        elif self.selected_annotation is not None:
            ann_page, index = self.selected_annotation
            item = next((a for a in self.doc.annotations(ann_page) if a["index"] == index), None)
            kind = "highlight" if item and (item["subtype"] == "highlight" or item["freehand"]) else "ink" if item else "annotation"
            menu.addSeparator()
            delete_annotation = menu.addAction(f"Delete {kind}")
            delete_annotation.setEnabled(not self.read_only)
            delete_annotation.triggered.connect(self.delete_annotation)

        return menu

    def _finish_transform(self):
        drag, self._transform = self._transform, None
        if not self.read_only and self.doc.transform_annotation(drag['page'], drag['index'], drag['rect']):
            self._annotation_changed([drag['page']])
        self.viewport().update()

    def _finish_stroke(self):
        stroke, self._stroke = self._stroke, []
        if self.read_only or len(stroke) < 2:
            return
        free = self._stroke_freehand
        color, width = (self.highlight_color, self.highlight_width) if free else (self.ink_color, self.ink_width)
        self.doc.add_ink(self._stroke_page, [stroke], color, width, free)
        self._annotation_changed([self._stroke_page], reveal=free)

    def event(self, event):
        # A shortcut such as Save or Close must see the stroke under the mouse as part of the document.
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress) and event.key() == Qt.Key_Escape and self._transform:
            self._transform = None
            self.viewport().update()
            return True
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress) and self._transform:
            self._finish_transform()
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress) and self._stroke:
            self._finish_stroke()
        return super().event(event)

    def select_all(self):
        pages = [page for page in range(len(self.doc)) if self.doc.words(page)]
        if pages:
            self._anchor_word = (pages[0], 0)
            self.selection = (self._anchor_word, (pages[-1], len(self.doc.words(pages[-1])) - 1))
            self.selection_lines = self.doc.selection_lines(*self.selection)
            self.viewport().update()
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
        if self.read_only or not pages:
            return
        self.doc.add_highlights(pages, self.highlight_color)
        self.selection, self.selection_lines, self._anchor_word = None, [], None
        self._annotation_changed(list(pages), reveal=True)

    def set_tool(self, tool):
        self.tool = tool if tool in ('select', 'pan', 'highlight', 'ink', 'comment') else 'select'
        self.select_annotation(None)
        self.select_comment(None)
        self._update_cursor()

    def select_annotation(self, selection):
        self.selected_annotation = selection
        if selection and self.selected_comment is not None:
            self.select_comment(None)
        self.annotation_selection_changed.emit()
        self.viewport().update()

    def _annotation_changed(self, pages, reveal=False):
        """A new highlight appears at once, even if highlights were hidden: then every page redraws."""
        shown = reveal and not self.highlights_visible
        self.highlights_visible = self.highlights_visible or reveal
        self.refresh_render(None if shown else pages)
        self.viewport().update()
        self.annotation_selection_changed.emit()
        self.edited.emit()

    def set_annotation_color(self, color):
        if self.selected_comment is not None and self.notes_model:
            note = {**self.selected_comment, "color": QColor.fromRgbF(*color).name()}
            if self.notes_model.update(self.selected_comment, note["color"]):
                self.select_comment(note)
            return
        if self.read_only:
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
        if self.read_only:
            return
        if self.selected_annotation:
            page, index = self.selected_annotation
            if self.doc.update_annotation(page, index, width=width):
                self._annotation_changed([page])
        elif self.highlighting:
            self.highlight_width = width
        else:
            self.ink_width = width

    def delete_annotation(self):
        if self.selected_comment is not None and self.notes_model:
            if self.notes_model.update(self.selected_comment):
                self.select_comment(None)
            return
        if self.selected_annotation and not self.read_only:
            page, index = self.selected_annotation
            self.doc.delete_annotation(page, index)
            self.select_annotation(None)
            self._annotation_changed([page])

    def undo(self):
        if self.notes_model and self.notes_model.deleted is not None:
            self.notes_model.undo_delete()
            return
        if not self.read_only and (pages := self.doc.undo()):
            self.select_annotation(None)
            self._annotation_changed(pages)

    def redo(self):
        if not self.read_only and (pages := self.doc.redo()):
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

    def finish_interaction(self):
        if self._pan:
            self._pan = None
            self._update_cursor()
        if self._transform:
            self._finish_transform()
        if self._stroke:
            self._finish_stroke()

    def save(self):
        """Explicit save of this view's document. False keeps the unsaved state for a retry."""
        self.finish_interaction()
        saved = self.doc.save(on_saving=self._show_saving)
        if saved and id(self.doc.pdf.data) != self._rendered_data_id:
            self.refresh_render()
        self.edited.emit()
        return saved

    # ---- search -----------------------------------------------------------------------
    def _show_hit(self):
        page, rect = self.hits[self.hit]
        band = self._band(page, rect)
        if band.top() < 0 or band.bottom() > self.viewport().height():
            self.goto(page, max(0, rect.y0 - 120 / self.zoom))
        self.viewport().update()

    def find(self, text, direction=1):
        self.hits = self.doc.search(text) if text else []
        if not self.hits:
            self.hit = -1
            self.viewport().update()
            return 0
        page = current_page(self)
        if direction >= 0:
            self.hit = next((i for i, (p, _) in enumerate(self.hits) if p >= page), 0)
        else:
            self.hit = next((i for i in range(len(self.hits) - 1, -1, -1) if self.hits[i][0] <= page), len(self.hits) - 1)
        self._show_hit()
        return len(self.hits)

    def step_hit(self, direction):
        if self.hits:
            self.hit = (self.hit + direction) % len(self.hits)
            self._show_hit()
        else:
            self.viewport().update()
