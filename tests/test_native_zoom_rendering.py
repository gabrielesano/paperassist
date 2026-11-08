"""High-zoom rendering remains visible under cache and prefetch pressure."""
import tempfile
import time
import unittest
from pathlib import Path

from test_native_support import PySide6, make_pdf, needs_qt, pdf_available

if pdf_available and PySide6:
    from pypdf import PdfReader, PdfWriter
    from PySide6.QtWidgets import QApplication
    from paperassist.native.document import PdfDocument
    from paperassist.native.layout import PageCache
    from paperassist.native.renderer import RenderClient
    from paperassist.native.source import FileSource
    from paperassist.native.view import PdfView


@needs_qt
class ZoomRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()

    def pump(self, until, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if until():
                return
            time.sleep(.005)
        self.fail("render timed out")

    def test_visible_entries_are_evicted_after_prefetch_entries(self):
        cache = PageCache(100)
        cache.put(0, "visible", 40)
        cache.put(1, "near", 40)
        cache.put(2, "new visible", 40, protected=(0, 2))
        self.assertEqual(set(cache.entries), {0, 2})

    def test_visible_pages_survive_prefetch_and_page_boundary_at_400_percent(self):
        for size in ((612, 792), (595, 842)):
            with self.subTest(page_size=size):
                self.exercise_zoom(size)

    def exercise_zoom(self, page_size):
        with tempfile.TemporaryDirectory() as folder:
            path = make_pdf(Path(folder) / "paper.pdf", pages=3)
            reader = PdfReader(path)
            writer = PdfWriter()
            for page in reader.pages:
                page.mediabox.upper_right = page_size
                writer.add_page(page)
            with path.open("wb") as output:
                writer.write(output)
            doc = PdfDocument(FileSource(path))

            class HiDpiView(PdfView):
                ratio = 2.0

                def devicePixelRatioF(self):
                    return self.ratio

            view = HiDpiView(doc, self.client)
            view.resize(700, 600)
            view.show()
            try:
                self.pump(lambda: view.has_page(0))
                view._set_zoom(4)
                for ratio in (1.0, 1.5, 2.0):
                    view.ratio = ratio
                    view.goto(0, 365)
                    view.horizontalScrollBar().setValue(0)
                    view._want()
                    self.pump(lambda: view.has_page(0))
                    image = view.viewport().grab().toImage().scaled(140, 120)
                    dark = sum(1 for y in range(image.height()) for x in range(image.width())
                               if image.pixelColor(x, y).lightness() < 100)
                    self.assertGreater(dark, 10, f"400% at DPR {ratio} painted blank")
                # The bottom of page 0 and the top of page 1 both need pixels.
                view.cache.budget = 64 * 1024 * 1024
                view.verticalScrollBar().setValue(int(view.pages.tops[1] - view.viewport().height() / 2))
                self.assertEqual(view.visible_pages(), [0, 1])
                self.pump(lambda: all(view.has_page(page) for page in (0, 1)))
                view._page_ready(view.doc_id, 2, view._scale(), 1, 1, bytes(3), 0)
                self.assertNotIn(2, view.cache)  # a late, no-longer-wanted render cannot evict a visible page
                self.pump(lambda: view.first_paint is not None)
                received = len(view.render_seconds)
                for _ in range(30):  # allow queued prefetch results to arrive without scrolling
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertTrue(all(view.has_page(page) for page in (0, 1)))
                self.assertEqual(len(view.render_seconds), received)
                self.assertLessEqual(view.cache.total(), view.cache.budget)
                self.assertGreater(view.cache.get(0)[2].width(), 0)
                self.assertGreater(view.cache.get(1)[2].width(), 0)
            finally:
                view.release()
                view.close()
                doc.pdf.close()
