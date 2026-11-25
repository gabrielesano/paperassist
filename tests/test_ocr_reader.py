"""End-to-end tests for OCR sidecar integration in the native reader."""
from contextlib import closing
import hashlib
import tempfile
import unittest
from pathlib import Path

from support import QtTestCase, needs_pdf, needs_qt, pdf_available, qt_app

if pdf_available:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QContextMenuEvent, QFont, QGuiApplication, QImage, QPageSize, QPainter, QPdfWriter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QToolButton

    from paperassist import ocr
    from paperassist.native.document import PdfDocument
    from paperassist.native.engine import Pdf, Rect
    from paperassist.native.source import FileSource, LibrarySource
    from paperassist.native.window import MainWindow
    from paperassist.workspace import Workspace

needs_ocr = unittest.skipUnless(
    pdf_available and ocr._probe("eng")["available"],
    "Tesseract with English data and PDFium are required",
)


def make_scanned_pdf(path: Path, lines: list[str]) -> Path:
    """Create an image-only PDF by painting crisp text onto a QImage and writing via QPdfWriter."""
    qt_app()
    image = QImage(1700, 2200, QImage.Format_Grayscale8)
    image.fill(Qt.white)
    painter = QPainter(image)
    painter.setPen(Qt.black)
    font = QFont("Sans Serif")
    font.setPixelSize(52)
    painter.setFont(font)
    y = 240
    for line in lines:
        painter.drawText(140, y, line)
        y += 120
    painter.end()

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.Letter))
    writer.setResolution(200)
    pdf_painter = QPainter(writer)
    pdf_painter.drawImage(0, 0, image)
    pdf_painter.end()
    return path


def _write_synthetic_sidecar(cache_dir: Path, sha256: str, page: int, size: tuple[float, float], words: list[dict]):
    ocr._write_sidecar_page(
        cache_dir,
        sha256,
        {"page": page, "size": list(size), "words": words, "lines": []},
        "5.3.0",
    )


@needs_qt
class OcrSidecarUnitTests(QtTestCase):
    def test_sidecar_words_and_search_fallback_with_cache_invalidation(self):
        scan = make_scanned_pdf(self.dir / "scan.pdf", ["Synthetic Scan"])
        raw = scan.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        cache = self.dir / "ocr_cache"

        with closing(Pdf(raw)) as native_only:
            self.assertEqual(native_only.words(0), [])
            self.assertEqual(native_only.search_page(0, "Synthetic"), [])

        ws = Workspace(self.dir / "lib")
        self.addCleanup(ws.close)
        doc_record = ws.library.import_paths([str(scan)])["documents"][0]
        source = LibrarySource(ws.library, doc_record)
        doc = PdfDocument(source)
        self.addCleanup(doc.close)

        self.assertEqual(doc.words(0), [])
        self.assertEqual(doc.search("Coronal Loops"), [])

        size = doc.sizes[0]
        _write_synthetic_sidecar(
            source.ocr_cache_dir,
            source.sha256,
            0,
            size,
            [
                {"text": "Adiabatic", "bbox": [50.0, 40.0, 130.0, 56.0], "line": 0},
                {"text": "Coronal", "bbox": [140.0, 40.0, 210.0, 56.0], "line": 0},
                {"text": "Loops", "bbox": [50.0, 64.0, 100.0, 80.0], "line": 1},
                {"text": "résumé", "bbox": [110.0, 64.0, 170.0, 80.0], "line": 1},
            ],
        )

        # Cached empty native words clear only after invalidate_text_cache().
        self.assertEqual(doc.words(0), [])
        doc.invalidate_text_cache([0])
        words = doc.words(0)
        self.assertEqual([w[4] for w in words], ["Adiabatic", "Coronal", "Loops", "résumé"])

        # Same-line multi-word phrase unions into one rect; cross-line splits into two rects.
        same_line = doc.search("adiabatic coronal")
        self.assertEqual(len(same_line), 1)
        self.assertEqual(same_line[0][0], 0)
        self.assertEqual(same_line[0][1], Rect(50.0, 40.0, 210.0, 56.0))

        cross_line = doc.search("Coronal Loops")
        self.assertEqual(len(cross_line), 2)
        self.assertEqual(cross_line[0], (0, Rect(140.0, 40.0, 210.0, 56.0)))
        self.assertEqual(cross_line[1], (0, Rect(50.0, 64.0, 100.0, 80.0)))

        # Accent-folded sub-word hit uses proportional character bounds.
        sub_word = doc.search("res")
        self.assertEqual(len(sub_word), 1)
        self.assertAlmostEqual(sub_word[0][1].x0, 110.0, places=3)
        self.assertAlmostEqual(sub_word[0][1].x1, 140.0, places=3)

        # Worker client receives (ocr_cache_dir, sha256) and searches sidecar on open_shm.
        worker_hits = []
        done = []
        worker_doc_id = self.client.open(
            raw,
            ocr_cache_dir=source.ocr_cache_dir,
            sha256=source.sha256,
        )
        self.addCleanup(lambda: self.client.close(worker_doc_id))
        self.client.search(
            worker_doc_id,
            "coronal loops",
            0,
            1,
            lambda hits, finished, error: (worker_hits.extend(hits), finished and done.append(error)),
        )
        self.pump(lambda: bool(done))
        self.assertEqual(done, [""])
        self.assertEqual(worker_hits, cross_line)
        self.assertEqual(sha, source.sha256)
        self.assertFalse(cache.exists())


