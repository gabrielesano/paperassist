"""PDFium reading/rendering boundary; annotations.py owns pypdf structures.

All coordinates exposed to the UI are top-left displayed PDF points, including
crop offsets and rotation. Documents hold bytes, never an open source file.
"""
import ctypes
import math
from contextlib import closing
from functools import wraps
from pathlib import Path
from threading import RLock
import unicodedata
from typing import NamedTuple

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from paperassist.citation import fold

HIGHLIGHT = tuple(value / 255 for value in (245, 215, 110))
FIT_TOP = {raw.PDFDEST_VIEW_FITR, raw.PDFDEST_VIEW_FITH, raw.PDFDEST_VIEW_FITBH}
BLANK = {" ", "\r", "\n", "\t", "\ufffe"}


_PDF_LOCK = RLock()


def pdf_locked(method):
    """PDFium is process-global: background analysis must not race the reader's PDF calls."""
    @wraps(method)
    def locked(*args, **kwargs):
        with _PDF_LOCK:
            return method(*args, **kwargs)
    return locked


class Rect(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    def union(self, other):
        return Rect(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))

    def contains(self, x, y):
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


class Link(NamedTuple):
    rect: Rect
    uri: str | None  # an external link...
    page: int | None  # ...or an internal one with its target page and y (displayed points)
    y: float
    destination: str | None = None


class Frame:
    """Maps PDF user space (origin bottom-left, crop box, rotation) to top-left view points."""

    def __init__(self, page):
        left, bottom, right, top = page.get_bbox()
        w, h = right - left, top - bottom
        # Affine map X = a*u + c*v + e, Y = b*u + d*v + f with u, v measured from the box corner.
        self.origin = (left, bottom)
        self.top = top
        self.rotation = page.get_rotation() % 360
        self.matrix = {0: (1, 0, 0, -1, 0, h), 90: (0, 1, 1, 0, 0, 0),
                       180: (-1, 0, 0, 1, w, 0), 270: (0, -1, -1, 0, h, w)}[self.rotation]

    def point(self, x, y):
        a, b, c, d, e, f = self.matrix
        u, v = x - self.origin[0], y - self.origin[1]
        return a * u + c * v + e, b * u + d * v + f

    def to_pdf(self, x, y):
        a, b, c, d, e, f = self.matrix
        x, y = x - e, y - f
        det = a * d - b * c
        return (d * x - c * y) / det + self.origin[0], (a * y - b * x) / det + self.origin[1]

    def rect(self, left, bottom, right, top):
        (x0, y0), (x1, y1) = self.point(left, bottom), self.point(right, top)
        return Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _search_ranges(text_page, query):
    needle = fold(query)
    if not needle:
        return
    letters, owners = [], []
    # PDFium character indices can differ from Python offsets in extracted text.
    for index in range(text_page.count_chars()):
        ch = chr(raw.FPDFText_GetUnicode(text_page, index))
        if ch.isspace():
            if letters and letters[-1] != " ":
                letters.append(" ")
                owners.append(index)
        elif letter := fold(ch):
            letters.append(letter)
            owners.extend([index] * len(letter))
    text, start = "".join(letters), 0
    while (found := text.find(needle, start)) >= 0:
        first, last = owners[found], owners[found + len(needle) - 1]
        yield first, last - first + 1
        start = found + len(needle)


def _same_visual_line(band_line, band_rect, word_line, rect):
    if word_line == band_line:
        return True
    across_y = min(band_rect.y1, rect.y1) - max(band_rect.y0, rect.y0)
    if across_y <= 0.5 * min(band_rect.height, rect.height):
        return False
    gap_x = max(rect.x0 - band_rect.x1, band_rect.x0 - rect.x1)
    return gap_x <= 2 * max(band_rect.height, rect.height)


def _search_sidecar_words(number, words, query):
    needle = fold(query)
    if not needle or not words:
        return []
    parts, owners = [], []
    for word_index, word in enumerate(words):
        folded = fold(word[4])
        if not folded:
            continue
        if parts:
            parts.append(" ")
            owners.append(None)
        parts.append(folded)
        word_len = len(folded)
        owners.extend((word_index, offset, word_len) for offset in range(word_len))
    text, hits, start = "".join(parts), [], 0
    while (found := text.find(needle, start)) >= 0:
        spans = {}
        for owner in owners[found:found + len(needle)]:
            if owner is None:
                continue
            word_index, offset, word_len = owner
            if word_index in spans:
                spans[word_index][1] = offset
            else:
                spans[word_index] = [offset, offset, word_len]
        band_line = band_rect = None
        for word_index, (first_char, last_char, word_len) in spans.items():
            x0, y0, x1, y1, _, word_line = words[word_index]
            width = x1 - x0
            rect = Rect(x0 + width * (first_char / word_len), y0, x0 + width * ((last_char + 1) / word_len), y1)
            if band_rect is not None and _same_visual_line(band_line, band_rect, word_line, rect):
                band_rect = band_rect.union(rect)
            else:
                if band_rect is not None:
                    hits.append((number, band_rect))
                band_line, band_rect = word_line, rect
        if band_rect is not None:
            hits.append((number, band_rect))
        start = found + len(needle)
    return hits


