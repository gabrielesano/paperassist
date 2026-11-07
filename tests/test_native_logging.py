"""Logging filter for pypdf's CFF Type1 fontTools notice."""
import io
import logging
import unittest

import paperassist  # noqa: F401 - installs the pypdf logging filter
from pypdf import PdfReader, PdfWriter
from pypdf._utils import logger_warning
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject


def _cff_type1_pdf_bytes():
    writer = PdfWriter()
    cff = DecodedStreamObject()
    cff.set_data(b"\x01\x00\x04\x01")
    cff[NameObject("/Subtype")] = NameObject("/Type1C")
    descriptor = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/FontDescriptor"),
        NameObject("/FontName"): NameObject("/TestCFF"),
        NameObject("/Flags"): NumberObject(4),
        NameObject("/FontFile3"): writer._add_object(cff),
    }))
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/TestCFF"),
        NameObject("/FontDescriptor"): descriptor,
    }))
    page = writer.add_blank_page(200, 200)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 20 100 Td (hello) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class PypdfWarningFilterTests(unittest.TestCase):
    def test_drops_cff_fonttools_warning_and_keeps_other_pypdf_warnings(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("pypdf")
        handler = Capture(level=logging.WARNING)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        logger_warning(
            "fontTools is required to fully parse the encoding of a CFF Type1 font",
            source="pypdf._cmap",
        )
        PdfReader(io.BytesIO(_cff_type1_pdf_bytes())).pages[0].extract_text()
        logger_warning("Malformed xref entry at offset 42", source="pypdf._cmap")
        logging.getLogger("pypdf._reader").warning("Unexpected EOF marker")

        self.assertFalse(any("fontTools is required" in msg for msg in records), records)
        self.assertTrue(any("Malformed xref entry at offset 42" in msg for msg in records), records)
        self.assertTrue(any("Unexpected EOF marker" in msg for msg in records), records)

        # Removing the filter confirms the synthetic CFF Type1 PDF genuinely triggers pypdf's warning.
        cmap_logger = logging.getLogger("pypdf._cmap")
        cmap_logger.removeFilter(paperassist._allow_pypdf_warning)
        try:
            PdfReader(io.BytesIO(_cff_type1_pdf_bytes())).pages[0].extract_text()
        finally:
            cmap_logger.addFilter(paperassist._allow_pypdf_warning)
        self.assertTrue(any("fontTools is required" in msg for msg in records), records)


if __name__ == "__main__":
    unittest.main()
