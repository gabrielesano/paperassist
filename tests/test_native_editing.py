"""Selecting, highlighting, drawing and editing in the native reader; dirty state and the explicit-Save rule."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from test_native_support import make_pdf, needs_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from paperassist.native.annotation_bar import install
    from paperassist.native.document import PdfDocument, SaveState
    from paperassist.native.engine import Pdf, Rect
    from paperassist.native.renderer import RenderClient
    from paperassist.native.source import FileSource
    from paperassist.native.window import MainWindow


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def two_columns(path):
    """Text lines whose left and right columns share baselines, as PDFium reports one line for both."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Courier')}))
    page = writer.add_blank_page(400, 500)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    content = ''.join(f'BT /F1 11 Tf 30 {430 - 14 * i} Td (left column line {i} words) Tj ET\n'
                      f'BT /F1 11 Tf 220 {430 - 14 * i} Td (right column line {i} words) Tj ET\n' for i in range(3))
    stream = DecodedStreamObject()
    stream.set_data(content.encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


def scan(path):
    """A page without any text, like a scanned paper."""
    writer = PdfWriter()
    writer.add_blank_page(400, 500)
    writer.write(path)
    return path


@needs_pdf
class DocumentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = make_pdf(self.dir / 'a.pdf')
        self.doc = PdfDocument(FileSource(self.path))
        self.addCleanup(self.doc.close)
        self.original = digest(self.path)

    def test_a_column_gutter_splits_the_highlight_into_separate_bands(self):
        doc = PdfDocument(FileSource(two_columns(self.dir / 'columns.pdf')))
        self.addCleanup(doc.close)
        words = doc.words(0)
        lines = doc.selection_lines((0, 0), (0, len(words) - 1))
        self.assertEqual([text for _, _, text in lines[:2]], ['left column line 0 words', 'right column line 0 words'])
        self.assertEqual(len(lines), 6)
        left, right = lines[0][1], lines[1][1]
        self.assertLess(left.x1, 200)
        self.assertGreater(right.x0, 200)

    def test_changing_a_color_back_is_not_a_change_and_undo_redo_follow_the_saved_state(self):
        self.doc.add_highlight(0, [Rect(*self.doc.words(0)[0][:4])])
        self.assertTrue(self.doc.save())
        yellow = self.doc.annotations(0)[0]['color']
        self.doc.update_annotation(0, 1, color=(0, 0, 1))
        self.assertIs(self.doc.state, SaveState.UNSAVED)
        self.doc.update_annotation(0, 1, color=yellow)
        self.assertIs(self.doc.state, SaveState.SAVED)  # same serialized annotations as on disk
        self.doc.update_annotation(0, 1, color=(0, 0, 1))
        self.doc.undo()
        self.assertFalse(self.doc.modified)
        self.doc.redo()
        self.assertTrue(self.doc.modified)

    def test_drawing_and_deleting_the_same_stroke_leaves_nothing_to_save(self):
        self.doc.add_ink(0, [[(50, 50), (90, 80), (130, 50)]])
        self.assertTrue(self.doc.modified)
        self.doc.delete_annotation(0, self.doc.annotations(0)[0]['index'])
        self.assertEqual(self.doc.annotations(0), [])
        self.assertFalse(self.doc.modified)

    def test_undoing_an_edit_made_before_the_save_makes_the_document_unsaved(self):
        self.doc.add_ink(0, [[(50, 50), (90, 80)]])
        self.assertTrue(self.doc.save())
        self.doc.undo()
        self.assertIs(self.doc.state, SaveState.UNSAVED)
        self.doc.redo()
        self.assertIs(self.doc.state, SaveState.SAVED)

    def test_nothing_is_written_until_save_for_every_kind_of_edit(self):
        self.doc.add_highlights({0: [Rect(*self.doc.words(0)[0][:4])]}, (1, 1, 0))
        self.doc.add_ink(0, [[(50, 50), (90, 80)]], (0, 0, 0), 4)
        self.doc.add_ink(0, [[(60, 60), (100, 90)]], (1, 1, 0), 12, True)
        index = len(self.doc.annotations(0))
        self.doc.update_annotation(0, index, color=(0, 1, 0), width=8)
        self.doc.delete_annotation(0, index)
        self.doc.undo()
        self.doc.redo()
        self.assertEqual(digest(self.path), self.original)
        self.assertTrue(self.doc.save())
        self.assertNotEqual(digest(self.path), self.original)

    def test_freehand_highlights_are_multiplied_ink_that_can_be_recolored_and_resized(self):
        self.doc.add_ink(0, [[(50, 50), (90, 80), (130, 50)]], (0.96, 0.84, 0.43), 12, True)
        item = self.doc.annotations(0)[0]
        self.assertTrue(item['freehand'])
        self.assertEqual(item['width'], 12)
        self.doc.update_annotation(0, 1, color=(0, 0, 1), width=6)
        written = PdfReader(io.BytesIO(self.doc.pdf.data)).pages[0]['/Annots'][1].get_object()
        self.assertEqual(written['/IT'], '/InkHighlight')
        self.assertEqual(written['/AP']['/N']['/Resources']['/ExtGState']['/GS']['/BM'], '/Multiply')

    def test_transform_saved_pdfjs_ink_preserves_appearance_and_undo(self):
        fixture = Path(__file__).parent / 'fixtures' / 'pdfjs-annotations.pdf'
        path = self.dir / 'fixture.pdf'
        original = fixture.read_bytes()
        path.write_bytes(original)
        doc = PdfDocument(FileSource(path))
        self.addCleanup(doc.close)
        for item in [a for a in doc.annotations(0) if a['subtype'] == 'ink']:
            before = PdfReader(io.BytesIO(doc.pdf.data)).pages[0]['/Annots'][item['index']].get_object()
            appearance = before['/AP']['/N'].get_object().get_data()
            old = item['rect']
            target = Rect(old.x0 + 20, old.y0 + 30, old.x0 + 20 + old.width * 1.5, old.y0 + 30 + old.height * 1.5)
            self.assertTrue(doc.transform_annotation(0, item['index'], target))
            moved = next(a for a in doc.annotations(0) if a['index'] == item['index'])
            for actual, expected in zip(moved['rect'], target):
                self.assertAlmostEqual(actual, expected, places=3)
            self.assertAlmostEqual(moved['width'], item['width'] * 1.5, places=3)
            after = PdfReader(io.BytesIO(doc.pdf.data)).pages[0]['/Annots'][item['index']].get_object()
            self.assertEqual(after['/AP']['/N'].get_object().get_data(), appearance)
            self.assertEqual(after.get('/IT'), before.get('/IT'))
            doc.undo()
            self.assertFalse(doc.modified)
            doc.redo()
            self.assertTrue(doc.modified)
            self.assertTrue(doc.save())
        reopened = PdfDocument(FileSource(path))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.annotations(0), doc.annotations(0))
        self.assertEqual(fixture.read_bytes(), original)

    def test_transformed_appearance_renders_at_new_location(self):
        import pypdfium2 as pdfium
        path = scan(self.dir / 'pixels.pdf')
        doc = PdfDocument(FileSource(path))
        self.addCleanup(doc.close)
        doc.add_ink(0, [[(50, 60), (100, 90)]], width=4)
        item = doc.annotations(0)[0]
        old = item['rect']
        doc.transform_annotation(0, item['index'], Rect(old.x0 + 120, old.y0 + 100, old.x1 + 120, old.y1 + 100))
        pdf = pdfium.PdfDocument(doc.pdf.data)
        self.addCleanup(pdf.close)
        bitmap = pdf[0].render(scale=1, rev_byteorder=True)
        pixels = memoryview(bitmap.buffer).cast("B")
        def pixel(x, y):
            offset = y * bitmap.stride + x * bitmap.n_channels
            return min(pixels[offset:offset + 3])
        self.assertLess(pixel(195, 175), 128)
        self.assertGreater(pixel(75, 75), 240)

    def test_transform_on_rotated_cropped_page_uses_display_coordinates(self):
        writer = PdfWriter()
        page = writer.add_blank_page(400, 500)
        page.cropbox.lower_left = (30, 50)
        page.cropbox.upper_right = (370, 450)
        page.rotate(90)
        path = self.dir / 'rotated.pdf'
        writer.write(path)
        doc = PdfDocument(FileSource(path))
        self.addCleanup(doc.close)
        doc.add_ink(0, [[(40, 60), (100, 90)]])
        item = doc.annotations(0)[0]
        old = item['rect']
        target = Rect(old.x0 + 10, old.y0 + 20, old.x1 + 10, old.y1 + 20)
        doc.transform_annotation(0, item['index'], target)
        for actual, expected in zip(doc.annotations(0)[0]['strokes'][0], [(50, 80), (110, 110)]):
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=3)
        self.assertTrue(doc.save())
        self.assertEqual(doc.annotations(0)[0]['rect'], target)

    def test_hidden_highlights_cannot_be_picked_but_ink_still_can(self):
        word = Rect(*self.doc.words(0)[0][:4])
        self.doc.add_highlight(0, [word])
        self.doc.add_ink(0, [[(300, 300), (350, 340)]])
        x, y = (word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2
        self.assertEqual(self.doc.annotation_at(0, x, y)['subtype'], 'highlight')
        self.assertIsNone(self.doc.annotation_at(0, x, y, highlights=False))
        self.assertEqual(self.doc.annotation_at(0, 325, 320, highlights=False)['subtype'], 'ink')


@needs_qt
class WindowEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()
        cls._files = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._files.name)

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()
        cls._files.cleanup()

    def setUp(self):
        config = patch("paperassist.paths.config_file", return_value=self.dir / f"{self.id()}-config.json")
        config.start()
        self.addCleanup(config.stop)
        self.window = MainWindow(self.client)
        self.window.resize(1200, 800)
        self.window.show()
        self.window.activateWindow()
        self.bar = install(self.window)
        self.addCleanup(self.close_window)

    def close_window(self):
        for pane in self.window.all_panes():
            pane.release()
        self.window.hide()
        self.window.deleteLater()

    def pump(self, until=None, seconds=0.2):
        import time
        end = time.perf_counter() + (10 if until else seconds)
        while time.perf_counter() < end and not (until and until()):
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(until() if until else True, "timed out")

    def open(self, name, maker=make_pdf):
        path = maker(self.dir / f"{self.id()}-{name}.pdf")
        tab = self.window.open_source(FileSource(path))
        self.pump(lambda: tab.main.view.has_page(0))
        return tab.main.view

    def point(self, view, page, x, y):
        return view._band(page, Rect(x, y, x, y)).topLeft().toPoint()

    def word_point(self, view, page, index, end=False):
        w = view.doc.words(page)[index]
        return self.point(view, page, w[2] - 1 if end else w[0] + 1, (w[1] + w[3]) / 2)

    def drag(self, view, points):
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, points[0])
        for point in points[1:]:
            QTest.mouseMove(view.viewport(), point)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, points[-1])

    def select_words(self, view, first=3, last=20):
        self.drag(view, [self.word_point(view, 0, first), self.word_point(view, 0, last, True)])

    # ---- selection and copy ---------------------------------------------------------------
    def test_ctrl_a_selects_all_text_and_shift_click_extends_a_selection(self):
        view = self.open("select")
        self.select_words(view, 3, 10)
        before = view.selection_lines[-1]
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ShiftModifier, self.word_point(view, 0, 60, True))
        self.assertGreater(len(view.selection_lines), 1)
        self.assertGreater(view.selection_lines[-1][1].y0, before[1].y0)
        self.assertEqual(view.selection_lines[0][1].x0, view.doc.words(0)[3][0])  # the anchor stays
        QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual({page for page, _, _ in view.selection_lines}, set(range(len(view.doc))))
        QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(QGuiApplication.clipboard().text(), view.selected_text())
        self.assertFalse(view.doc.modified)

    # ---- freehand highlights, strokes and the Save rule --------------------------------------
    def test_highlighting_a_page_without_text_paints_a_freehand_highlight(self):
        view = self.open("scan", scan)
        original = digest(view.doc.source.path)
        self.window.highlight_action.setChecked(True)
        self.assertEqual(self.bar.thickness.value(), 12)  # the toolbar default for the Highlight tool
        self.drag(view, [self.point(view, 0, 50, 100), self.point(view, 0, 150, 120), self.point(view, 0, 250, 100)])
        item = view.doc.annotations(0)[0]
        self.assertEqual((item['freehand'], item['width']), (True, 12))
        self.assertIs(view.doc.state, SaveState.UNSAVED)
        self.assertEqual(self.bar.label.text(), '')
        self.assertEqual(digest(view.doc.source.path), original)
        self.window.save()
        written = PdfReader(view.doc.source.path).pages[0]['/Annots'][0].get_object()
        self.assertEqual(written['/IT'], '/InkHighlight')

    def test_a_stroke_under_the_mouse_is_committed_before_a_shortcut_runs(self):
        view = self.open("stroke")
        self.bar.draw.trigger()
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 50, 300))
        QTest.mouseMove(view.viewport(), self.point(view, 0, 120, 340))
        QTest.mouseMove(view.viewport(), self.point(view, 0, 200, 300))
        self.assertFalse(view.doc.modified)  # still drawing
        QTest.keyClick(view, Qt.Key_S, Qt.ControlModifier)
        self.assertIs(view.doc.state, SaveState.SAVED)
        self.assertEqual(len(PdfReader(view.doc.source.path).pages[0]['/Annots']) - 1, 1)  # plus the link
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 200, 300))
        self.assertEqual(len(view.doc.annotations(0)), 1)  # the release adds nothing

    def test_selected_ink_moves_resizes_and_respects_read_only(self):
        view = self.open('transform')
        view.doc.add_ink(0, [[(80, 300), (150, 340), (200, 300)]])
        view._annotation_changed([0])
        item = view.doc.annotations(0)[0]
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 150, 340))
        self.assertEqual(view.selected_annotation, (0, item['index']))
        original = item['rect']
        self.assertTrue(view.save())
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 150, 320))
        self.assertFalse(view.doc.modified)
        self.drag(view, [self.point(view, 0, 150, 320), self.point(view, 0, 180, 340)])
        moved = view.doc.annotations(0)[0]['rect']
        self.assertAlmostEqual(moved.x0 - original.x0, 30, delta=2)
        self.assertAlmostEqual(moved.y0 - original.y0, 20, delta=2)
        view.undo()
        self.assertFalse(view.doc.modified)
        view.redo()
        view.select_annotation((0, item['index']))
        box = view._band(0, moved).adjusted(-2, -2, 2, 2)
        self.drag(view, [box.bottomRight().toPoint(), (box.bottomRight() + QPoint(50, 30)).toPoint()])
        resized = view.doc.annotations(0)[0]['rect']
        self.assertGreater(resized.width, moved.width)
        self.assertAlmostEqual(resized.width / moved.width, resized.height / moved.height, places=3)
        view.undo()
        self.assertAlmostEqual(view.doc.annotations(0)[0]['rect'].width, moved.width, places=3)
        view.redo()
        view.select_annotation((0, item['index']))
        box = view._band(0, view.doc.annotations(0)[0]['rect'])
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, box.center().toPoint())
        QTest.mouseMove(view.viewport(), (box.center() + QPoint(20, 20)).toPoint())
        before = view.doc.pdf.data
        QTest.keyClick(view, Qt.Key_Escape)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, (box.center() + QPoint(20, 20)).toPoint())
        self.assertEqual(view.doc.pdf.data, before)
        box = view._band(0, view.doc.annotations(0)[0]['rect'])
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, box.center().toPoint())
        view.locked = True
        QTest.mouseMove(view.viewport(), (box.center() + QPoint(20, 20)).toPoint())
        before = view.doc.pdf.data
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, (box.center() + QPoint(20, 20)).toPoint())
        self.assertEqual(view.doc.pdf.data, before)
        view.locked = False
        view.doc.state = SaveState.SAVING
        before = view.doc.pdf.data
        self.drag(view, [self.point(view, 0, 180, 340), self.point(view, 0, 200, 350)])
        self.assertEqual(view.doc.pdf.data, before)
        view.doc.state = SaveState.UNSAVED
        self.assertTrue(view.save())
        self.assertEqual(view.doc.annotations(0)[0]['rect'], resized)

    def test_no_edit_is_possible_while_the_file_is_being_written(self):
        view = self.open("saving")
        self.bar.draw.trigger()
        self.drag(view, [self.point(view, 0, 50, 300), self.point(view, 0, 120, 340)])
        view.select_annotation((0, 1))
        seen = []
        source = view.doc.source
        write = source.save

        def save(pdf):
            data = pdf.data
            view.undo()
            view.delete_annotation()
            view.set_annotation_color((1, 0, 0))
            self.drag(view, [self.point(view, 0, 50, 400), self.point(view, 0, 120, 440)])
            seen.append((pdf.data == data, view.read_only, self.bar.draw.isEnabled(), self.bar.undo.isEnabled()))
            write(pdf)
        with patch.object(source, "save", save):
            self.assertTrue(view.save())
        self.assertEqual(seen, [(True, True, False, False)])
        self.assertEqual(len(view.doc.annotations(0)), 1)

    def test_a_failed_save_keeps_every_edit_and_the_next_save_retries_it(self):
        view = self.open("failing")
        self.bar.draw.trigger()
        self.drag(view, [self.point(view, 0, 50, 300), self.point(view, 0, 120, 340)])
        original = digest(view.doc.source.path)
        with patch.object(view.doc.source, "save", side_effect=RuntimeError("This paper was saved again since you opened it")):
            self.assertFalse(view.save())
        self.assertIs(view.doc.state, SaveState.FAILED)
        self.assertTrue(view.doc.modified)
        self.assertEqual(len(view.doc.annotations(0)), 1)
        self.assertEqual(digest(view.doc.source.path), original)
        self.assertTrue(view.save())
        self.assertIs(view.doc.state, SaveState.SAVED)

    # ---- hiding highlights ------------------------------------------------------------------
    def highlight(self, view):
        self.window.highlight_action.setChecked(True)
        self.select_words(view)
        self.pump(lambda: view.has_page(0))

    def test_hidden_highlights_cannot_be_selected_and_a_new_one_shows_them_again(self):
        view = self.open("hidden")
        self.highlight(view)
        self.bar.select.trigger()
        view.select_annotation((0, 1))
        self.assertEqual(self.bar.label.text(), 'Selected highlight')
        view.set_highlights_visible(False)
        self.assertIsNone(view.selected_annotation)
        centre = self.word_point(view, 0, 10)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, centre)
        self.assertIsNone(view.selected_annotation)
        self.assertFalse(self.bar.visibility.isChecked())
        self.window.highlight_action.setChecked(True)
        self.select_words(view, 30, 40)
        self.assertTrue(view.highlights_visible)  # a highlight you just made must not be invisible
        self.assertTrue(self.bar.visibility.isChecked())
        self.assertEqual(len(view.doc.annotations(0)), 2)

    def test_edits_while_highlights_are_hidden_keep_them_hidden_in_the_render_copy(self):
        from paperassist.native.annotations import read
        view = self.open("hidden-edit")
        self.highlight(view)
        view.set_highlights_visible(False)
        self.bar.draw.trigger()
        self.drag(view, [self.point(view, 0, 50, 300), self.point(view, 0, 120, 340)])
        kinds = [a['subtype'] for a in read(view.doc.pdf.render_data(False), 0)]
        self.assertEqual(kinds, ['ink'])
        self.assertEqual([a['subtype'] for a in view.doc.annotations(0)], ['highlight', 'ink'])  # the document keeps both
        self.assertFalse(view.highlights_visible)

    # ---- the whole Save rule ------------------------------------------------------------------
    def test_only_save_writes_the_pdf_whatever_the_reader_does(self):
        view = self.open("never")
        path = view.doc.source.path
        original = digest(path)
        press = lambda key, mod=Qt.NoModifier: QTest.keyClick(view, key, mod)
        self.highlight(view)
        self.bar.draw.trigger()
        self.drag(view, [self.point(view, 0, 50, 300), self.point(view, 0, 120, 340), self.point(view, 0, 200, 300)])
        self.bar.select.trigger()
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 120, 340))
        self.assertEqual(view.selected_annotation[0], 0)
        self.bar.colors.activated.emit(2)
        self.bar.thickness.setValue(9)
        press(Qt.Key_Delete)
        press(Qt.Key_Z, Qt.ControlModifier)
        press(Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
        press(Qt.Key_Z, Qt.ControlModifier)
        press(Qt.Key_Y, Qt.ControlModifier)
        self.bar.visibility.trigger()
        self.bar.visibility.trigger()
        for action in (self.bar.draw, self.bar.select, self.window.highlight_action):
            action.trigger()
        self.pump(seconds=0.3)
        self.assertEqual(digest(path), original)
        self.assertTrue(view.doc.modified)
        self.window.save()
        self.assertNotEqual(digest(path), original)

    def test_switching_tools_alone_is_clean_and_undo_back_to_the_start_is_clean(self):
        view = self.open("tools")
        for action in (self.bar.draw, self.bar.select, self.window.highlight_action, self.bar.select):
            action.trigger()
        self.assertFalse(view.doc.modified)
        self.highlight(view)
        self.assertTrue(view.doc.modified)
        QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
        self.assertFalse(view.doc.modified)
        QTest.keyClick(view, Qt.Key_Y, Qt.ControlModifier)
        self.assertTrue(view.doc.modified)

    def test_the_toolbars_offer_no_tool_beyond_select_draw_highlight_and_comment(self):
        view = self.open("tools-only")
        names = {a.text() for a in self.bar.actions() if a.text()}
        self.assertEqual(names, {'Select', 'Draw', 'Comment', 'Delete', 'Undo', 'Redo', 'Show highlights'})
        self.assertEqual(self.window.highlight_action.text(), 'Highlight')
        self.assertIsNotNone(view)

    def test_controls_follow_the_selection_and_go_quiet_when_read_only(self):
        view = self.open("controls")
        self.assertFalse(self.bar.colors.isEnabled() or self.bar.remove.isEnabled())
        self.assertFalse(self.bar.width_action.isVisible())
        self.bar.draw.trigger()
        self.assertTrue(self.bar.colors.isEnabled() and self.bar.width_action.isVisible())
        self.assertEqual(self.bar.thickness.value(), 2)
        view.locked = True  # another view of the paper holds unsaved edits
        self.bar.refresh()
        self.assertFalse(any(w.isEnabled() for w in (self.bar.select, self.bar.draw, self.bar.colors, self.bar.thickness, self.bar.remove)))
