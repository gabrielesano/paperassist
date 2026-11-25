"""Selecting, highlighting, drawing and editing in the native reader; dirty state and the explicit-Save rule."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NullObject, NumberObject, RectangleObject)

from support import QtTestCase, make_pdf, needs_pdf, needs_qt, pdf_available, pdf_bytes

if pdf_available:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QContextMenuEvent, QGuiApplication
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QToolButton

    from paperassist.native.document import PdfDocument, SaveState
    from paperassist.native.engine import Pdf, Rect
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


def two_columns_with_line_numbers(path, right_side="gutter", step=1):
    """Two-column proof with a full-width title and line numbers in outer margins or central gutter."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Courier')}))
    page = writer.add_blank_page(600, 500)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    lines = ['BT /F1 12 Tf 60 455 Td (Full Width Title Across Both Columns) Tj ET']
    for i in range(6):
        y = 420 - 16 * i
        n1 = (i + 1) * step
        lines.append(f'BT /F1 9 Tf 30 {y} Td ({n1}) Tj ET')
        lines.append(f'BT /F1 10 Tf 60 {y} Td (left col line {i} text) Tj ET')
    for i in range(6):
        y = 420 - 16 * i
        n2 = (i + 7) * step
        if right_side == "gutter":
            lines.append(f'BT /F1 9 Tf 295 {y} Td ({n2}) Tj ET')
            lines.append(f'BT /F1 10 Tf 320 {y} Td (right col line {i} text) Tj ET')
        else:
            lines.append(f'BT /F1 10 Tf 320 {y} Td (right col line {i} text) Tj ET')
            lines.append(f'BT /F1 9 Tf 565 {y} Td ({n2}) Tj ET')
    stream = DecodedStreamObject()
    stream.set_data('\n'.join(lines).encode('ascii'))
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

    def test_multiline_selection_excludes_margin_and_gutter_line_numbers(self):
        for side, step in (("gutter", 1), ("gutter", 5), ("right", 5)):
            with self.subTest(side=side, step=step):
                doc = PdfDocument(FileSource(two_columns_with_line_numbers(
                    self.dir / f'lineno-{side}-{step}.pdf', right_side=side, step=step)))
                self.addCleanup(doc.close)
                # Drag across multiple lines in the left column and in the right column (even starting near the number).
                left_start = doc.word_at(0, 32, 80)  # pointer in left margin near line number 1
                left_end = doc.word_at(0, 210, 112)
                left_lines = doc.selection_lines((0, left_start), (0, left_end))
                self.assertEqual([text for _, _, text in left_lines],
                                 ['left col line 0 text', 'left col line 1 text', 'left col line 2 text'])
                self.assertTrue(all(rect.x0 >= 58 for _, rect, _ in left_lines))

                right_start = doc.word_at(0, 297, 80)  # pointer in gutter near right-column line number
                right_end = doc.word_at(0, 566, 112)   # pointer in right margin
                right_lines = doc.selection_lines((0, right_start), (0, right_end))
                self.assertEqual([text for _, _, text in right_lines],
                                 ['right col line 0 text', 'right col line 1 text', 'right col line 2 text'])
                self.assertTrue(all(rect.x0 >= 318 and rect.x1 <= 550 for _, rect, _ in right_lines))

    def test_table_row_numbers_and_inline_digits_stay_selectable(self):
        writer = PdfWriter()
        font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Courier')}))
        page = writer.add_blank_page(500, 500)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        lines = ['BT /F1 11 Tf 60 440 Td (Body paragraph starting at the left margin.) Tj ET']
        for i in range(5):
            y = 400 - 16 * i
            lines.append(f'BT /F1 11 Tf 60 {y} Td ({i + 1}) Tj ET')
            lines.append(f'BT /F1 11 Tf 110 {y} Td (row item with inline 42 value) Tj ET')
        stream = DecodedStreamObject()
        stream.set_data('\n'.join(lines).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
        path = self.dir / 'table.pdf'
        writer.write(path)
        doc = PdfDocument(FileSource(path))
        self.addCleanup(doc.close)
        words = doc.words(0)
        selected = "\n".join(text for _, _, text in doc.selection_lines((0, 0), (0, len(words) - 1)))
        self.assertIn("1", selected)
        self.assertIn("5", selected)
        self.assertIn("42", selected)

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

    def test_pdf_render_filters_highlights_and_ink_highlights_while_keeping_ink(self):
        from paperassist.native import annotations
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        data = annotations.apply(pdf_bytes(writer), 'highlight', 0, rects=[(20, 20, 80, 40)], color=(1, 1, 0), opacity=1)
        data = annotations.apply(data, 'ink', 0, strokes=[[(20, 80), (80, 100)]], color=(0, 1, 0), width=10, freehand=True)
        data = annotations.apply(data, 'ink', 0, strokes=[[(20, 140), (80, 160)]], color=(0, 0, 0), width=6, freehand=False)
        pdf = Pdf(data)
        self.addCleanup(pdf.close)

        def pixel(rendered, x, y):
            mv = memoryview(rendered.rgb).cast("B")
            off = ((rendered.height - y) * rendered.width + x) * 3
            return tuple(mv[off:off + 3])

        visible = pdf.render(0, 1.0, highlights=True)
        self.assertNotEqual(pixel(visible, 50, 30), (255, 255, 255))
        self.assertNotEqual(pixel(visible, 50, 90), (255, 255, 255))
        self.assertEqual(pixel(visible, 50, 150), (0, 0, 0))

        hidden = pdf.render(0, 1.0, highlights=False)
        self.assertEqual(pixel(hidden, 50, 30), (255, 255, 255))
        self.assertEqual(pixel(hidden, 50, 90), (255, 255, 255))
        self.assertEqual(pixel(hidden, 50, 150), (0, 0, 0))

        # Flag restoration ensures subsequent visible renders keep all highlights.
        self.assertEqual(bytes(pdf.render(0, 1.0, highlights=True).rgb), bytes(visible.rgb))


@needs_qt
class WindowEditingTests(QtTestCase):
    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.client)
        self.window.resize(1200, 800)
        self.window.show()
        self.window.activateWindow()
        self.bar = self.window.annotation_bar
        self.addCleanup(lambda: self.cleanup_window(self.window))

    def open(self, name, maker=make_pdf):
        path = maker(self.dir / f"{self.id()}-{name}.pdf")
        tab = self.window.open_source(FileSource(path))
        self.pump(lambda: tab.main.view.has_page(0))
        return tab.main.view

    def test_multipage_selection_writes_and_reopens_once_with_one_undo_step(self):
        from paperassist.native import annotations

        view = self.open('multipage', lambda path: make_pdf(path, pages=4, rotate=90))
        doc = view.doc
        original = doc.pdf.data
        expected = [view.doc.links(page) for page in range(len(doc))]
        view.select_all()
        with patch.object(annotations, 'incremental_writer', wraps=annotations.incremental_writer) as writer, \
             patch.object(doc.pdf, '_replace', wraps=doc.pdf._replace) as reopen:
            view.highlight_selection()
        writer.assert_called_once_with(original)
        reopen.assert_called_once()
        pages = list(range(4))
        self.assertTrue(all(len(doc.annotations(page)) == 1 for page in pages))
        self.assertTrue(doc.pdf.data.startswith(original))
        self.assertEqual(doc.undo(), pages)
        self.assertEqual(doc.undo(), [])
        self.assertFalse(doc.modified)
        self.assertTrue(all(not doc.annotations(page) for page in pages))
        self.assertEqual(doc.redo(), pages)
        self.assertTrue(all(len(doc.annotations(page)) == 1 for page in pages))
        self.assertEqual([doc.pdf.links(page) for page in pages], expected)
        self.assertEqual(doc.source.path.read_bytes(), original)

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
    def test_select_all_only_builds_visible_bands_and_streams_copy_without_caching_words(self):
        view = self.open("lazy-select", lambda path: make_pdf(path, pages=24))
        cached_before = set(view.doc._words)
        with patch.object(view.doc, "page_selection", wraps=view.doc.page_selection) as lines:
            view.select_all()
            lines.assert_not_called()
            self.pump()
            self.assertEqual({call.args[0] for call in lines.call_args_list}, set(view.visible_pages()))
            view.goto(12)
            self.pump()
            self.assertEqual(set(view._visible_selection), set(view.visible_pages()))
            view.copy_with_source()
        text = QGuiApplication.clipboard().text()
        self.assertIn("Page 24.", text)
        self.assertTrue(text.endswith(f"{view.doc.source.path.name}, PDF pp. 1–24"))
        self.assertEqual(set(view.doc._words), cached_before)

    def test_ctrl_a_selects_all_text_and_shift_click_extends_a_selection(self):
        view = self.open("select")
        self.select_words(view, 3, 10)
        before = view.selection_lines[-1]
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ShiftModifier, self.word_point(view, 0, 60, True))
        self.assertGreater(len(view.selection_lines), 1)
        self.assertGreater(view.selection_lines[-1][1].y0, before[1].y0)
        self.assertEqual(view.selection_lines[0][1].x0, view.doc.words(0)[3][0])  # the anchor stays
        QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual({page for page, _, _ in view.selected_lines()}, set(range(len(view.doc))))
        QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(QGuiApplication.clipboard().text(), view.selected_text())
        self.assertFalse(view.doc.modified)

    # ---- freehand highlights, strokes and the Save rule --------------------------------------
    def test_foreign_indirect_ink_can_be_recolored_resized_and_saved_in_the_window(self):
        original = _foreign_ink(True, (True, False), 'indirect-null', True)
        path = self.dir / f'{self.id()}-foreign.pdf'
        path.write_bytes(original)
        tab = self.window.open_source(FileSource(path))
        view = tab.main.view
        self.pump(lambda: view.has_page(0))
        strokes = view.doc.annotations(0)[0]['strokes']
        view.select_annotation((0, 0))
        view.set_annotation_color((0, 0, 1))
        view.set_annotation_width(6)
        self.pump(lambda: view.has_page(0))
        item = view.doc.annotations(0)[0]
        self.assertEqual(item['strokes'], strokes)
        self.assertEqual((item['color'], item['width']), ((0, 0, 1), 6))
        self.assertEqual(path.read_bytes(), original)
        self.window.save()
        self.assertFalse(tab.main.modified)
        self.assertTrue(path.read_bytes().startswith(original))
        self.assertGreater(len(path.read_bytes()), len(original))

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

    def test_escape_cancels_active_stroke_and_bare_modifiers_do_not_split_it(self):
        view = self.open("stroke-modifiers")
        self.bar.draw.trigger()
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 50, 300))
        QTest.mouseMove(view.viewport(), self.point(view, 0, 120, 340))
        QTest.keyPress(view, Qt.Key_Shift)
        QTest.keyPress(view, Qt.Key_Control)
        self.assertEqual(len(view._stroke), 2)
        self.assertFalse(view.doc.modified)
        QTest.mouseMove(view.viewport(), self.point(view, 0, 200, 300))
        QTest.keyClick(view, Qt.Key_Escape)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 200, 300))
        self.assertEqual(view.doc.annotations(0), [])
        self.assertIs(view.doc.state, SaveState.SAVED)

        # Pressing Shift mid-stroke then releasing normally keeps one continuous stroke.
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 50, 300))
        QTest.mouseMove(view.viewport(), self.point(view, 0, 120, 340))
        QTest.keyPress(view, Qt.Key_Shift)
        QTest.mouseMove(view.viewport(), self.point(view, 0, 200, 300))
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(view, 0, 200, 300))
        self.assertEqual(len(view.doc.annotations(0)), 1)
        self.assertEqual(len(view.doc.annotations(0)[0]['strokes'][0]), 3)

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
    def test_highlight_visibility_is_reachable_in_clean_select_mode(self):
        view = self.open('visibility-control')
        self.highlight(view)
        path = view.doc.source.path
        self.window.save()
        self.assertTrue(self.window.close_tab(0))
        view = self.window.open_source(FileSource(path)).main.view
        self.pump(lambda: view.has_page(0))
        self.window.resize(800, 800)
        self.pump()
        self.assertFalse(self.bar.isVisible())
        self.assertIn(self.bar.visibility, self.window.main_bar.actions())
        self.assertTrue(self.bar.visibility.isEnabled())
        extension = self.window.main_bar.findChild(QToolButton, 'qt_toolbar_ext_button')
        QTest.mouseClick(extension, Qt.LeftButton)
        menu = self.app.activePopupWidget()
        self.assertIn(self.bar.visibility, menu.actions())
        QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(self.bar.visibility).center())
        self.assertFalse(view.highlights_visible)
        self.assertFalse(view.doc.modified)
        self.bar.visibility.trigger()
        self.assertTrue(view.highlights_visible)
        self.assertFalse(view.doc.modified)

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
        from paperassist.native import annotations
        view = self.open("hidden-edit")
        self.highlight(view)
        with patch.object(annotations, "without_highlights", wraps=annotations.without_highlights) as stripped, \
             patch.object(view.client, "open", wraps=view.client.open) as reopened:
            view.set_highlights_visible(False)
            self.pump(lambda: view.has_page(0))
            reopened.assert_not_called()
            self.bar.draw.trigger()
            self.drag(view, [self.point(view, 0, 50, 300), self.point(view, 0, 120, 340)])
            self.pump(lambda: view.has_page(0))
            stripped.assert_not_called()
        self.assertEqual([a['subtype'] for a in view.doc.annotations(0)], ['highlight', 'ink'])  # the document keeps both
        self.assertFalse(view.highlights_visible)
        hidden = view.doc.pdf.render(0, 1.0, highlights=False)
        shown = view.doc.pdf.render(0, 1.0, highlights=True)
        self.assertNotEqual(bytes(hidden.rgb), bytes(shown.rgb))

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

    def test_the_toolbars_offer_no_tool_beyond_select_pan_draw_highlight_and_comment(self):
        view = self.open("tools-only")
        strip_names = {a.text() for a in self.bar.actions() if a.text()}
        self.assertEqual(strip_names, {'Delete', 'Undo', 'Redo'})
        checkable_tools = {a.text() for a in self.window.main_bar.actions() if a.isCheckable() and a.text() not in ('Panels', 'Show highlights')}
        self.assertEqual(checkable_tools, {'Select', 'Pan', 'Draw', 'Highlight', 'Comment'})
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

    def _open_context_menu(self, view, pos):
        view._context_menu.close()
        QApplication.postEvent(view.viewport(), QContextMenuEvent(
            QContextMenuEvent.Mouse, pos, view.viewport().mapToGlobal(pos)))
        self.pump(lambda: view._context_menu.isVisible())
        return {action.text(): action for action in view._context_menu.actions() if not action.isSeparator()}

    def _click_menu_action(self, view, action):
        QTest.mouseClick(view._context_menu, Qt.LeftButton, pos=view._context_menu.actionGeometry(action).center())
        self.pump(lambda: not view._context_menu.isVisible())

    def test_context_menu_copies_highlights_and_deletes_annotation_without_saving_until_save(self):
        view = self.open("context-menu")
        path = view.doc.source.path
        original = digest(path)

        # Without a selection on a loose file, Copy/Highlight/Comment are disabled and Select all works.
        actions = self._open_context_menu(view, self.word_point(view, 0, 0))
        self.assertFalse(actions["Copy"].isEnabled())
        self.assertFalse(actions["Highlight"].isEnabled())
        self.assertFalse(actions["Comment"].isEnabled())
        self.assertTrue(actions["Select all"].isEnabled())
        view._context_menu.close()

        # Drag-selecting words preserves the selection on right-click and enables Copy and Highlight.
        self.select_words(view, 0, 3)
        expected_text = view.selected_text()
        self.assertTrue(expected_text)
        QGuiApplication.clipboard().clear()
        actions = self._open_context_menu(view, self.word_point(view, 0, 1))
        self.assertTrue(actions["Copy"].isEnabled())
        self.assertTrue(actions["Highlight"].isEnabled())
        self._click_menu_action(view, actions["Copy"])
        self.assertEqual(QGuiApplication.clipboard().text(), expected_text)

        actions = self._open_context_menu(view, self.word_point(view, 0, 1))
        self._click_menu_action(view, actions["Highlight"])
        self.assertEqual(len(view.doc.annotations(0)), 1)
        self.assertEqual(view.doc.annotations(0)[0]["subtype"], "highlight")
        self.assertEqual(digest(path), original)

        # Right-clicking directly on the highlight selects it and offers Delete highlight.
        actions = self._open_context_menu(view, self.word_point(view, 0, 1))
        self.assertEqual(view.selected_annotation, (0, 1))
        self.assertIn("Delete highlight", actions)
        self.assertTrue(actions["Delete highlight"].isEnabled())
        self._click_menu_action(view, actions["Delete highlight"])
        self.assertEqual(view.doc.annotations(0), [])

        # Read-only views (e.g. a second pane while another holds unsaved edits) keep Copy enabled while disabling Highlight.
        self.select_words(view, 0, 2)
        actions = self._open_context_menu(view, self.word_point(view, 0, 1))
        self._click_menu_action(view, actions["Highlight"])
        quick = self.window.open_quick(FileSource(path)).view
        self.pump(lambda: quick.has_page(0))
        self.assertTrue(quick.read_only)
        self.select_words(quick, 0, 2)
        actions = self._open_context_menu(quick, self.word_point(quick, 0, 1))
        self.assertTrue(actions["Copy"].isEnabled())
        self.assertFalse(actions["Highlight"].isEnabled())
        quick._context_menu.close()

    def test_context_menu_highlight_and_copy_on_two_column_line_numbered_pdf_exclude_numbers(self):
        view = self.open("two-col-lineno", lambda path: two_columns_with_line_numbers(path, right_side="gutter", step=5))
        # Drag from slightly left of column 2 (in the gutter) across three lines of column 2.
        start = self.point(view, 0, 297, 80)
        end = self.point(view, 0, 480, 114)
        self.drag(view, [start, end])
        self.assertEqual(view.selected_text(),
                         "right col line 0 text\nright col line 1 text\nright col line 2 text")
        actions = self._open_context_menu(view, self.point(view, 0, 360, 96))
        self._click_menu_action(view, actions["Highlight"])
        highlights = [a for a in view.doc.annotations(0) if a["subtype"] == "highlight"]
        self.assertEqual(len(highlights), 1)
        self.assertGreaterEqual(highlights[0]["rect"].x0, 318)


