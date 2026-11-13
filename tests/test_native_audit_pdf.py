"""Regression cases for foreign PDF structures and displayed text geometry."""
import io
import unittest
from unittest.mock import MagicMock, patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NullObject, RectangleObject)

from test_native_support import needs_pdf, needs_qt


def pdf_bytes(writer):
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def ink(writer, color=None, **entries):
    annotation = DictionaryObject({NameObject('/Subtype'): NameObject('/Ink'),
        NameObject('/Rect'): RectangleObject((10, 20, 110, 120)),
        NameObject('/InkList'): ArrayObject([ArrayObject(FloatObject(v) for v in (10, 20, 110, 120))])})
    if color is not None:
        annotation[NameObject('/C')] = ArrayObject(FloatObject(v) for v in color)
    for key, value in entries.items():
        annotation[NameObject('/' + key)] = value
    writer.add_annotation(0, annotation)
    return annotation


@needs_pdf
class ForeignAnnotationTests(unittest.TestCase):
    def writer(self):
        writer = PdfWriter()
        writer.add_blank_page(300, 400)
        return writer

    def test_indirect_quadpoints_are_read(self):
        from paperassist.native.annotations import read
        writer = self.writer()
        writer.add_annotation(0, DictionaryObject({NameObject('/Subtype'): NameObject('/Highlight'),
            NameObject('/Rect'): RectangleObject((10, 20, 110, 40)),
            NameObject('/QuadPoints'): writer._add_object(ArrayObject(FloatObject(v) for v in (10, 40, 110, 40, 10, 20, 110, 20)))}))
        self.assertEqual(read(pdf_bytes(writer), 0)[0]['quads'], [(10, 20, 110, 40)])

    def test_regenerated_width_uses_pdf_default_gray_and_cmyk_colors(self):
        from paperassist.native.annotations import apply, read
        for color, expected in ((None, (0, 0, 0)), ((.25,), (.25, .25, .25)), ((.1, .2, .3, .4), (.5, .4, .3))):
            with self.subTest(color=color):
                writer = self.writer()
                ink(writer, color)
                changed = apply(pdf_bytes(writer), 'update', 0, index=0, width=4)
                item = read(changed, 0)[0]
                self.assertEqual(item['width'], 4)
                for actual, wanted in zip(item['color'], expected):
                    self.assertAlmostEqual(actual, wanted)
                appearance = PdfReader(io.BytesIO(changed)).pages[0]['/Annots'][0].get_object()['/AP']['/N'].get_data()
                self.assertIn(('{:.6f} {:.6f} {:.6f} RG'.format(*expected)).encode(), appearance)

    def test_null_border_and_appearance_can_be_read_recolored_and_resized(self):
        from paperassist.native.annotations import apply, read
        writer = self.writer()
        ink(writer, BS=NullObject(), AP=NullObject())
        original = pdf_bytes(writer)
        self.assertEqual(read(original, 0)[0]['width'], 1)
        for change in ({'color': (1, 0, 0)}, {'width': 3}):
            changed = apply(original, 'update', 0, index=0, **change)
            self.assertEqual(len(read(changed, 0)), 1)
            self.assertIn('/N', PdfReader(io.BytesIO(changed)).pages[0]['/Annots'][0].get_object()['/AP'])

    def test_transform_normalizes_reversed_rectangle_without_flipping_ink(self):
        from paperassist.native.annotations import apply, read
        writer = self.writer()
        annotation = ink(writer, BS=DictionaryObject({NameObject('/W'): FloatObject(2)}))
        annotation[NameObject('/Rect')] = RectangleObject((110, 120, 10, 20))
        changed = apply(pdf_bytes(writer), 'transform', 0, index=0, rect=(20, 40, 220, 240))
        item = read(changed, 0)[0]
        self.assertEqual(item['strokes'], [[(20, 40), (220, 240)]])
        self.assertEqual(item['width'], 4)


