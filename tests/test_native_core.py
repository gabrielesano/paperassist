"""Native reader logic that needs no screen: layout, cache, close rules, words, saving, and foreign PDF geometry."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NullObject, RectangleObject)

from support import make_pdf, needs_pdf, pdf_available, pdf_bytes

if pdf_available:
    from paperassist.native.document import PdfDocument, SaveState
    from paperassist.native.engine import Pdf, Rect
    from paperassist.native.layout import GAP, MARGIN, PageCache, PageLayout
    from paperassist.native.rules import CANCEL, DISCARD, SAVE, apply_decisions, collect_decisions, locked_by_other
    from paperassist.native.source import FileSource


class Fake:
    def __init__(self, key, modified):
        self.key, self.modified = key, modified


class LayoutTests(unittest.TestCase):
    layout = PageLayout([(400, 500), (400, 500), (200, 300)], 2.0, 1000) if pdf_available else None

    @needs_pdf
    def test_pages_stack_with_gaps_and_are_centered(self):
        self.assertEqual(self.layout.tops, [MARGIN, MARGIN + 1000 + GAP, MARGIN + 2 * (1000 + GAP)])
        self.assertEqual(self.layout.rect(0), (100, MARGIN, 800, 1000))
        self.assertEqual(self.layout.rect(2)[0], 300)
        self.assertEqual(self.layout.height, self.layout.tops[2] + 600 + MARGIN)

    @needs_pdf
    def test_content_is_wider_than_the_viewport_when_zoomed_in(self):
        self.assertEqual(PageLayout([(400, 500)], 4.0, 1000).width, 1600 + 2 * MARGIN)

    @needs_pdf
    def test_visible_pages_and_page_at_gaps(self):
        top = self.layout.tops[1]
        self.assertEqual(self.layout.visible(0, 100), [0])
        self.assertEqual(self.layout.visible(top - GAP - 5, top + 5), [0, 1])
        self.assertEqual(self.layout.page_at(top - GAP / 2), 1)
        self.assertEqual(self.layout.page_at(top - GAP), 0)
        self.assertEqual(self.layout.page_at(-50), 0)

    @needs_pdf
    def test_point_mapping_round_trips_through_page_coordinates(self):
        for page, point in ((0, (10, 20)), (1, (399, 1)), (2, (100.5, 250))):
            x, y = self.layout.to_content(page, *point)
            self.assertEqual(self.layout.to_page(x, y), (page, *point))

    @needs_pdf
    def test_zoom_anchor_keeps_the_position_inside_a_page(self):
        top = self.layout.tops[1] + 250
        page, fraction = self.layout.anchor(top)
        self.assertEqual(page, 1)
        zoomed = PageLayout(self.layout.sizes, 3.0, 1000)
        self.assertAlmostEqual(zoomed.top_of(page, fraction), zoomed.tops[1] + 375)


@needs_pdf
class PageCacheTests(unittest.TestCase):
    def test_least_recently_used_pages_go_first_when_over_budget(self):
        cache = PageCache(100)
        for page in range(3):
            cache.put(page, f"p{page}", 40)
        self.assertEqual(list(cache.entries), [1, 2])
        cache[1]  # painting a page makes it the newest
        cache.put(3, "p3", 40)
        self.assertEqual(list(cache.entries), [1, 3])
        self.assertLessEqual(cache.total(), 100)

    def test_the_newest_page_stays_even_when_it_exceeds_the_budget(self):
        cache = PageCache(10)
        cache.put(0, "a", 50)
        cache.put(1, "b", 50)
        self.assertEqual(list(cache.entries), [1])

    def test_replacing_a_page_does_not_double_count_it(self):
        cache = PageCache(100)
        cache.put(0, "old", 60)
        cache.put(0, "new", 60)
        self.assertEqual((cache.total(), cache.get(0)), (60, "new"))

    def test_clear_releases_everything(self):
        cache = PageCache(100)
        cache.put(0, "a", 10)
        cache.clear()
        self.assertEqual((cache.total(), 0 in cache), (0, False))


@needs_pdf
class RuleTests(unittest.TestCase):
    def test_a_view_is_locked_only_by_another_modified_view_of_the_same_pdf(self):
        main, quick, other = Fake("a", True), Fake("a", False), Fake("b", False)
        panes = [main, quick, other]
        self.assertTrue(locked_by_other(quick, panes))
        self.assertFalse(locked_by_other(main, panes))  # its own edits never lock it
        self.assertFalse(locked_by_other(other, panes))

    def test_two_clean_views_are_both_editable(self):
        panes = [Fake("a", False), Fake("a", False)]
        self.assertFalse(any(locked_by_other(p, panes) for p in panes))

    def test_every_modified_pane_is_asked_before_anything_is_decided(self):
        asked = []
        panes = [Fake("a", True), Fake("b", False), Fake("c", True)]
        decisions = collect_decisions(panes, lambda pane: asked.append(pane.key) or SAVE)
        self.assertEqual(asked, ["a", "c"])
        self.assertEqual([answer for _, answer in decisions], [SAVE, SAVE])

    def test_cancelling_the_second_pane_cancels_the_whole_close(self):
        answers = iter([SAVE, CANCEL])
        panes = [Fake("a", True), Fake("c", True)]
        self.assertIsNone(collect_decisions(panes, lambda pane: next(answers)))

    def test_saves_stop_at_the_first_failure_and_discards_are_not_saved(self):
        saved = []
        decisions = [(Fake("a", True), SAVE), (Fake("b", True), DISCARD), (Fake("c", True), SAVE), (Fake("d", True), SAVE)]

        def save(pane):
            saved.append(pane.key)
            return pane.key != "c"
        self.assertFalse(apply_decisions(decisions, save))
        self.assertEqual(saved, ["a", "c"])


@needs_pdf
class DocumentTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = make_pdf(Path(self._dir.name) / "paper.pdf")
        self.original = self.path.read_bytes()
        self.doc = PdfDocument(FileSource(self.path))
        self.addCleanup(self.doc.close)

    def highlight_first_line(self):
        words = self.doc.words(0)
        self.doc.add_highlight(0, [Rect(*words[0][:4]).union(Rect(*words[5][:4]))])

    def annotations(self):
        return [str(a.get_object()['/Subtype'])[1:] for page in PdfReader(self.path).pages
                for a in page.get('/Annots', []) if a.get_object()['/Subtype'] != '/Link']

    def test_editing_marks_the_document_unsaved_and_writes_nothing(self):
        self.assertIs(self.doc.state, SaveState.SAVED)
        self.highlight_first_line()
        self.assertIs(self.doc.state, SaveState.UNSAVED)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_save_appends_an_incremental_update_and_keeps_the_original_bytes(self):
        self.highlight_first_line()
        self.assertTrue(self.doc.save())
        self.assertIs(self.doc.state, SaveState.SAVED)
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(self.original))
        self.assertGreater(len(data), len(self.original))
        self.assertEqual(self.annotations(), ["Highlight"])

    def test_a_crash_in_the_middle_of_writing_leaves_the_original_untouched(self):
        self.highlight_first_line()

        def die_half_way(out):
            out.write(b"half a file")
            raise OSError("power lost")

        with patch.object(Pdf, "write", side_effect=die_half_way):
            self.assertFalse(self.doc.save())
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["paper.pdf"])
        self.assertTrue(self.doc.modified)
        self.assertTrue(self.doc.save())  # the retry writes the whole edit
        self.assertEqual(self.annotations(), ["Highlight"])

    def test_saving_twice_appends_twice_and_a_reopened_file_shows_both(self):
        self.highlight_first_line()
        self.assertTrue(self.doc.save())
        first = self.path.read_bytes()
        words = self.doc.words(1)
        self.doc.add_highlight(1, [Rect(*words[0][:4])])
        self.assertTrue(self.doc.save())
        self.assertTrue(self.path.read_bytes().startswith(first))
        reopened = PdfDocument(FileSource(self.path))
        self.addCleanup(reopened.close)
        self.assertEqual(self.annotations(), ["Highlight", "Highlight"])
        self.assertFalse(reopened.modified)

    @unittest.skipIf(os.name == "nt", "Windows files have no Unix permission bits")
    def test_a_save_keeps_the_file_permissions(self):
        self.path.chmod(0o600)
        self.highlight_first_line()
        self.assertTrue(self.doc.save())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
    def test_an_open_document_keeps_no_file_handle_so_windows_can_replace_the_file(self):
        self.assertNotIn(str(self.path), [os.path.realpath(f"/proc/self/fd/{fd}") for fd in os.listdir("/proc/self/fd")])

    def test_saving_state_is_visible_while_the_file_is_written(self):
        seen = []
        self.highlight_first_line()
        self.doc.source.save = lambda doc: seen.append(self.doc.state)
        self.doc.save(on_saving=lambda: seen.append("callback"))
        self.assertEqual(seen, ["callback", SaveState.SAVING])

    def test_a_failed_save_keeps_the_edits_shows_the_error_and_can_be_retried(self):
        self.highlight_first_line()
        real_save = self.doc.source.save

        def refuse(doc):
            raise PermissionError("disk is read-only")
        self.doc.source.save = refuse
        self.assertFalse(self.doc.save())
        self.assertIs(self.doc.state, SaveState.FAILED)
        self.assertIn("disk is read-only", self.doc.error)
        self.assertTrue(self.doc.modified)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.doc.source.save = real_save  # the retry is the same explicit action
        self.assertTrue(self.doc.save())
        self.assertEqual((self.doc.state, self.doc.error, len(self.annotations())), (SaveState.SAVED, "", 1))

    def test_saving_rewrites_atomically_without_leaving_temp_files(self):
        self.highlight_first_line()
        self.assertTrue(self.doc.save())
        self.assertEqual(len(self.annotations()), 1)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["paper.pdf"])  # no temp file left behind

    def test_text_comes_in_reading_order_as_one_band_per_line(self):
        words = self.doc.words(0)
        lines = self.doc.selection_lines((0, 0), (0, 12))
        self.assertTrue(lines[0][2].startswith("Page 1. Coronal loops"))
        self.assertEqual(" ".join(w[4] for w in words[:13]), " ".join(text for _, _, text in lines))
        self.assertTrue(all(a[1].y0 < b[1].y0 for a, b in zip(lines, lines[1:])))

    def test_selection_works_backwards_and_across_pages(self):
        forward = self.doc.selection_lines((0, 40), (1, 3))
        backward = self.doc.selection_lines((1, 3), (0, 40))
        self.assertEqual(forward, backward)
        self.assertEqual({page for page, _, _ in forward}, {0, 1})

    def test_word_at_picks_the_word_under_the_point_or_the_nearest(self):
        x0, y0, x1, y1, *_ = self.doc.words(0)[7]
        self.assertEqual(self.doc.word_at(0, (x0 + x1) / 2, (y0 + y1) / 2), 7)
        self.assertIsNotNone(self.doc.word_at(0, 5, 5))

    def test_the_citation_link_points_at_the_bibliography(self):
        cite = self.doc.search("[1]")[0][1]
        link = self.doc.link_at(0, (cite.x0 + cite.x1) / 2, (cite.y0 + cite.y1) / 2)
        self.assertEqual((link.page, link.uri), (3, None))
        self.assertAlmostEqual(link.y, 120)
        self.assertIsNone(self.doc.link_at(0, 5, 5))

    def test_a_named_destination_lands_on_its_line(self):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
        path = make_pdf(Path(self._dir.name) / "named.pdf")
        raw = Pdf(path.read_bytes())
        cite = next(rect for page, rect in raw.search("Smith") if page == 0)
        raw.close()
        top = 500 - 108
        writer = PdfWriter(clone_from=path)
        writer.add_named_destination_array(TextStringObject("ref1"), ArrayObject(
            [writer.pages[3].indirect_reference, NameObject("/XYZ"), FloatObject(40), FloatObject(top), FloatObject(0)]))
        writer.add_annotation(0, DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (cite.x0, 500 - cite.y1, cite.x1, 500 - cite.y0)]),
            NameObject("/Dest"): TextStringObject("ref1")}))
        writer.write(path)
        named = PdfDocument(FileSource(path))
        self.addCleanup(named.close)
        link = named.link_at(0, (cite.x0 + cite.x1) / 2, (cite.y0 + cite.y1) / 2)
        self.assertEqual((link.page, link.uri), (3, None))
        self.assertAlmostEqual(link.y, 108)

    def test_search_finds_every_occurrence(self):
        self.assertEqual(len(self.doc.search("nanoflares")), 4 * 8)

    def test_rotated_pages_report_what_the_user_sees_and_highlights_land_on_the_text(self):
        path = make_pdf(Path(self._dir.name) / "rotated.pdf", pages=2, rotate=90)
        rotated = PdfDocument(FileSource(path))
        self.addCleanup(rotated.close)
        self.assertEqual(rotated.sizes[0], (500, 400))
        page_rect = Rect(0, 0, 500, 400)
        self.assertTrue(all(page_rect.union(Rect(*w[:4])) == page_rect for w in rotated.words(0)))
        hit = rotated.search("nanoflares")[0][1]
        rotated.add_highlight(0, [hit])
        self.assertTrue(rotated.save())
        renderer = Pdf(path.read_bytes())
        rendered = renderer.render(0, 1)
        data = bytes(rendered.rgb)
        pixels = b''.join(data[(y * rendered.width + int(hit.x0)) * 3:(y * rendered.width + int(hit.x1)) * 3]
                          for y in range(int(hit.y0), int(hit.y1)))
        renderer.close()
        yellow = sum(1 for i in range(0, len(pixels), 3) if pixels[i] > 200 and pixels[i + 1] > 180 and pixels[i + 2] < 120)
        self.assertGreater(yellow * 3, len(pixels) // 3 * 0.3)

    def test_a_link_into_a_rotated_page_uses_that_pages_rotation(self):
        rotated = PdfDocument(FileSource(make_pdf(Path(self._dir.name) / "rotated.pdf", pages=2, rotate=90)))
        self.addCleanup(rotated.close)
        link = rotated.links(0)[0]
        self.assertEqual(link.page, 1)
        self.assertNotAlmostEqual(link.y, 120)  # 120 was the unrotated y; displayed it is a former x
        self.assertTrue(0 <= link.y <= 400)


def _ink(writer, color=None, **entries):
    annotation = DictionaryObject({
        NameObject('/Subtype'): NameObject('/Ink'),
        NameObject('/Rect'): RectangleObject((10, 20, 110, 120)),
        NameObject('/InkList'): ArrayObject([ArrayObject(FloatObject(v) for v in (10, 20, 110, 120))]),
    })
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
        writer.add_annotation(0, DictionaryObject({
            NameObject('/Subtype'): NameObject('/Highlight'),
            NameObject('/Rect'): RectangleObject((10, 20, 110, 40)),
            NameObject('/QuadPoints'): writer._add_object(ArrayObject(FloatObject(v) for v in (10, 40, 110, 40, 10, 20, 110, 20))),
        }))
        self.assertEqual(read(pdf_bytes(writer), 0)[0]['quads'], [(10, 20, 110, 40)])

    def test_regenerated_width_uses_pdf_default_gray_and_cmyk_colors(self):
        from paperassist.native.annotations import apply, read
        for color, expected in ((None, (0, 0, 0)), ((.25,), (.25, .25, .25)), ((.1, .2, .3, .4), (.5, .4, .3))):
            with self.subTest(color=color):
                writer = self.writer()
                _ink(writer, color)
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
        _ink(writer, BS=NullObject(), AP=NullObject())
        original = pdf_bytes(writer)
        self.assertEqual(read(original, 0)[0]['width'], 1)
        for change in ({'color': (1, 0, 0)}, {'width': 3}):
            changed = apply(original, 'update', 0, index=0, **change)
            self.assertEqual(len(read(changed, 0)), 1)
            self.assertIn('/N', PdfReader(io.BytesIO(changed)).pages[0]['/Annots'][0].get_object()['/AP'])

    def test_transform_normalizes_reversed_rectangle_without_flipping_ink(self):
        from paperassist.native.annotations import apply, read
        writer = self.writer()
        annotation = _ink(writer, BS=DictionaryObject({NameObject('/W'): FloatObject(2)}))
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
        font = writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        }))
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        matrix = b'0 1 -1 0 250 50' if rotation == 90 else b'0 -1 1 0 50 350'
        stream.set_data(b'BT /F1 12 Tf ' + matrix + b' Tm (Alpha ) Tj /F1 14 Tf (Beta) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        return pdf_bytes(writer)

    def test_rotated_pages_merge_font_spans_along_displayed_horizontal_line(self):
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
        for sizes in ([], [(300, 0)], [(0, 400)]):
            document = MagicMock()
            document.__len__.return_value = len(sizes)
            document.get_page_size.side_effect = sizes
            with self.subTest(sizes=sizes), patch('paperassist.native.engine.pdfium.PdfDocument', return_value=document):
                with self.assertRaisesRegex(ValueError, 'positive width and height'):
                    Pdf(b'PDF bytes')
                document.close.assert_called_once()

    def test_empty_and_zero_height_layout_anchors_are_safe(self):
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
        from paperassist.citations import citation_matches
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


if __name__ == "__main__":
    unittest.main()
