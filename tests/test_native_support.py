"""Synthetic PDFs and skip rules shared by the native reader tests."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # tests never open a real window

try:
    import pymupdf
except ImportError:
    pymupdf = None

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None

needs_pdf = unittest.skipUnless(pymupdf, "PyMuPDF is not installed (pip install -e '.[native]')")
needs_qt = unittest.skipUnless(pymupdf and PySide6, "PySide6 or PyMuPDF is not installed (pip install -e '.[native]')")

BODY = "Coronal loops are heated by nanoflares and observed in the extreme ultraviolet. " * 8


def make_pdf(path, pages=4, rotate=0):
    """Pages of justified body text; page 1 cites [1] with a link to the bibliography on the last page."""
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page(width=400, height=500)
        page.insert_textbox(pymupdf.Rect(40, 60, 360, 320), f"Page {number + 1}. " + BODY, fontsize=11)
        if number == 0:
            page.insert_text((40, 340), "As shown by Smith [1] the loops cool.", fontsize=11)
        if number == pages - 1:
            page.insert_text((40, 120), "[1] Smith, J. 2015, Journal of Loops, 1, 1", fontsize=11)
        if rotate:
            page.set_rotation(rotate)
    cite = doc[0].search_for("[1]")[0]  # the target page must exist before the link can point at it
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": cite, "page": pages - 1, "to": pymupdf.Point(40, 120)})
    doc.save(path)
    doc.close()
    return path
