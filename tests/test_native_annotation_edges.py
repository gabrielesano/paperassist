"""Indirect ink strokes and null normal appearances in external PDFs."""
import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NullObject, NumberObject, RectangleObject)

from test_native_support import needs_pdf


STROKES = [[(10, 20), (110, 120)], [(30, 140), (130, 240)]]


def foreign_ink(indirect_outer, indirect_strokes, normal='missing', indirect_ap=False):
    writer = PdfWriter()
    writer.add_blank_page(600, 700)
    strokes = ArrayObject()
    for points, indirect in zip(STROKES, indirect_strokes):
        stroke = ArrayObject(FloatObject(value) for point in points for value in point)
        strokes.append(writer._add_object(stroke) if indirect else stroke)
    annotation = DictionaryObject({NameObject('/Type'): NameObject('/Annot'),
        NameObject('/Subtype'): NameObject('/Ink'), NameObject('/F'): NumberObject(4),
        NameObject('/Rect'): RectangleObject((0, 0, 150, 250)),
        NameObject('/InkList'): writer._add_object(strokes) if indirect_outer else strokes,
        NameObject('/C'): ArrayObject([FloatObject(0)] * 3),
        NameObject('/BS'): DictionaryObject({NameObject('/W'): FloatObject(2)})})
    if normal != 'missing':
        if normal == 'stream':
            appearance = DecodedStreamObject()
            appearance.set_data(b'0 0 0 RG 2 w 10 20 m 110 120 l S 30 140 m 130 240 l S')
            appearance.update({NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Form'),
                NameObject('/BBox'): RectangleObject((0, 0, 150, 250)),
                NameObject('/Resources'): DictionaryObject()})
            normal_value = writer._add_object(appearance)
        else:
            normal_value = writer._add_object(NullObject()) if normal == 'indirect-null' else NullObject()
        appearances = DictionaryObject({NameObject('/N'): normal_value})
        annotation[NameObject('/AP')] = writer._add_object(appearances) if indirect_ap else appearances
    writer.add_annotation(0, annotation)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@needs_pdf
class IndirectInkTests(unittest.TestCase):
    def variants(self, normal='missing'):
        for indirect_outer in (False, True):
            for indirect_strokes in ((False, True), (True, False), (True, True)):
                yield indirect_outer, indirect_strokes, foreign_ink(indirect_outer, indirect_strokes, normal)

    def assert_revision(self, original, changed):
        self.assertTrue(changed.startswith(original))
        self.assertGreater(len(changed), len(original))
        annotation = PdfReader(io.BytesIO(changed)).pages[0]['/Annots'][0].get_object()
        appearance = annotation['/AP']['/N']
        self.assertEqual(appearance['/Subtype'], '/Form')
        self.assertEqual(appearance.get_data().split().count(b'S'), 2)
        from paperassist.native.engine import PageRenderer
        renderer = PageRenderer(changed)
        try:
            self.assertLess(min(bytes(renderer.render(0, .5).rgb)), 240)
        finally:
            renderer.close()
        return annotation

    def test_width_edit_resolves_indirect_outer_and_mixed_stroke_arrays(self):
        from paperassist.native.annotations import apply, read
        for outer, strokes, original in self.variants():
            with self.subTest(indirect_outer=outer, indirect_strokes=strokes):
                self.assertEqual(read(original, 0)[0]['strokes'], STROKES)
                changed = apply(original, 'update', 0, index=0, width=5)
                self.assert_revision(original, changed)
                item = read(changed, 0)[0]
                self.assertEqual(item['strokes'], STROKES)
                self.assertEqual(item['width'], 5)
                self.assertEqual(item['rect'], (7.5, 17.5, 132.5, 242.5))

    def test_recolor_without_appearance_regenerates_all_indirect_strokes(self):
        from paperassist.native.annotations import apply, read
        for outer, strokes, original in self.variants():
            with self.subTest(indirect_outer=outer, indirect_strokes=strokes):
                changed = apply(original, 'update', 0, index=0, color=(.2, .4, .6))
                annotation = self.assert_revision(original, changed)
                item = read(changed, 0)[0]
                self.assertEqual(item['strokes'], STROKES)
                self.assertEqual(item['color'], (.2, .4, .6))
                self.assertIn(b'0.200000 0.400000 0.600000 RG', annotation['/AP']['/N'].get_data())

    def test_transform_resolves_indirect_strokes_and_keeps_original_appearance(self):
        from paperassist.native.annotations import apply, read
        expected = [[(10 + x * 2, 20 + y * 2) for x, y in stroke] for stroke in STROKES]
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
                    original = foreign_ink(True, (True, False), normal, indirect_ap)
                    changed = apply(original, 'update', 0, index=0, color=(1, 0, 0))
                    annotation = self.assert_revision(original, changed)
                    self.assertEqual(read(changed, 0)[0]['strokes'], STROKES)
                    self.assertIn(b'1.000000 0.000000 0.000000 RG', annotation['/AP']['/N'].get_data())

    def test_document_edit_leaves_source_untouched_until_explicit_incremental_save(self):
        from paperassist.native.document import PdfDocument
        from paperassist.native.source import FileSource
        original = foreign_ink(True, (True, False), 'indirect-null', True)
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
