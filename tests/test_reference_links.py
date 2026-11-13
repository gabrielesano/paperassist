"""Conservative bibliography anchors and reconciled text/link citation events."""
import copy
import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject,
                           NameObject, TextStringObject)

from paperassist import references
from paperassist.native.citations import CitationIndex, citation_parts, merge_links
from paperassist.native.engine import Pdf


ALPHA = 'Alpha, A. 2000, Journal, 1, 22'
BETA = 'Beta, B. 2001, Journal, 2, 33'


class MappingTests(unittest.TestCase):
    def map(self, lines, destinations, refs):
        class Page:
            mediabox = SimpleNamespace(right=600)

            def extract_text(self, visitor_text):
                for text, x, y in lines:
                    visitor_text(text, [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, x, y], None, 10)

        reader = SimpleNamespace(pages=[Page()], named_destinations=destinations,
                                 get_destination_page_number=lambda dest: 0)
        references.map_destinations(reader, ['References'], refs)
        return [ref['destinations'] for ref in refs]

    def destination(self, top=510, left=40, typ='/XYZ', bottom=None):
        return SimpleNamespace(top=top, left=left, typ=typ, bottom=bottom)

    def test_unknown_nearest_row_does_not_fall_back_to_next_entry(self):
        self.assertEqual(self.map([(ALPHA + ' damaged text', 40, 500), (BETA, 40, 489)],
                                  {'cite.alpha': self.destination()}, [{'raw': ALPHA}, {'raw': BETA}]), [[], []])

    def test_anchor_inside_font_height_belongs_to_that_baseline(self):
        self.assertEqual(self.map([(ALPHA, 40, 500), (BETA, 40, 492)],
                                  {'cite.alpha': self.destination(505)}, [{'raw': ALPHA}, {'raw': BETA}]),
                         [['cite.alpha'], []])

    def test_fit_modes_offsets_and_inverted_rectangle(self):
        for destination in (self.destination(518), self.destination(518, typ='/FitR', bottom=500),
                            self.destination(500, typ='/FitR', bottom=518),
                            self.destination(518, left=None, typ='/FitH'),
                            self.destination(518, left=None, typ='/FitBH')):
            with self.subTest(destination=destination):
                self.assertEqual(self.map([(ALPHA, 70, 500)], {'cite.alpha': destination}, [{'raw': ALPHA}]),
                                 [['cite.alpha']])

    def test_item_and_aalinkref_names_are_not_rejected_without_geometric_evidence(self):
        self.assertEqual(self.map([(ALPHA, 40, 500)],
                                  {name: self.destination() for name in ('Item.1', 'aalinkref.1', 'page.1', 'section.2')},
                                  [{'raw': ALPHA}]), [['Item.1', 'aalinkref.1']])

    def test_height_only_destination_cannot_choose_between_columns(self):
        self.assertEqual(self.map([(ALPHA, 40, 500), (BETA, 340, 500)],
                                  {'cite.unknown': self.destination(left=None, typ='/FitH')},
                                  [{'raw': ALPHA}, {'raw': BETA}]), [[], []])

    def test_equal_horizontal_coordinates_preserve_content_stream_order(self):
        self.assertEqual(self.map([(ALPHA, 40, 500), ('Ad-', 40, 500)],
                                  {'cite.alpha': self.destination()}, [{'raw': ALPHA + ' Ad- continuation'}]),
                         [['cite.alpha']])

    def test_repeated_author_row_matches_only_a_unique_entry(self):
        self.assertEqual(self.map([('---. 2000, Journal, 1, 22', 40, 500)],
                                  {'cite.alpha': self.destination()}, [{'raw': ALPHA}]), [['cite.alpha']])
        self.assertEqual(self.map([('---. 2000, Journal, 1, 22', 40, 500)],
                                  {'cite.alpha': self.destination()}, [{'raw': ALPHA}, {'raw': ALPHA}]), [[], []])

    def test_single_column_fragments_are_not_cut_at_260_points(self):
        text = 'Alpha, A. 2000, A very long title'
        self.assertEqual(self.map([(text, 40, 500), (', Journal, 1, 22', 330, 500)],
                                  {'cite.alpha': self.destination()}, [{'raw': text + ', Journal, 1, 22'}]),
                         [['cite.alpha']])


