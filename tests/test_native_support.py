"""Synthetic PDFs and skip rules shared by the native reader tests."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # tests never open a real window

from importlib.util import find_spec
from textwrap import wrap

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject

pdf_available = find_spec('pypdfium2') is not None

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None

needs_pdf = unittest.skipUnless(pdf_available, "PDFium is not installed (pip install -e '.[native]')")
needs_qt = unittest.skipUnless(pdf_available and PySide6, "PySide6 or PDFium is not installed (pip install -e '.[native]')")

BODY = "Coronal loops are heated by nanoflares and observed in the extreme ultraviolet. " * 8


def make_pdf(path, pages=4, rotate=0):
    """Pages of justified body text; page 1 cites [1] with a link to the bibliography on the last page."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Courier')}))
    for number in range(pages):
        page = writer.add_blank_page(400, 500)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        content = 'BT /F1 11 Tf 40 430 Td 14 TL '
        lines = wrap(f'Page {number + 1}. ' + BODY, 48)
        content += ' T* '.join(f'({line}) Tj' for line in lines) + ' ET\n'
        if number == 0:
            content += 'BT /F1 11 Tf 40 160 Td (As shown by Smith [1] the loops cool.) Tj ET\n'
        if number == pages - 1:
            content += 'BT /F1 11 Tf 40 380 Td ([1] Smith, J. 2015, Journal of Loops, 1, 1) Tj ET\n'
        stream = DecodedStreamObject()
        stream.set_data(content.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
        if rotate:
            page.rotate(rotate)
    left = 40 + len('As shown by Smith ') * 6.6
    writer.add_annotation(0, DictionaryObject({NameObject('/Type'): NameObject('/Annot'),
        NameObject('/Subtype'): NameObject('/Link'),
        NameObject('/Rect'): ArrayObject(FloatObject(v) for v in (left, 157, left + 19.8, 171)),
        NameObject('/Dest'): ArrayObject([writer.pages[-1].indirect_reference, NameObject('/XYZ'),
                                         FloatObject(40), FloatObject(380), NumberObject(0)])}))
    writer.write(path)
    return path