_FOREIGN_STROKES = [[(10, 20), (110, 120)], [(30, 140), (130, 240)]]


def _foreign_ink(indirect_outer, indirect_strokes, normal='missing', indirect_ap=False):
    writer = PdfWriter()
    writer.add_blank_page(600, 700)
    strokes = ArrayObject()
    for points, indirect in zip(_FOREIGN_STROKES, indirect_strokes):
        stroke = ArrayObject(FloatObject(value) for point in points for value in point)
        strokes.append(writer._add_object(stroke) if indirect else stroke)
    annotation = DictionaryObject({
        NameObject('/Type'): NameObject('/Annot'),
        NameObject('/Subtype'): NameObject('/Ink'), NameObject('/F'): NumberObject(4),
        NameObject('/Rect'): RectangleObject((0, 0, 150, 250)),
        NameObject('/InkList'): writer._add_object(strokes) if indirect_outer else strokes,
        NameObject('/C'): ArrayObject([FloatObject(0)] * 3),
        NameObject('/BS'): DictionaryObject({NameObject('/W'): FloatObject(2)}),
    })
    if normal != 'missing':
        if normal == 'stream':
            appearance = DecodedStreamObject()
            appearance.set_data(b'0 0 0 RG 2 w 10 20 m 110 120 l S 30 140 m 130 240 l S')
            appearance.update({
                NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Form'),
                NameObject('/BBox'): RectangleObject((0, 0, 150, 250)),
                NameObject('/Resources'): DictionaryObject(),
            })
            normal_value = writer._add_object(appearance)
        else:
            normal_value = writer._add_object(NullObject()) if normal == 'indirect-null' else NullObject()
        appearances = DictionaryObject({NameObject('/N'): normal_value})
        annotation[NameObject('/AP')] = writer._add_object(appearances) if indirect_ap else appearances
    writer.add_annotation(0, annotation)
    return pdf_bytes(writer)


