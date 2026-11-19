"""Shared Qt/PDF test harness and synthetic PDF builders (not discovered by unittest)."""
import atexit
import io
import os
import tempfile
import time
import unittest
from importlib.util import find_spec
from pathlib import Path
from textwrap import wrap
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # tests never open a real window

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject

pdf_available = find_spec("pypdfium2") is not None

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None

needs_pdf = unittest.skipUnless(pdf_available, "PDFium is not installed (pip install -e '.')")
needs_qt = unittest.skipUnless(pdf_available and PySide6, "PySide6 or PDFium is not installed (pip install -e '.')")

BODY = "Coronal loops are heated by nanoflares and observed in the extreme ultraviolet. " * 8

_CLIENT = None


def pdf_bytes(writer):
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def make_pdf(path, pages=4, rotate=0):
    """Pages of justified body text; page 1 cites [1] with a link to the bibliography on the last page."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Courier"),
    }))
    for number in range(pages):
        page = writer.add_blank_page(400, 500)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        content = "BT /F1 11 Tf 40 430 Td 14 TL "
        lines = wrap(f"Page {number + 1}. " + BODY, 48)
        content += " T* ".join(f"({line}) Tj" for line in lines) + " ET\n"
        if number == 0:
            content += "BT /F1 11 Tf 40 160 Td (As shown by Smith [1] the loops cool.) Tj ET\n"
        if number == pages - 1:
            content += "BT /F1 11 Tf 40 380 Td ([1] Smith, J. 2015, Journal of Loops, 1, 1) Tj ET\n"
        stream = DecodedStreamObject()
        stream.set_data(content.encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        if rotate:
            page.rotate(rotate)
    left = 40 + len("As shown by Smith ") * 6.6
    writer.add_annotation(0, DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (left, 157, left + 19.8, 171)),
        NameObject("/Dest"): ArrayObject([writer.pages[-1].indirect_reference, NameObject("/XYZ"),
                                          FloatObject(40), FloatObject(380), NumberObject(0)]),
    }))
    writer.write(path)
    return path


def qt_app():
    from PySide6.QtWidgets import QApplication
    from paperassist.native.theme import apply_theme

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        apply_theme(app)
    elif not app.styleSheet():
        apply_theme(app)
    return app


def shared_client():
    global _CLIENT
    if _CLIENT is None or not _CLIENT.process.is_alive():
        from paperassist.native.worker import RenderClient

        qt_app()
        _CLIENT = RenderClient()
    return _CLIENT


def _shutdown_client():
    global _CLIENT
    if _CLIENT is not None:
        try:
            _CLIENT.shutdown()
        except Exception:
            pass
        _CLIENT = None


atexit.register(_shutdown_client)


def pump(until=None, *, seconds=0.15, timeout=10.0):
    app = qt_app()
    end = time.perf_counter() + (timeout if until is not None else seconds)
    while time.perf_counter() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.002)
    app.processEvents()
    return until() if until is not None else True


def flush_deletes():
    from PySide6.QtCore import QCoreApplication, QEvent

    app = qt_app()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def cleanup_window(window):
    from PySide6.QtWidgets import QDialog

    for dialog in window.findChildren(QDialog):
        dialog.reject()
    for pane in window.all_panes():
        pane.release()
    window.hide()
    window.deleteLater()
    flush_deletes()


class QtTestCase(unittest.TestCase):
    """Shared Qt test base: process-wide QApplication & RenderClient, isolated config & temp dir."""
    use_client = True

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        cls.client = shared_client() if cls.use_client else None

    def setUp(self):
        from paperassist.native.theme import apply_theme

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        config = patch("paperassist.paths.config_file", return_value=self.dir / "config.json")
        config.start()
        self.addCleanup(config.stop)
        self.addCleanup(lambda: apply_theme(self.app))

    def pump(self, until=None, seconds=0.15, timeout=10.0):
        ok = pump(until, seconds=seconds, timeout=timeout)
        if until is not None:
            self.assertTrue(ok, "timed out")
        return ok

    def flush_deletes(self):
        flush_deletes()

    def cleanup_window(self, window):
        cleanup_window(window)