def make_link_paper(path, rotation=0, crop=False):
    """Two linked citations, an unlinked mention, a shared bibliography/appendix page, and an appendix."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Courier')}))
    lines = [[('Linked [1]', 40, 600), ('Linked [1]', 40, 576), ('Unlinked [1]', 40, 540)],
             [('References', 40, 640), ('[1] ' + ALPHA + ' [1]', 40, 600), ('Same-page appendix [1]', 340, 500)],
             [('Appendix', 40, 640), ('Appendix cites [1]', 40, 600)]]
    for page_lines in lines:
        page = writer.add_blank_page(600, 700)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data('\n'.join(f'BT /F1 11 Tf {x} {y} Td ({text}) Tj ET' for text, x, y in page_lines).encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
        if crop:
            page.cropbox.lower_left = (20, 30)
            page.cropbox.upper_right = (580, 680)
        page.rotate(rotation)
    writer.add_named_destination_array(TextStringObject('Item.1'), ArrayObject([
        writer.pages[1].indirect_reference, NameObject('/FitR'), FloatObject(40), FloatObject(620),
        FloatObject(260), FloatObject(600)]))
    for page_number, indices in ((0, (0, 1)), (1, (1, 2)), (2, (1,))):
        for index in indices:
            text, x, y = lines[page_number][index]
            x += text.rindex('[1]') * 6.6
            writer.add_annotation(page_number, DictionaryObject({NameObject('/Subtype'): NameObject('/Link'),
                NameObject('/Rect'): ArrayObject(FloatObject(v) for v in (x, y - 2, x + 19.8, y + 11)),
                NameObject('/Dest'): TextStringObject('Item.1')}))
    writer.write(path)


def make_cross_page_paper(path, rotation=0, linked=True, complete=False):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Courier')}))
    prefix = 'Smith et al. (2000)' if complete else 'Smith et al.'
    suffix = 'Smith et al. (2000)' if complete else '(2000)'
    rows = [[('Earlier Smith et al. are discussed.', 40, 600), (prefix, 40, 50)],
            [(suffix, 40, 640)],
            [('References', 40, 640), ('Smith, A., Jones, B., & Brown, C. 2000, Journal, 1, 22', 40, 600)]]
    for lines in rows:
        page = writer.add_blank_page(600, 700)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        content = []
        for text, x, y in lines:
            text = text.replace('(', r'\(').replace(')', r'\)')
            content.append(f'BT /F1 11 Tf {x} {y} Td ({text}) Tj ET')
        stream.set_data('\n'.join(content).encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
        page.rotate(rotation)
    writer.add_named_destination_array(TextStringObject('cite.smith'), ArrayObject([
        writer.pages[2].indirect_reference, NameObject('/XYZ'), FloatObject(40), FloatObject(613), FloatObject(0)]))
    if linked:
        for number, text, y in ((0, prefix, 50), (1, suffix, 640)):
            writer.add_annotation(number, DictionaryObject({NameObject('/Subtype'): NameObject('/Link'),
                NameObject('/Rect'): ArrayObject(FloatObject(v) for v in (40, y - 2, 40 + len(text) * 6.6, y + 11)),
                NameObject('/Dest'): TextStringObject('cite.smith')}))
    writer.write(path)


class EventTests(unittest.TestCase):
    def test_one_cross_page_event_keeps_geometry_on_both_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            for rotation in (0, 90, 180, 270):
                for linked in (False, True):
                    with self.subTest(rotation=rotation, linked=linked):
                        path = Path(directory) / 'source.pdf'
                        make_cross_page_paper(path, rotation, linked)
                        reader = PdfReader(path)
                        pages = [page.extract_text() for page in reader.pages]
                        refs = references.extract(pages)
                        self.assertEqual(refs[0]['citation_count'], 1)
                        references.map_destinations(reader, pages, refs)
                        references.crosscheck_links(path, pages, refs)
                        self.assertEqual(refs[0]['citation_count'], 1)
                        index = CitationIndex(path.read_bytes())
                        try:
                            events = index.locate(refs[0])
                            self.assertEqual(len(events), 1)
                            self.assertEqual([page for page, rects in citation_parts(events[0]) if rects], [1, 2])
                            # The first partial marker also appears earlier; navigation must use its final occurrence.
                            self.assertLess(refs[0]['occurrences'][0]['rects'][0][1], 100)
                        finally:
                            index.close()

    def test_complete_citations_at_adjacent_page_edges_remain_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'
            make_cross_page_paper(path, complete=True)
            reader = PdfReader(path)
            pages = [page.extract_text() for page in reader.pages]
            refs = references.extract(pages)
            references.map_destinations(reader, pages, refs)
            references.crosscheck_links(path, pages, refs)
            self.assertEqual(refs[0]['citation_count'], 2)
            self.assertFalse(any(event.get('continuations') for event in refs[0]['occurrences']))

    def test_split_submitted_year_and_degenerate_wrap_still_form_one_event(self):
        links = [{'page': 1, 'dest': 'a', 'rects': [(316, 600, 376, 610)], 'text': 'Shelyag et al.'},
                 {'page': 1, 'dest': 'a', 'rects': [(391, 600, 507, 610)], 'text': '(Submitted on 31 Jan 2011)'}]
        self.assertEqual(len(merge_links(links)), 1)
        links = [{'page': 1, 'dest': 'a', 'rects': [(324, 600, 521, 601)]},
                 {'page': 1, 'dest': 'a', 'rects': [(329, 590, 350, 600)], 'text': '2005'}]
        self.assertEqual(len(merge_links(links)), 1)

    def test_complete_citations_stay_distinct_and_incomplete_wraps_merge(self):
        for displayed in (False, True):
            boxes = [(100, 600, 220, 614), (100, 576, 220, 590)]
            if displayed:
                boxes = [(100, 86, 220, 100), (100, 110, 220, 124)]
            links = [{'page': 1, 'dest': 'a', 'rects': [box], 'text': 'Alpha 2000'} for box in boxes]
            self.assertEqual(len(merge_links(links, displayed=displayed)), 2)
            links[0]['text'] = 'Alpha &'
            self.assertEqual(len(merge_links(links, displayed=displayed)), 1)

    def test_union_keeps_unlinked_same_page_and_appendix_citations_without_bibliography(self):
        with tempfile.TemporaryDirectory() as directory:
            for rotation, crop in ((r, c) for r in (0, 90, 180, 270) for c in (False, True)):
                with self.subTest(rotation=rotation, crop=crop):
                    path = Path(directory) / 'source.pdf'
                    make_link_paper(path, rotation, crop)
                    before = hashlib.sha256(path.read_bytes()).digest()
                    reader = PdfReader(path)
                    pages = [page.extract_text() for page in reader.pages]
                    refs = references.extract(pages)
                    self.assertEqual(len(refs), 1)
                    self.assertEqual(refs[0]['citation_count'], 4)
                    references.map_destinations(reader, pages, refs)
                    self.assertEqual(refs[0]['destinations'], ['Item.1'])
                    references.crosscheck_links(path, pages, refs)
                    self.assertEqual(refs[0]['citation_count'], 5)
                    self.assertEqual([o['page'] for o in refs[0]['occurrences']], [1, 1, 1, 2, 3])
                    self.assertEqual(sum(o['source'] == 'text' for o in refs[0]['occurrences']), 1)
                    self.assertTrue(all(o['rects'] for o in refs[0]['occurrences']))
                    index = CitationIndex(path.read_bytes())
                    try:
                        self.assertEqual([o['page'] for o in index.locate(refs[0])], [1, 1, 1, 2, 3])
                    finally:
                        index.close()
                    self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_ambiguous_counts_are_not_silently_replaced_with_link_counts(self):
        refs = [{'raw': ALPHA, 'citation_count': None, 'count_status': 'ambiguous',
                 'destinations': ['Item.1'], 'occurrences': []}]
        references.crosscheck_links('unused.pdf', ['References'], refs)
        self.assertIsNone(refs[0]['citation_count'])

    def test_destination_migration_reextracts_saved_link_occurrences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'
            make_link_paper(path)
            reader = PdfReader(path)
            pages = [page.extract_text() for page in reader.pages]
            refs = references.extract(pages)
            references.map_destinations(reader, pages, refs)
            references.crosscheck_links(path, pages, refs)
            expected = copy.deepcopy(refs[0]['occurrences'])
            library = SimpleNamespace(db_path=Path(directory) / 'library.sqlite',
                                      get_path=lambda *args, **kwargs: path,
                                      list_documents=lambda: [])
            references._save(library, 1, {'references': refs, 'status': 'partial',
                                          'destination_version': references.DESTINATION_VERSION - 1,
                                          'extraction_version': references.EXTRACTION_VERSION})
            migrated = references.get(library, 1)['references'][0]
            self.assertEqual(migrated['citation_count'], len(expected))
            self.assertEqual(sum(o['source'] == 'link' for o in migrated['occurrences']),
                             sum(o['source'] == 'link' for o in expected))
            self.assertEqual([o['rects'] for o in migrated['occurrences']], [o['rects'] for o in expected])

    def test_duplicate_annotation_and_goto_action_do_not_add_an_extra_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'
            make_link_paper(path)
            writer = PdfWriter(clone_from=path)
            annotation = writer.pages[0]['/Annots'][0].get_object()
            duplicate = DictionaryObject({NameObject('/Subtype'): NameObject('/Link'),
                NameObject('/Rect'): annotation['/Rect'],
                NameObject('/A'): DictionaryObject({NameObject('/S'): NameObject('/GoTo'),
                                                    NameObject('/D'): TextStringObject('Item.1')})})
            writer.add_annotation(0, duplicate)
            output = Path(directory) / 'duplicate.pdf'
            writer.write(output)
            reader = PdfReader(output)
            pages = [page.extract_text() for page in reader.pages]
            refs = references.extract(pages)
            references.map_destinations(reader, pages, refs)
            references.crosscheck_links(output, pages, refs)
            self.assertEqual(refs[0]['citation_count'], 5)

    def test_background_crosscheck_can_overlap_foreground_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'
            make_link_paper(path)
            reader = PdfReader(path)
            pages = [page.extract_text() for page in reader.pages]
            refs = references.extract(pages)
            references.map_destinations(reader, pages, refs)
            barrier = threading.Barrier(2)

            def analyze():
                barrier.wait(timeout=5)
                for _ in range(8):
                    result = copy.deepcopy(refs)
                    references.crosscheck_links(path, pages, result)
                    self.assertEqual(result[0]['citation_count'], 5)

            def read():
                pdf = Pdf(path.read_bytes())
                try:
                    barrier.wait(timeout=5)
                    for _ in range(20):
                        self.assertTrue(pdf.words(0))
                        self.assertEqual(len(pdf.links(0)), 2)
                finally:
                    pdf.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(analyze), executor.submit(read)]
                for future in futures:
                    future.result(timeout=20)


if __name__ == '__main__':
    unittest.main()