@needs_pdf
class IndirectInkTests(unittest.TestCase):
    def variants(self, normal='missing'):
        for indirect_outer in (False, True):
            for indirect_strokes in ((False, True), (True, False), (True, True)):
                yield indirect_outer, indirect_strokes, _foreign_ink(indirect_outer, indirect_strokes, normal)

    def assert_revision(self, original, changed):
        self.assertTrue(changed.startswith(original))
        self.assertGreater(len(changed), len(original))
        annotation = PdfReader(io.BytesIO(changed)).pages[0]['/Annots'][0].get_object()
        appearance = annotation['/AP']['/N']
        self.assertEqual(appearance['/Subtype'], '/Form')
        self.assertEqual(appearance.get_data().split().count(b'S'), 2)
        renderer = Pdf(changed)
        try:
            self.assertLess(min(bytes(renderer.render(0, .5).rgb)), 240)
        finally:
            renderer.close()
        return annotation

    def test_width_edit_resolves_indirect_outer_and_mixed_stroke_arrays(self):
        from paperassist.native.annotations import apply, read
        for outer, strokes, original in self.variants():
            with self.subTest(indirect_outer=outer, indirect_strokes=strokes):
                self.assertEqual(read(original, 0)[0]['strokes'], _FOREIGN_STROKES)
                changed = apply(original, 'update', 0, index=0, width=5)
                self.assert_revision(original, changed)
                item = read(changed, 0)[0]
                self.assertEqual(item['strokes'], _FOREIGN_STROKES)
                self.assertEqual(item['width'], 5)
                self.assertEqual(item['rect'], (7.5, 17.5, 132.5, 242.5))

    def test_recolor_without_appearance_regenerates_all_indirect_strokes(self):
        from paperassist.native.annotations import apply, read
        for outer, strokes, original in self.variants():
            with self.subTest(indirect_outer=outer, indirect_strokes=strokes):
                changed = apply(original, 'update', 0, index=0, color=(.2, .4, .6))
                annotation = self.assert_revision(original, changed)
                item = read(changed, 0)[0]
                self.assertEqual(item['strokes'], _FOREIGN_STROKES)
                self.assertEqual(item['color'], (.2, .4, .6))
                self.assertIn(b'0.200000 0.400000 0.600000 RG', annotation['/AP']['/N'].get_data())

    def test_transform_resolves_indirect_strokes_and_keeps_original_appearance(self):
        from paperassist.native.annotations import apply, read
        expected = [[(10 + x * 2, 20 + y * 2) for x, y in stroke] for stroke in _FOREIGN_STROKES]
        for outer, strokes, original in self.variants(normal='stream'):
            with self.subTest(indirect_outer=outer, indirect_strokes=strokes):
                before = PdfReader(io.BytesIO(original)).pages[0]['/Annots'][0].get_object()['/AP']['/N'].get_data()
                changed = apply(original, 'transform', 0, index=0, rect=(10, 20, 310, 520))
                annotation = self.assert_revision(original, changed)
                item = read(changed, 0)[0]
                self.assertEqual(item['strokes'], expected)
                self.assertEqual(item['width'], 4)
                self.assertEqual(item['rect'], (10, 20, 310, 520))
                self.assertEqual(annotation['/AP']['/N'].get_data(), before)

    def test_direct_and_indirect_null_normal_appearances_regenerate_on_recolor(self):
        from paperassist.native.annotations import apply, read
        for indirect_ap in (False, True):
            for normal in ('null', 'indirect-null'):
                with self.subTest(indirect_ap=indirect_ap, normal=normal):
                    original = _foreign_ink(True, (True, False), normal, indirect_ap)
                    changed = apply(original, 'update', 0, index=0, color=(1, 0, 0))
                    annotation = self.assert_revision(original, changed)
                    self.assertEqual(read(changed, 0)[0]['strokes'], _FOREIGN_STROKES)
                    self.assertIn(b'1.000000 0.000000 0.000000 RG', annotation['/AP']['/N'].get_data())

    def test_document_edit_leaves_source_untouched_until_explicit_incremental_save(self):
        original = _foreign_ink(True, (True, False), 'indirect-null', True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'external.pdf'
            path.write_bytes(original)
            document = PdfDocument(FileSource(path))
            try:
                document.update_annotation(0, 0, color=(0, 0, 1), width=6)
                self.assertEqual(path.read_bytes(), original)
                self.assertTrue(document.modified)
                self.assert_revision(original, document.pdf.data)
                self.assertTrue(document.save())
                self.assertEqual(path.read_bytes(), document.pdf.data)
                self.assertTrue(path.read_bytes().startswith(original))
            finally:
                document.close()