@needs_ocr
@needs_qt
class OcrReaderEndToEndTests(QtTestCase):
    def setUp(self):
        super().setUp()
        ocr.clear_probe_cache()
        self.workspace = Workspace(self.dir / "workspace")
        self.addCleanup(self.workspace.close)
        self.window = MainWindow(self.client, self.workspace)
        self.window.resize(1200, 800)
        self.window.show()
        self.window.activateWindow()
        self.addCleanup(lambda: self.cleanup_window(self.window))

    def _open_context_menu(self, view, pos):
        view._context_menu.close()
        QApplication.postEvent(
            view.viewport(),
            QContextMenuEvent(QContextMenuEvent.Mouse, pos, view.viewport().mapToGlobal(pos)),
        )
        self.pump(lambda: view._context_menu.isVisible())
        return {action.text(): action for action in view._context_menu.actions() if not action.isSeparator()}

    def test_scanned_pdf_on_demand_ocr_search_copy_highlight_and_comment(self):
        scan_path = make_scanned_pdf(
            self.dir / "coronal-scan.pdf",
            [
                "Adiabatic Heating of Coronal Loops",
                "Observational Evidence from Solar Flares",
                "Magnetic reconnection drives rapid cooling.",
            ],
        )
        with closing(Pdf(scan_path.read_bytes())) as raw_pdf:
            self.assertEqual(raw_pdf.words(0), [])

        imported = self.workspace.library.import_paths([str(scan_path)])["documents"][0]
        doc_id = imported["id"]
        original_sha = imported["sha256"]
        original_path = self.workspace.library.get_path(doc_id, annotated=False)
        original_bytes = original_path.read_bytes()

        tab = self.window.open_library_document(doc_id)
        pane = tab.main
        view = pane.view
        find_bar = pane.find_bar
        self.pump(lambda: view.has_page(0))
        self.assertEqual(self.window._ocr_watchers, {})
        self.assertFalse((self.workspace.data_dir / "cache" / "ocr" / "pages").exists())

        # Context menu exposes on-demand OCR when page has no text.
        center = view.viewport().rect().center()
        menu_actions = self._open_context_menu(view, center)
        self.assertIn("Recognize scanned text (OCR)", menu_actions)
        view._context_menu.close()

        # Ctrl+F on unscanned PDF yields 0 hits and reveals FindBar's OCR scan button.
        QTest.keyClick(view, Qt.Key_F, Qt.ControlModifier)
        self.pump(lambda: find_bar.isVisible())
        self.assertFalse(find_bar.ocr_button.isVisible())

        find_bar.field.setText("Coronal Loops")
        QTest.keyClick(find_bar.field, Qt.Key_Return)
        self.pump(lambda: not view.search_pending)
        self.assertEqual(view.hits, [])
        ocr_btn = pane.findChild(QToolButton, "find-ocr-button")
        self.assertIsNotNone(ocr_btn)
        self.assertTrue(ocr_btn.isVisible())

        # Trigger on-demand OCR and verify duplicate-job guard.
        QTest.mouseClick(ocr_btn, Qt.LeftButton)
        self.assertIn(doc_id, self.window._ocr_watchers)
        first_job_count = len(self.workspace.jobs)
        self.window.start_ocr(doc_id)
        self.assertEqual(len(self.workspace.jobs), first_job_count)

        self.pump(lambda: doc_id not in self.window._ocr_watchers, timeout=60.0)
        self.pump(lambda: not view.search_pending)

        # Original PDF bytes on disk remain untouched and at revision 0.
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(hashlib.sha256(original_path.read_bytes()).hexdigest(), original_sha)
        self.assertEqual(self.workspace.library.get_document(doc_id)["revision"], 0)

        # Active FindBar query re-ran automatically and found the recognized phrase; OCR button hid.
        self.assertGreaterEqual(len(view.hits), 1)
        self.assertFalse(ocr_btn.isVisible())

        # Context menu no longer offers OCR once text exists on the page.
        menu_actions = self._open_context_menu(view, center)
        self.assertNotIn("Recognize scanned text (OCR)", menu_actions)
        view._context_menu.close()

        # Multi-word worker search via FindBar + Shift+Enter + Esc.
        find_bar.field.setText("Solar Flares")
        QTest.keyClick(find_bar.field, Qt.Key_Return)
        self.pump(lambda: not view.search_pending and len(view.hits) >= 1)
        self.assertGreaterEqual(len(view.hits), 1)
        QTest.keyClick(find_bar.field, Qt.Key_Return, Qt.ShiftModifier)
        QTest.keyClick(pane, Qt.Key_Escape)
        self.assertFalse(find_bar.isVisible())
        self.assertEqual(view.hits, [])

        # Select OCR words and test copy_with_source().
        words = view.doc.words(0)
        self.assertTrue(words)
        coronal_idx = next(i for i, w in enumerate(words) if "Coronal" in w[4])
        loops_idx = next(i for i, w in enumerate(words) if "Loops" in w[4])
        first_idx, last_idx = sorted((coronal_idx, loops_idx))
        start_word, end_word = words[first_idx], words[last_idx]
        p_start = view._band(0, Rect(*start_word[:4])).center().toPoint()
        p_end = view._band(0, Rect(*end_word[:4])).center().toPoint()
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, p_start)
        QTest.mouseMove(view.viewport(), p_end)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, p_end)
        self.assertTrue(view.selection_lines)

        QGuiApplication.clipboard().clear()
        view.copy_with_source()
        copied = QGuiApplication.clipboard().text()
        self.assertIn("Coronal", copied)
        self.assertIn("Loops", copied)
        self.assertIn("PDF p. 1", copied)

        # Highlighting selected OCR words creates a standard /Highlight annotation, not freehand ink.
        view.highlight_selection()
        annots = view.doc.annotations(0)
        self.assertEqual(len(annots), 1)
        self.assertEqual(annots[0]["subtype"], "highlight")
        self.assertFalse(annots[0]["freehand"])
        self.assertEqual(original_path.read_bytes(), original_bytes)

        # Phrase-linked comment anchors red dashed outline geometry to OCR words.
        f_idx = next(i for i, w in enumerate(words) if "Solar" in w[4])
        l_idx = next(i for i, w in enumerate(words) if "Flares" in w[4])
        c_first, c_last = sorted((f_idx, l_idx))
        c_start = view._band(0, Rect(*words[c_first][:4])).center().toPoint()
        c_end = view._band(0, Rect(*words[c_last][:4])).center().toPoint()
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, c_start)
        QTest.mouseMove(view.viewport(), c_end)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, c_end)
        self.assertTrue(view.selection_lines)

        self.window.comments.compose()
        self.window.comments.panel.text.setPlainText("Check solar flare data.")
        QTest.mouseClick(self.window.comments.panel.save_button, Qt.LeftButton)
        self.pump(lambda: bool(view._comment_bands.get(0)))
        anchored_note, anchored_rect = view._comment_bands[0][0]
        self.assertIn("Solar", anchored_note["quote"])
        self.assertIn("Flares", anchored_note["quote"])
        self.assertIn(0, view._comment_paths)
        self.assertGreater(anchored_rect.width, 0)
        self.assertGreater(anchored_rect.height, 0)
        self.assertEqual(original_path.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
