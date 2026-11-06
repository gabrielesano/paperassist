"""PDFium geometry and pypdf interchange, including web-reader and foreign annotations."""
import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, RectangleObject, TextStringObject)

from test_native_support import make_pdf, needs_pdf


@needs_pdf
class EngineTests(unittest.TestCase):
    def setUp(self):
        from paperassist.native.engine import Pdf, PageRenderer, Rect
        from paperassist.native.document import PdfDocument
        from paperassist.native.source import FileSource
        self.Pdf, self.PageRenderer, self.Rect = Pdf, PageRenderer, Rect
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'sample.pdf'
        self.path.write_bytes((Path(__file__).parent / 'fixtures/pdfjs-annotations.pdf').read_bytes())
        self.doc = PdfDocument(FileSource(self.path))
        self.addCleanup(self.doc.close)

    def pixels(self, data):
        renderer = self.PageRenderer(data)
        try:
            return bytes(renderer.render(0, .75).rgb)
        finally:
            renderer.close()

    def test_the_web_palette_ink_and_inkhighlight_are_read_even_with_appearance_streams(self):
        items = self.doc.annotations(0)
        self.assertEqual(len(items), 7)
        for item, color in zip(items, ('f5d76e', '8fd3aa', '79c5df', 'e9a9bc', '243f3a')):
            self.assertEqual([round(v * 255) for v in item['color']], list(bytes.fromhex(color)))
        self.assertEqual(items[5]['width'], 4)
        self.assertTrue(items[6]['freehand'])
        self.assertFalse(items[5]['freehand'])

    def test_recolor_delete_add_undo_redo_save_reopen_preserve_pixels_and_original_prefix(self):
        original = self.path.read_bytes()
        self.doc.update_annotation(0, 0, color=(0, .5, 1))
        self.doc.update_annotation(0, 5, width=7)
        self.doc.delete_annotation(0, 1)
        self.doc.add_highlight(0, [self.Rect(*self.doc.words(0)[0][:4])])
        before_save = self.pixels(self.doc.pdf.data)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(self.doc.save())
        saved = self.path.read_bytes()
        self.assertTrue(saved.startswith(original))
        self.assertEqual(before_save, self.pixels(saved))
        self.assertFalse(self.doc.modified)
        self.doc.undo()
        self.assertTrue(self.doc.modified)
        self.doc.redo()
        self.assertFalse(self.doc.modified)
        self.doc.undo()
        self.assertTrue(self.doc.save())
        self.assertTrue(self.path.read_bytes().startswith(saved))
        reopened = self.Pdf(self.path.read_bytes())
        self.addCleanup(reopened.close)
        self.assertEqual(len(reopened.annotations(0)), 6)
        self.assertEqual(reopened.annotations(0)[0]['color'], (0, .5, 1))

    def test_undo_back_to_the_original_is_clean_and_a_new_branch_has_no_redo(self):
        original_pixels = self.pixels(self.doc.pdf.data)
        self.doc.delete_annotation(0, 0)
        self.doc.undo()
        self.assertFalse(self.doc.modified)
        self.assertEqual(original_pixels, self.pixels(self.doc.pdf.data))
        self.doc.update_annotation(0, 0, color=(1, 0, 0))
        self.assertEqual(self.doc.redo(), [])
        self.assertTrue(self.doc.modified)

    def test_recolor_preserves_freehand_filled_curves_and_multiply(self):
        before = PdfReader(io.BytesIO(self.doc.pdf.data)).pages[0]['/Annots'][6].get_object()
        old = before['/AP']['/N'].get_object().get_data()
        self.doc.update_annotation(0, 6, color=(0, 0, 1))
        after = PdfReader(io.BytesIO(self.doc.pdf.data)).pages[0]['/Annots'][6].get_object()
        appearance = after['/AP']['/N'].get_object()
        self.assertEqual(after['/IT'], '/InkHighlight')
        self.assertEqual(appearance['/Resources']['/ExtGState']['/R0']['/BM'], '/Multiply')
        self.assertIn(b' h f', appearance.get_data().replace(b'\n', b' '))
        self.assertEqual(old.count(b' c'), appearance.get_data().count(b' c'))
        self.assertNotEqual(self.pixels(self.path.read_bytes()), self.pixels(self.doc.pdf.data))

    def test_foreign_highlight_without_an_appearance_gets_a_multiply_appearance_on_edit(self):
        writer = PdfWriter(clone_from=self.path)
        writer.add_annotation(0, DictionaryObject({NameObject('/Subtype'): NameObject('/Highlight'),
            NameObject('/Rect'): RectangleObject((30, 720, 200, 740)),
            NameObject('/QuadPoints'): ArrayObject(FloatObject(v) for v in (30, 740, 200, 740, 30, 720, 200, 720)),
            NameObject('/C'): ArrayObject(FloatObject(v) for v in (1, 1, 0)), NameObject('/CA'): FloatObject(.5),
            NameObject('/NM'): TextStringObject('foreign-test-id')}))
        out = io.BytesIO();writer.write(out)
        pdf = self.Pdf(out.getvalue());self.addCleanup(pdf.close)
        self.assertEqual(pdf.annotations(0)[-1]['opacity'], .5)
        pdf.update_annotation(0, 7, color=(0, 1, 0))
        annotation = PdfReader(io.BytesIO(pdf.data)).pages[0]['/Annots'][7].get_object()
        self.assertEqual((annotation['/NM'], annotation['/CA']), ('foreign-test-id', .5))
        self.assertEqual(annotation['/AP']['/N']['/Resources']['/ExtGState']['/GS']['/BM'], '/Multiply')

    def test_ink_hit_testing_uses_the_stroke_and_does_not_select_its_empty_bounding_box(self):
        item = self.doc.annotations(0)[5]
        x, y = item['strokes'][0][5]
        self.assertEqual(self.doc.annotation_at(0, x, y)['index'], 5)
        box = item['rect']
        self.assertIsNone(self.doc.annotation_at(0, box.x1 - 2, box.y1 - 2, tolerance=0))

    def test_foreign_nested_form_appearance_is_recolored_without_losing_its_shape(self):
        writer = PdfWriter(clone_from=self.path)
        annotation = writer.pages[0]['/Annots'][0].get_object()
        previous = annotation['/AP']['/N'].get_object()
        wrapper = DecodedStreamObject()
        wrapper.set_data(b'/Child Do')
        wrapper.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
            NameObject('/BBox'): previous['/BBox'], NameObject('/Resources'): DictionaryObject({NameObject('/XObject'):
                DictionaryObject({NameObject('/Child'): annotation['/AP'].raw_get('/N')})})})
        annotation[NameObject('/AP')] = DictionaryObject({NameObject('/N'): writer._add_object(wrapper)})
        output = io.BytesIO();writer.write(output)
        pdf = self.Pdf(output.getvalue());self.addCleanup(pdf.close)
        before = self.pixels(pdf.data)
        pdf.update_annotation(0, 0, color=(0, 0, 1))
        self.assertNotEqual(before, self.pixels(pdf.data))
        item = PdfReader(io.BytesIO(pdf.data)).pages[0]['/Annots'][0].get_object()
        outer = item['/AP']['/N'].get_object()
        self.assertEqual(outer.get_data(), b'/Child Do\n')
        child = outer['/Resources']['/XObject']['/Child'].get_object()
        self.assertIn(b'0.0 0.0 1 rg', child.get_data())
        self.assertIn(b'f*', child.get_data())

    def test_same_color_or_inapplicable_highlight_width_is_not_an_edit(self):
        self.assertFalse(self.doc.update_annotation(0, 0, color=tuple(v / 255 for v in (245, 215, 110))))
        self.assertFalse(self.doc.update_annotation(0, 0, width=6))
        self.assertFalse(self.doc.modified)

    def test_line_end_hyphens_and_ligatures_copy_as_plain_text(self):
        writer = PdfWriter()
        page = writer.add_blank_page(400, 500)
        mapping = DecodedStreamObject()
        mapping.set_data(b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap '
            b'/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def '
            b'/CMapName /Test def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange '
            b'1 beginbfchar <41> <FB01> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end')
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Courier'), NameObject('/ToUnicode'): writer._add_object(mapping)})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 40 430 Td (inter-) Tj 0 -14 Td (national Aeld) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        output = io.BytesIO();writer.write(output)
        pdf = self.Pdf(output.getvalue());self.addCleanup(pdf.close)
        words = [word[4] for word in pdf.words(0)]
        self.assertEqual(words, ['inter-', 'national', 'field'])
        self.assertNotIn('\ufffe', ' '.join(words))

    def test_fit_destinations_and_cropped_rotated_pages_keep_view_coordinates(self):
        for rotation in (0, 90, 180, 270):
            path = make_pdf(self.path.with_name(f'rotated-{rotation}.pdf'), pages=2, rotate=rotation)
            writer = PdfWriter(clone_from=path)
            for page in writer.pages:
                page.cropbox = RectangleObject((20, 30, 380, 480))
            for mode, values in (('/FitH', (380,)), ('/FitR', (40, 350, 200, 380))):
                writer.add_annotation(0, DictionaryObject({NameObject('/Subtype'): NameObject('/Link'),
                    NameObject('/Rect'): RectangleObject((35, 150, 80, 170)),
                    NameObject('/Dest'): ArrayObject([writer.pages[1].indirect_reference, NameObject(mode),
                                                     *(FloatObject(v) for v in values)])}))
            out = io.BytesIO();writer.write(out)
            pdf = self.Pdf(out.getvalue());self.addCleanup(pdf.close)
            expected_size = (360, 450) if rotation % 180 == 0 else (450, 360)
            self.assertEqual(pdf.sizes[0], expected_size)
            for link in pdf.links(0):
                self.assertTrue(0 <= link.y <= expected_size[1])
            self.assertAlmostEqual(pdf.links(0)[-1].y, pdf.frame(1).point(40, 380)[1])
            self.assertEqual(len(pdf.search('As shown')), 1)
            hit = pdf.search('nanoflares')[0][1]
            pdf.add_highlight(0, [hit])
            annot = pdf.annotations(0)[0]
            self.assertAlmostEqual(annot['quads'][0].x0, hit.x0, places=4)
            self.assertAlmostEqual(annot['quads'][0].y0, hit.y0, places=4)