@needs_pdf
class DisplayedGeometryTests(unittest.TestCase):
    def counter_rotated(self, rotation):
        writer = PdfWriter()
        page = writer.add_blank_page(300, 400)
        page.rotate(rotation)
        font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        # Rotation changes the page, while this baseline counter-rotates the text.
        matrix = b'0 1 -1 0 250 50' if rotation == 90 else b'0 -1 1 0 50 350'
        stream.set_data(b'BT /F1 12 Tf ' + matrix + b' Tm (Alpha ) Tj /F1 14 Tf (Beta) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        return pdf_bytes(writer)

    def test_rotated_pages_merge_font_spans_along_displayed_horizontal_line(self):
        from paperassist.native.engine import Pdf
        for rotation in (90, 270):
            with self.subTest(rotation=rotation):
                pdf = Pdf(self.counter_rotated(rotation))
                try:
                    hits = pdf.search('Alpha Beta')
                    self.assertEqual(len(hits), 1)
                    self.assertGreater(hits[0][1].width, hits[0][1].height * 3)
                finally:
                    pdf.close()

    def test_zero_page_and_zero_height_pdf_raise_a_readable_error_before_view_creation(self):
        from paperassist.native.engine import Pdf
        for sizes in ([], [(300, 0)], [(0, 400)]):
            document = MagicMock()
            document.__len__.return_value = len(sizes)
            document.get_page_size.side_effect = sizes
            with self.subTest(sizes=sizes), patch('paperassist.native.engine.pdfium.PdfDocument', return_value=document):
                with self.assertRaisesRegex(ValueError, 'positive width and height'):
                    Pdf(b'PDF bytes')
                document.close.assert_called_once()

    def test_empty_and_zero_height_layout_anchors_are_safe(self):
        from paperassist.native.layout import PageLayout
        self.assertEqual(PageLayout([], 1, 500).anchor(0), (0, 0))
        self.assertEqual(PageLayout([(400, 0)], 1, 500).anchor(0), (0, 0))

    def test_null_bookmark_top_keeps_following_outline_entries(self):
        from pypdf.generic import Fit
        from paperassist.native.outline import read_outline
        writer = PdfWriter()
        writer.add_blank_page(300, 400)
        writer.add_outline_item('No vertical coordinate', 0, fit=Fit.xyz(left=0, top=None, zoom=0))
        writer.add_outline_item('Later section', 0, fit=Fit.xyz(left=0, top=200, zoom=0))
        self.assertEqual(read_outline(pdf_bytes(writer)), [('No vertical coordinate', 0, 0, 0), ('Later section', 0, 0, 200)])

    def test_rotated_note_fragments_merge_by_displayed_baseline_and_keep_separate_lines(self):
        from paperassist.native.comments import note_rects
        from paperassist.native.engine import Pdf, Rect
        for rotation in (90, 270):
            pdf = Pdf(self.counter_rotated(rotation))
            try:
                bands = [Rect(30, 30, 70, 40), Rect(73, 30, 110, 40), Rect(30, 43, 70, 53), Rect(73, 43, 110, 53)]
                frame = pdf.frame(0)
                rects = []
                for band in bands:
                    points = [frame.to_pdf(*band[:2]), frame.to_pdf(*band[2:])]
                    rects.append([min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)])
                note = {'anchor': {'version': 1, 'pages': [{'page': 1, 'rects': rects}]}}
                self.assertEqual(note_rects(note, pdf), [(0, Rect(30, 30, 110, 40)), (0, Rect(30, 43, 110, 53))])
            finally:
                pdf.close()

    def test_rotated_notes_keep_horizontal_glyphs_and_vertical_spans_on_their_lines(self):
        from paperassist.native.comments import note_rects
        from paperassist.native.engine import Pdf, Rect
        cases = [([Rect(10, 10, 15, 20), Rect(16, 10, 21, 20), Rect(10, 23, 15, 33), Rect(16, 23, 21, 33)],
                  [Rect(10, 10, 21, 20), Rect(10, 23, 21, 33)]),
                 ([Rect(10, 10, 20, 20), Rect(22, 10, 32, 20), Rect(10, 23, 20, 33), Rect(22, 23, 32, 33)],
                  [Rect(10, 10, 32, 20), Rect(10, 23, 32, 33)]),
                 ([Rect(10, 10, 20, 50), Rect(10, 53, 20, 100), Rect(23, 10, 33, 50), Rect(23, 53, 33, 100)],
                  [Rect(10, 10, 20, 100), Rect(23, 10, 33, 100)])]
        for rotation in (0, 90, 180, 270):
            writer = PdfWriter()
            writer.add_blank_page(300, 400).rotate(rotation)
            pdf = Pdf(pdf_bytes(writer))
            try:
                for bands, expected in cases:
                    rects = []
                    for band in bands:
                        points = [pdf.frame(0).to_pdf(*band[:2]), pdf.frame(0).to_pdf(*band[2:])]
                        rects.append([min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)])
                    note = {'anchor': {'version': 1, 'pages': [{'page': 1, 'rects': rects}]}}
                    with self.subTest(rotation=rotation, bands=bands):
                        self.assertEqual(note_rects(note, pdf), [(0, rect) for rect in expected])
            finally:
                pdf.close()

    def test_narrow_punctuation_does_not_override_word_span_direction(self):
        from paperassist.native.comments import note_rects
        from paperassist.native.engine import Pdf, Rect
        cases = [([Rect(10, 10, 40, 20), Rect(42, 10, 45, 20),
                   Rect(10, 23, 40, 33), Rect(42, 23, 45, 33)],
                  [Rect(10, 10, 45, 20), Rect(10, 23, 45, 33)]),
                 ([Rect(5, 10, 8, 20), Rect(10, 10, 40, 20),
                   Rect(5, 23, 8, 33), Rect(10, 23, 40, 33)],
                  [Rect(5, 10, 40, 20), Rect(5, 23, 40, 33)]),
                 ([Rect(10, 10, 20, 50), Rect(10, 52, 20, 55),
                   Rect(23, 10, 33, 50), Rect(23, 52, 33, 55)],
                  [Rect(10, 10, 20, 55), Rect(23, 10, 33, 55)])]
        for rotation in (0, 90, 180, 270):
            writer = PdfWriter()
            writer.add_blank_page(300, 400).rotate(rotation)
            data = pdf_bytes(writer)
            pdf = Pdf(data)
            try:
                for bands, expected in cases:
                    rects = []
                    for band in bands:
                        points = [pdf.frame(0).to_pdf(*band[:2]), pdf.frame(0).to_pdf(*band[2:])]
                        rects.append([min(p[0] for p in points), min(p[1] for p in points),
                                      max(p[0] for p in points), max(p[1] for p in points)])
                    note = {'anchor': {'version': 1, 'pages': [{'page': 1, 'rects': rects}]}}
                    with self.subTest(rotation=rotation, bands=bands):
                        self.assertEqual(note_rects(note, pdf), [(0, rect) for rect in expected])
                        self.assertEqual(pdf.data, data)
            finally:
                pdf.close()


@needs_pdf
class CitationGrammarTests(unittest.TestCase):
    def test_year_suffixes_two_authors_and_particle_surnames(self):
        from paperassist.native.citations import citation_matches
        refs = [dict(id='a', authors='Jones, J., Brown, B.', year='2020', raw='Jones, J., Brown, B. 2020a. First paper.'),
                dict(id='b', authors='Jones, J., Brown, B.', year='2020b'),
                dict(id='c', authors='van Rossum, G.', year='2015'),
                dict(id='d', authors='de la Cruz, J.', year='2020')]
        for citation, index in (('Jones & Brown 2020a', 0), ('Jones and Brown (2020b)', 1), ('van Rossum 2015', 2), ('de la Cruz 2020', 3)):
            with self.subTest(citation=citation):
                self.assertEqual(citation_matches(refs, citation), [refs[index]])
        self.assertEqual(citation_matches(refs, 'Jones & White 2020a'), [])
        self.assertEqual(citation_matches(refs, 'Jones & Brown 2020c'), [])
        self.assertEqual(citation_matches(refs, 'Jones & Brown 2020'), [])