class RenderedPage:
    def __init__(self, bitmap):
        self._bitmap = bitmap
        self.width, self.height = bitmap.width, bitmap.height

    @property
    def rgb(self):
        return memoryview(self._bitmap.buffer)


def _annot_string(annot, key):
    length = raw.FPDFAnnot_GetStringValue(annot, key, None, 0)
    if length <= 2:
        return ""
    buffer = (ctypes.c_ushort * ((length + 1) // 2))()
    raw.FPDFAnnot_GetStringValue(annot, key, buffer, length)
    return bytes(buffer)[:length].decode("utf-16-le", errors="replace").rstrip("\0")


class Pdf:
    @pdf_locked
    def __init__(self, data, *, owner=None, sha256=None, ocr_cache_dir=None):
        self.sha256 = sha256
        self.ocr_cache_dir = Path(ocr_cache_dir) if ocr_cache_dir else None
        if isinstance(data, ctypes.Array):
            self._buffer = data
            self._owner = owner
            self._data = None
            source = self._buffer
        else:
            self._buffer = None
            self._owner = owner
            self._data = bytes(data)
            source = self._data
        self._doc = None
        try:
            self._doc = pdfium.PdfDocument(source)
            self.sizes = [self._doc.get_page_size(i) for i in range(len(self._doc))]
            if not self.sizes or any(not math.isfinite(size) or size <= 0 for pair in self.sizes for size in pair):
                raise ValueError('PDF must contain pages with positive width and height')
        except Exception:
            if self._doc is not None:
                self._doc.close()
                self._doc = None
            self._buffer = None
            self._owner = None
            raise
        self._frames = {}
        self._named_destinations = None
        self._named_pages = {}

    @property
    def data(self):
        if self._data is not None:
            return self._data
        return bytes(self._buffer) if self._buffer is not None else b""

    @data.setter
    def data(self, value):
        self._data = value
        self._buffer = None

    @property
    def is_closed(self):
        return self._doc is None or self._doc.raw is None

    @pdf_locked
    def render(self, page, scale, *, highlights=True):
        with closing(self._doc[page]) as target:
            hidden = []
            if not highlights:
                for index in range(raw.FPDFPage_GetAnnotCount(target)):
                    annot = raw.FPDFPage_GetAnnot(target, index)
                    if not annot:
                        continue
                    subtype = raw.FPDFAnnot_GetSubtype(annot)
                    if subtype == raw.FPDF_ANNOT_HIGHLIGHT or (
                        subtype == raw.FPDF_ANNOT_INK and _annot_string(annot, b"IT").lstrip("/") == "InkHighlight"
                    ):
                        flags = raw.FPDFAnnot_GetFlags(annot)
                        hidden.append((annot, flags))
                        raw.FPDFAnnot_SetFlags(annot, flags | raw.FPDF_ANNOT_FLAG_HIDDEN)
                    else:
                        raw.FPDFPage_CloseAnnot(annot)
            try:
                return RenderedPage(target.render(scale=scale, rev_byteorder=True, prefer_bgrx=False))
            finally:
                for annot, flags in hidden:
                    raw.FPDFAnnot_SetFlags(annot, flags)
                    raw.FPDFPage_CloseAnnot(annot)

    @pdf_locked
    def frame(self, number):
        if number not in self._frames:
            with closing(self._doc[number]) as page:
                self._frames[number] = Frame(page)
        return self._frames[number]

    @pdf_locked
    def words(self, number):
        frame, page = self.frame(number), self._doc[number]
        text_page = page.get_textpage()
        text = "".join(chr(raw.FPDFText_GetUnicode(text_page, i) or 32) for i in range(text_page.count_chars()))
        words, start, line = [], None, 0
        for i, char in enumerate(text + " "):
            hyphen = char in ('\ufffe', '\x02', '-') and bool(raw.FPDFText_IsHyphen(text_page, i))
            if char in BLANK or hyphen:
                # PDFium exposes line-end hyphens as U+FFFE in ranges, or U+0002 via GetUnicode.
                if start is not None:
                    boxes = [text_page.get_charbox(k, loose=True) for k in range(start, i + hyphen)]  # loose: font-height bands
                    rect = frame.rect(min(b[0] for b in boxes), min(b[1] for b in boxes),
                                      max(b[2] for b in boxes), max(b[3] for b in boxes))
                    words.append((*rect, unicodedata.normalize("NFKC", text[start:i]) + "-" * hyphen, line))
                    start = None
                line += hyphen or char == "\n"
            elif start is None:
                start = i
        text_page.close()
        page.close()
        if not words and self.ocr_cache_dir and self.sha256:
            from paperassist import ocr
            return ocr.sidecar_words(self.ocr_cache_dir, self.sha256, number, self.sizes[number])
        return words

    def _dest_target(self, dest, *, clamp_top=False):
        target = raw.FPDFDest_GetDestPageIndex(self._doc, dest)
        if not 0 <= target < len(self.sizes):
            return None, 0.0
        has_x, has_y, has_zoom = (ctypes.c_int() for _ in range(3))
        x, y, zoom = (ctypes.c_float() for _ in range(3))
        raw.FPDFDest_GetLocationInPage(dest, has_x, has_y, has_zoom, x, y, zoom)
        if not has_y.value:  # fit modes keep their top edge in the view parameters, not in the location
            count, params = ctypes.c_ulong(4), (ctypes.c_float * 4)()
            view = raw.FPDFDest_GetView(dest, count, params)
            y.value, has_y.value = {raw.PDFDEST_VIEW_FITR: params[3], raw.PDFDEST_VIEW_FITH: params[0],
                                    raw.PDFDEST_VIEW_FITBH: params[0]}.get(view, 0), view in FIT_TOP
            if view in (raw.PDFDEST_VIEW_FITR, raw.PDFDEST_VIEW_FITV, raw.PDFDEST_VIEW_FITBV):
                x.value, has_x.value = params[0], True
        target_frame = self.frame(target)
        if clamp_top:
            if not has_y.value or target_frame.rotation:
                return target, 0.0
            return target, max(0.0, target_frame.point(target_frame.origin[0], y.value)[1])
        top = (target_frame.point(x.value if has_x.value else target_frame.origin[0],
                                  y.value if has_y.value else target_frame.top)[1]
               if has_x.value or has_y.value else 0)
        return target, top

    @pdf_locked
    def links(self, number):
        """Resolve internal destinations including XYZ, FitH and FitR to displayed coordinates."""
        frame, page = self.frame(number), self._doc[number]
        result, position, link = [], ctypes.c_int(0), raw.FPDF_LINK()
        while raw.FPDFLink_Enumerate(page, position, link):
            box = raw.FS_RECTF()
            raw.FPDFLink_GetAnnotRect(link, box)
            rect = frame.rect(box.left, box.bottom, box.right, box.top)
            action = raw.FPDFLink_GetAction(link)
            if action and raw.FPDFAction_GetType(action) == raw.PDFACTION_URI:
                size = raw.FPDFAction_GetURIPath(self._doc, action, None, 0)
                buffer = ctypes.create_string_buffer(size)
                raw.FPDFAction_GetURIPath(self._doc, action, buffer, size)
                item = Link(rect, buffer.value.decode("utf-8", errors="replace"), None, 0)
            elif dest := raw.FPDFLink_GetDest(self._doc, link):
                target, top = self._dest_target(dest)
                if target is None:
                    continue
                item = Link(rect, None, target, top, self._destination_name(dest))
            else:
                continue
            result.append(item)
        page.close()
        return result

    @pdf_locked
    def outline(self):
        """[(title, level, page index, y)] with y in displayed points from the page top; [] without bookmarks."""
        entries = []
        for bookmark in self._doc.get_toc():
            dest = raw.FPDFBookmark_GetDest(self._doc, bookmark)
            if not dest:
                action = raw.FPDFBookmark_GetAction(bookmark)
                if action:
                    dest = raw.FPDFAction_GetDest(self._doc, action)
            if not dest:
                continue
            page, top = self._dest_target(dest, clamp_top=True)
            if page is not None:
                entries.append((bookmark.get_title(), bookmark.level, page, top))
        return entries

    def _destination_name(self, destination):
        if self._named_destinations is None:
            self._named_destinations = {}
            for index in range(raw.FPDF_CountNamedDests(self._doc)):
                length = ctypes.c_long()
                raw.FPDF_GetNamedDest(self._doc, index, None, length)
                if length.value <= 0:
                    continue
                name = ctypes.create_string_buffer(length.value)
                dest = raw.FPDF_GetNamedDest(self._doc, index, name, length)
                if dest:
                    key = ctypes.cast(dest, ctypes.c_void_p).value
                    value = name.raw.decode("utf-16-le", errors="replace").rstrip("\0")
                    self._named_destinations[key] = value
                    self._named_pages[value] = raw.FPDFDest_GetDestPageIndex(self._doc, dest)
        return self._named_destinations.get(ctypes.cast(destination, ctypes.c_void_p).value)

    @pdf_locked
    def named_destinations(self):
        self._destination_name(raw.FPDF_DEST())
        return dict(self._named_pages)

    def search(self, text):
        return [hit for page in range(len(self.sizes)) for hit in self.search_page(page, text)]

    @pdf_locked
    def search_page(self, number, text):
        hits = []
        frame = self.frame(number)
        with closing(self._doc[number]) as page, closing(page.get_textpage()) as text_page:
            has_chars = text_page.count_chars() > 0
            for match in _search_ranges(text_page, text):
                line = None
                angle = raw.FPDFText_GetCharAngle(text_page, match[0])
                a, b, c, d, _, _ = frame.matrix
                vertical = abs(b * math.cos(angle) + d * math.sin(angle)) > abs(a * math.cos(angle) + c * math.sin(angle))
                across = 0 if vertical else 1
                for i in range(text_page.count_rects(*match)):
                    rect = frame.rect(*text_page.get_rect(i))
                    if line and min(line[across + 2], rect[across + 2]) - max(line[across], rect[across]) > 0.5 * min(
                            line[across + 2] - line[across], rect[across + 2] - rect[across]):
                        line = line.union(rect)  # PDFium splits a match at font changes and spaces
                    else:
                        line and hits.append((number, line))
                        line = rect
                line and hits.append((number, line))
        if not hits and not has_chars and self.ocr_cache_dir and self.sha256:
            from paperassist import ocr
            words = ocr.sidecar_words(self.ocr_cache_dir, self.sha256, number, self.sizes[number])
            return _search_sidecar_words(number, words, text)
        return hits

    def render_data(self, highlights=True):
        if highlights:
            return self.data
        from .annotations import without_highlights
        return without_highlights(self.data)

    def _edit(self, kind, page, **values):
        from . import annotations
        self._replace(annotations.apply(self.data, kind, page, **values))

    @pdf_locked
    def _replace(self, data):
        document = pdfium.PdfDocument(data)
        if self._doc is not None:
            self._doc.close()
        self._buffer = None
        if self._owner is not None:
            self._owner.close()
            self._owner = None
        self._data, self._doc = data, document
        self._named_destinations = None
        self._named_pages.clear()

    def add_highlight(self, page, rects, color=HIGHLIGHT, opacity=1):
        self.add_highlights({page: rects}, color, opacity)

    def add_highlights(self, pages, color=HIGHLIGHT, opacity=1):
        from . import annotations
        edits = []
        for page, rects in pages.items():
            frame = self.frame(page)
            quads = [annotations.bounds([frame.to_pdf(r.x0, r.y0), frame.to_pdf(r.x1, r.y1)]) for r in rects]
            edits.append(('highlight', page, {'rects': quads, 'color': color, 'opacity': opacity}))
        self._replace(annotations.apply_edits(self.data, edits))

    def add_ink(self, page, strokes, color=(0, 0, 0), width=2, freehand=False):
        frame = self.frame(page)
        self._edit('ink', page, strokes=[[frame.to_pdf(x, y) for x, y in stroke] for stroke in strokes],
                   color=color, width=width, freehand=freehand)

    def annotations(self, page):
        return self.annotations_for_pages([page])[page]

    def annotations_for_pages(self, pages):
        from . import annotations
        result = {}
        for page, items in annotations.read_pages(self.data, pages):
            frame = self.frame(page)
            for item in items:
                item['rect'] = frame.rect(*item['rect'])
                item['quads'] = [frame.rect(*r) for r in item['quads']]
                item['strokes'] = [[frame.point(*p) for p in stroke] for stroke in item['strokes']]
            result[page] = items
        return result

    def update_annotation(self, page, index, color=None, width=None):
        self._edit('update', page, index=index, color=color, width=width)

    def transform_annotation(self, page, index, rect):
        from .annotations import bounds
        frame = self.frame(page)
        box = bounds([frame.to_pdf(rect.x0, rect.y0), frame.to_pdf(rect.x1, rect.y1)])
        self._edit('transform', page, index=index, rect=box)

    def delete_annotation(self, page, index):
        self._edit('delete', page, index=index)

    def restore_annotations(self, previous, pages):
        from . import annotations
        self._replace(annotations.restore(self.data, previous, pages))

    def write(self, out):
        out.write(self.data)

    @pdf_locked
    def close(self):
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._buffer = None
        if self._owner is not None:
            self._owner.close()
            self._owner = None
