"""Native reader logic that needs no screen: layout, cache, close rules, words and saving."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_native_support import make_pdf, needs_pdf, pymupdf

if pymupdf:
    from paperassist.native.document import PdfDocument, SaveState
    from paperassist.native.engine import Pdf, Rect
    from paperassist.native.layout import GAP, MARGIN, PageCache, PageLayout
    from paperassist.native.rules import CANCEL, DISCARD, SAVE, apply_decisions, collect_decisions, locked_by_other
    from paperassist.native.source import FileSource


class Fake:
    def __init__(self, key, modified):
        self.key, self.modified = key, modified


class LayoutTests(unittest.TestCase):
    layout = PageLayout([(400, 500), (400, 500), (200, 300)], 2.0, 1000) if pymupdf else None

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
        with pymupdf.open(self.path) as saved:
            return [a.type[1] for page in saved for a in page.annots()]

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

    def test_files_that_cannot_be_appended_to_are_rewritten_atomically(self):
        self.highlight_first_line()
        with patch.object(Pdf, "can_save_incrementally", return_value=False):
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
        with pymupdf.open(path) as raw:
            cite = raw[0].search_for("Smith")[0]
            top = raw[3].rect.height - 108  # PDF space, just above the "[1] Smith" line
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
        with pymupdf.open(path) as raw:  # MuPDF itself reports the unrotated place
            self.assertNotEqual(tuple(raw[0].search_for("nanoflares")[0]), tuple(rotated.search("nanoflares")[0][1]))
        hit = rotated.search("nanoflares")[0][1]
        rotated.add_highlight(0, [hit])
        self.assertTrue(rotated.save())
        with pymupdf.open(path) as saved:  # an independent reader draws the highlight where the text is
            pixels = saved[0].get_pixmap(clip=pymupdf.Rect(*hit), alpha=False).samples
        yellow = sum(1 for i in range(0, len(pixels), 3) if pixels[i] > 200 and pixels[i + 1] > 180 and pixels[i + 2] < 120)
        self.assertGreater(yellow * 3, len(pixels) // 3 * 0.3)

    def test_a_link_into_a_rotated_page_uses_that_pages_rotation(self):
        rotated = PdfDocument(FileSource(make_pdf(Path(self._dir.name) / "rotated.pdf", pages=2, rotate=90)))
        self.addCleanup(rotated.close)
        link = rotated.links(0)[0]
        self.assertEqual(link.page, 1)
        self.assertNotAlmostEqual(link.y, 120)  # 120 was the unrotated y; displayed it is a former x
        self.assertTrue(0 <= link.y <= 400)


if __name__ == "__main__":
    unittest.main()
