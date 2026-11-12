"""PDFium reading/rendering boundary; annotations.py owns pypdf structures.

All coordinates exposed to the UI are top-left displayed PDF points, including
crop offsets and rotation. Documents hold bytes, never an open source file.
"""
import ctypes
from contextlib import closing
from functools import wraps
from threading import RLock
import unicodedata
from typing import NamedTuple

import pypdfium2 as pdfium
import pypdfium2.raw as raw

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
        self.matrix = {0: (1, 0, 0, -1, 0, h), 90: (0, 1, 1, 0, 0, 0),
                       180: (-1, 0, 0, 1, w, 0), 270: (0, -1, -1, 0, h, w)}[page.get_rotation() % 360]

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
    def fold(text):
        return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                       if not unicodedata.combining(c))

    needle = fold(query)
    if not needle:
        return
    letters, owners = [], []
    # PDFium character indices can differ from Python offsets in extracted text.
    for index in range(text_page.count_chars()):
        letter = fold(chr(raw.FPDFText_GetUnicode(text_page, index)))
        letters.append(letter)
        owners.extend([index] * len(letter))
    text, start = "".join(letters), 0
    while (found := text.find(needle, start)) >= 0:
        first, last = owners[found], owners[found + len(needle) - 1]
        yield first, last - first + 1
        start = found + len(needle)


class Pdf:
    @pdf_locked
    def __init__(self, data):
        self.data = bytes(data)
        self._doc = pdfium.PdfDocument(self.data)
        self.sizes = [self._doc.get_page_size(i) for i in range(len(self._doc))]
        self._frames = {}
        self._named_destinations = None
        self._named_pages = {}

    @property
    def is_closed(self):
        return self._doc.raw is None

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
        return words

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
                target = raw.FPDFDest_GetDestPageIndex(self._doc, dest)
                if not 0 <= target < len(self.sizes):
                    continue
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
                top = target_frame.point(x.value if has_x.value else target_frame.origin[0],
                                         y.value if has_y.value else target_frame.top)[1] if has_x.value or has_y.value else 0
                item = Link(rect, None, target, top, self._destination_name(dest))
            else:
                continue
            result.append(item)
        page.close()
        return result

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

    @pdf_locked
    def search(self, text):
        hits = []
        for number in range(len(self.sizes)):
            frame, page = self.frame(number), self._doc[number]
            text_page = page.get_textpage()
            for match in _search_ranges(text_page, text):
                line = None
                for i in range(text_page.count_rects(*match)):
                    rect = Rect(*text_page.get_rect(i))
                    if line and min(line.y1, rect.y1) - max(line.y0, rect.y0) > 0.5 * min(line.height, rect.height):
                        line = line.union(rect)  # PDFium splits a match at font changes and spaces
                    else:
                        line and hits.append((number, frame.rect(*line)))
                        line = rect
                line and hits.append((number, frame.rect(*line)))
            text_page.close()
            page.close()
        return hits

    def render_data(self, highlights=True):
        if highlights:
            return self.data
        from .annotations import without_highlights
        return without_highlights(self.data)

    def _edit(self, kind, page, **values):
        from . import annotations
        data = annotations.apply(self.data, kind, page, **values)
        self._replace(data)

    @pdf_locked
    def _replace(self, data):
        document = pdfium.PdfDocument(data)
        self._doc.close()
        self.data, self._doc = data, document
        self._named_destinations = None
        self._named_pages.clear()

    def add_highlight(self, page, rects, color=HIGHLIGHT, opacity=1):
        from . import annotations
        frame = self.frame(page)
        quads = []
        for rect in rects:
            points = [frame.to_pdf(x, y) for x, y in ((rect.x0, rect.y0), (rect.x1, rect.y1))]
            quads.append(annotations.bounds(points))
        self._edit('highlight', page, rects=quads, color=color, opacity=opacity)

    def add_ink(self, page, strokes, color=(0, 0, 0), width=2, freehand=False):
        frame = self.frame(page)
        self._edit('ink', page, strokes=[[frame.to_pdf(x, y) for x, y in stroke] for stroke in strokes],
                   color=color, width=width, freehand=freehand)

    def annotations(self, page):
        from . import annotations
        frame = self.frame(page)
        result = annotations.read(self.data, page)
        for item in result:
            item['rect'] = frame.rect(*item['rect'])
            item['quads'] = [frame.rect(*r) for r in item['quads']]
            item['strokes'] = [[frame.point(*p) for p in stroke] for stroke in item['strokes']]
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
        self._doc.close()


class RenderedPage:
    def __init__(self, bitmap):
        self._bitmap = bitmap
        self.width, self.height = bitmap.width, bitmap.height

    @property
    def rgb(self):
        return memoryview(self._bitmap.buffer)


class PageRenderer:
    @pdf_locked
    def __init__(self, data):
        self.data = bytes(data)
        self._doc = pdfium.PdfDocument(self.data)

    @pdf_locked
    def render(self, page, scale):
        with closing(self._doc[page]) as target:
            return RenderedPage(target.render(scale=scale, rev_byteorder=True, prefer_bgrx=False))

    @pdf_locked
    def close(self):
        self._doc.close()


def trim_caches():
    # PDFium releases page caches when page handles close; there is no public global trim API.
    pass
