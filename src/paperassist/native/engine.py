"""The only module that talks to MuPDF (PyMuPDF).

Everything the rest of the reader knows about a PDF comes out of here as plain Python
data, so replacing the engine means rewriting this file and nothing else. MuPDF
reports text and links in the page's unrotated space; this module converts them to
what the user sees, and converts highlight rectangles back when writing.
"""
from typing import NamedTuple

import pymupdf

HIGHLIGHT = (1.0, 0.92, 0.2)


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


def _rect(value):
    return Rect(value.x0, value.y0, value.x1, value.y1)


class Pdf:
    """A PDF opened for reading its structure and for appending highlights.

    It is opened from bytes, not from a path, so no file handle stays open: Windows
    cannot replace a file that is open, and saving replaces it (see source.py).
    """

    def __init__(self, data):
        self._doc = pymupdf.open(stream=data, filetype="pdf")
        self.sizes = [(p.rect.width, p.rect.height) for p in self._doc]
        self._rotation = [p.rotation_matrix for p in self._doc]  # unrotated space -> displayed space

    @property
    def is_closed(self):
        return self._doc.is_closed

    def words(self, page):
        """[(x0, y0, x1, y1, text, block)] in the engine's reading order."""
        m = self._rotation[page]
        return [(*_rect(pymupdf.Rect(w[:4]) * m), w[4], w[5]) for w in self._doc[page].get_text("words")]

    def links(self, page):
        m, found = self._rotation[page], []
        for link in self._doc[page].get_links():
            if link["kind"] == pymupdf.LINK_URI:
                found.append(Link(_rect(link["from"] * m), link["uri"], None, 0.0))
            elif link.get("page", -1) >= 0:  # goto, or a named destination MuPDF resolved
                target, to = link["page"], link.get("to")
                # MuPDF leaves a resolved named destination in PDF space (y up), unlike a goto;
                # journals such as ApJ link every citation that way.
                if to and link["kind"] == pymupdf.LINK_NAMED and (to.x or to.y):
                    to = to * self._doc[target].transformation_matrix
                point = (to or pymupdf.Point(0, 0)) * self._rotation[target]
                found.append(Link(_rect(link["from"] * m), None, target, point.y))
        return found

    def search(self, text):
        """[(page, Rect)] for every case-insensitive occurrence."""
        return [(page, _rect(rect * self._rotation[page])) for page in range(len(self.sizes))
                for rect in self._doc[page].search_for(text)]

    def add_highlight(self, page, rects):
        target = self._doc[page]  # an annotation is only valid while its page object is alive
        quads = [pymupdf.Rect(r) * ~self._rotation[page] for r in rects]
        annot = target.add_highlight_annot(quads=quads)
        annot.set_colors(stroke=HIGHLIGHT)
        annot.update()

    def can_save_incrementally(self):
        return self._doc.can_save_incrementally()

    def write(self, out):
        """Write the whole new file to the binary file object out.

        When possible the update is appended to the original bytes, which stay a prefix.
        MuPDF copies them to the output itself, so out needs no existing file. PyMuPDF's
        own incremental save insists on an on-disk original, hence the direct call.
        """
        if not self.can_save_incrementally():
            out.write(self._doc.tobytes())
            return
        options = pymupdf.mupdf.PdfWriteOptions()
        options.do_incremental = True
        options.do_encrypt = pymupdf.mupdf.PDF_ENCRYPT_KEEP
        options.dont_regenerate_id = True
        sink = pymupdf.JM_new_output_fileptr(out)
        pymupdf.mupdf.pdf_write_document(pymupdf._as_pdf_document(self._doc), sink, options)
        sink.fz_close_output()

    def close(self):
        if not self._doc.is_closed:
            self._doc.close()


class RenderedPage:
    def __init__(self, pixmap):
        self._pixmap = pixmap  # the buffer is only valid while the pixmap lives
        self.width, self.height = pixmap.width, pixmap.height

    @property
    def rgb(self):
        return self._pixmap.samples_mv


class PageRenderer:
    """A read-only replica of the file that draws pages; used by the render process."""

    def __init__(self, data):
        self._doc = pymupdf.open(stream=data, filetype="pdf")  # bytes, so a save can replace the file on Windows

    def render(self, page, scale):
        pixmap = self._doc[page].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return RenderedPage(pixmap)


def trim_caches():
    pymupdf.TOOLS.store_shrink(100)
