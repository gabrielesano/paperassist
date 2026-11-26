import unittest
import threading
from concurrent.futures import ThreadPoolExecutor

from paperassist.library import Library as Catalog
from paperassist.references import (analyze, extract, local_candidates, get, link, repair_downloads, set_retrieval, _load, _save, _store_analysis,
                                    map_destinations, _publication, _extract_pages, DESTINATION_VERSION, EXTRACTION_VERSION)
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch


class ReferenceTestLibrary(Catalog):
    def read_pdf(self, document_id, annotated=True):
        return str(self.get_path(document_id, annotated)).encode(), 0


def _fake_pdf(pages, named_destinations=None):
    class FakePdf:
        def __init__(self, _data=b''):
            self.pages = pages
            self.named_destinations = named_destinations or {}
            self.closed = False

        def close(self):
            self.closed = True

    return FakePdf


class ReferenceTests(unittest.TestCase):
    def test_publication_fingerprints_distinguish_journals(self):
        self.assertEqual(_publication('Antolin, P. 2012, ApJ, 745, 152', '2012'),
                         _publication('The Astrophysical Journal, 745:152 (21pp), 2012 February 1'))
        self.assertEqual(_publication('Fang 2013, ApJL, 771, L29', '2013'),
                         _publication('The Astrophysical Journal Letters, 771:L29 (6pp), 2013 July 10'))
        self.assertEqual(_publication('Mignone 2007, ApJS, 170, 228', '2007'),
                         _publication('The Astrophysical Journal Supplement Series, 170:228–242, 2007 June'))
        self.assertNotEqual(_publication('ApJ, 170, 228', '2007'), _publication('ApJS, 170, 228', '2007'))
        self.assertIsNone(_publication('ApJ, 170, 228'))

    def test_numeric_groups_ranges_exclude_bibliography(self):
        pages = ['See [1—3] and [1, 3; 1]. Ignore [2, 3–1].\n'
                 'References\n[1] Alpha 2020. First.\n[2] Beta 2021. Second.\n[3] Gamma 2022. Third.']
        refs = extract(pages)
        self.assertEqual([r['citation_count'] for r in refs], [2, 1, 2])
        self.assertTrue(all(r['count_status'] == 'partial' for r in refs))
        self.assertEqual(refs[0]['occurrences'][0]['page'], 1)
        self.assertEqual(refs[1]['occurrences'][0]['marker'], '[1—3]')

    def test_wrapped_years_and_article_numbers_stay_in_dotted_entries(self):
        refs = extract(['References\n'
                        '1. Alpha A. 2020 An observation made between 2005 and\n'
                        '2012. Example Journal 1, 10.\n'
                        '2. Beta B. 2021 An example from the literature. Journal 2,\n'
                        '271. (doi:10.1234/\n'
                        'example(2))\n'
                        '3. Gamma C. 2022 Another observation. Journal 3, 12.\n'
                        '5. Delta D. 2023 A reference with a skipped number.'])
        self.assertEqual([ref['label'] for ref in refs], ['1', '2', '3', '5'])
        self.assertIn('between 2005 and 2012. Example Journal', refs[0]['raw'])
        self.assertIn('Journal 2, 271.', refs[1]['raw'])
        self.assertEqual(refs[1]['doi'], '10.1234/example(2)')

    def test_author_year_and_no_heading(self):
        refs = extract(['Smith et al. (2020) and Smith (2020).\nReferences\nSmith, A. 2020. A useful paper.'])
        self.assertEqual(refs[0]['citation_count'], 2)
        self.assertEqual(extract(['No bibliography here']), [])

    def test_alternate_headings_require_reference_entries(self):
        for heading in ('References and Notes:', 'Appendix A – References'):
            with self.subTest(heading=heading):
                pages = [f'Text cites [1].\n{heading}\n[1] Alpha, A. 2020, ApJ, 1, 1\n[2] Beta, B. 2021, ApJ, 2, 2']
                self.assertEqual([ref['citation_count'] for ref in extract(pages)], [1, 0])
                self.assertEqual(extract([f'{heading}\nA section listed in the contents.']), [])

    def test_bibliography_can_begin_with_a_surname_without_a_comma(self):
        refs = extract(['Text cites Alpha (2020).\nReferences\nAlpha A., 2020, ApJ, 1, 1\nBeta B., 2021, ApJ, 2, 2'])
        self.assertEqual([ref['label'] for ref in refs], ['Alpha 2020', 'Beta 2021'])
        self.assertEqual(refs[0]['citation_count'], 1)

    def test_multiline_entries_and_ambiguous_year(self):
        refs = extract(['Smith et al. (2020) mentions this.\nReferences\nSmith, A., Jones, B.,\nBrown, C. A long title, Journal 4, 2020.\nSmith, C., Green, D., White, E., Another paper, Journal 5, 2020.'])
        self.assertEqual(len(refs), 2)
        self.assertTrue(all(ref['citation_count'] is None for ref in refs))
        self.assertTrue(all(ref['count_status'] == 'ambiguous' for ref in refs))

    def test_same_first_author_and_year_are_told_apart_by_author_count(self):
        pages = ['Smith & Jones (2020) and Smith et al. (2020) agree; Smith (2020) differs.\n'
                 'References\n'
                 'Smith, A., & Jones, B. 2020, Journal, 1, 1\n'
                 'Smith, A., Jones, B., & Brown, C. 2020, Journal, 2, 2\n'
                 'Smith, D. 2020, Journal, 3, 3']
        self.assertEqual([r['citation_count'] for r in extract(pages)], [1, 1, 1])
        pages = ['Smith & Brown (2020) and Smith et al. (2020).\nReferences\n'
                 'Smith, A., & Jones, B. 2020, Journal, 1, 1\n'
                 'Smith, A., Brown, C., & Green, D. 2020, Journal, 2, 2']
        # "Smith & Brown" fits neither entry: neither count can be trusted.
        self.assertEqual([r['citation_count'] for r in extract(pages)], [None, None])

    def test_wrapped_citations_do_not_consume_unrelated_prose(self):
        refs = extract(['Smith et al.\n(2020); Smith &\nJones (2020). Smith discussed Brown in 2020. Smith (2020a).\nReferences\nSmith, A. 2020. A useful paper.'])
        self.assertEqual(refs[0]['citation_count'], 2)
        self.assertIn('\n', refs[0]['occurrences'][0]['marker'])

    def test_unicode_surnames_start_distinct_entries(self):
        pages = ['Török et al. (2004), Martínez-Sykora (2008).\nReferences\n'
                 'Testa, P., & Reale, F. 2012, ApJ, 750, L10\n'
                 'Török, T., Kliem, B., & Titov, V. 2004, A&A, 413, L27\n'
                 'Martínez-Sykora, J., Hansteen, V., & Carlsson, M. 2008, ApJ, 679, 871\n'
                 'van Ballegooijen, A. A., & Berger, M. 2014, ApJ, 787, 87']
        refs = extract(pages)
        self.assertEqual([r['label'] for r in refs],
                         ['Testa 2012', 'Török 2004', 'Martínez-Sykora 2008', 'van Ballegooijen 2014'])
        self.assertEqual([refs[1]['citation_count'], refs[2]['citation_count']], [1, 1])
        self.assertNotIn('Török', refs[0]['raw'])

    def test_year_lists_and_suffix_lists_cite_each_entry(self):
        pages = ['Lee et al. (2005, 2008), Hall 2010a,b and Hall (2010a, b, c); Ray et al. 2001, 2003.\n'
                 'References\n'
                 'Lee, A., Kim, B., & Park, C. 2005, J, 1, 1\n'
                 'Lee, A., Kim, B., & Park, C. 2008, J, 1, 2\n'
                 'Hall, D. 2010a, J, 2, 1\n'
                 'Hall, D. 2010b, J, 2, 2\n'
                 'Hall, D. 2010c, J, 2, 3\n'
                 'Ray, E., Fox, F., & Day, G. 2001, J, 3, 1\n'
                 'Ray, E., Fox, F., & Day, G. 2003, J, 3, 2']
        self.assertEqual({r['label']: r['citation_count'] for r in extract(pages)},
                         {'Lee 2005': 1, 'Lee 2008': 1, 'Hall 2010a': 2, 'Hall 2010b': 2, 'Hall 2010c': 1,
                          'Ray 2001': 1, 'Ray 2003': 1})

    def test_second_author_is_not_a_citation_of_that_author(self):
        pages = ['Torrilhon & Balsara 2004 and Balsara (2004); Antolin & Rouppe van der Voort 2012; '
                 'Smith & Jones discussed Brown in 2020.\n'
                 'References\n'
                 'Balsara, D. 2004, J, 1, 1\n'
                 'Torrilhon, M., & Balsara, D. 2004, J, 1, 2\n'
                 'Rouppe van der Voort, L. 2012, J, 2, 2\n'
                 'Antolin, P., & Rouppe van der Voort, L. 2012, J, 2, 1\n'
                 'Smith, A., & Jones, B. 2020, J, 3, 1']
        self.assertEqual({r['label']: r['citation_count'] for r in extract(pages)},
                         {'Balsara 2004': 1, 'Torrilhon 2004': 1, 'Rouppe van der Voort 2012': 0,
                          'Antolin 2012': 1, 'Smith 2020': 0})

    def test_detached_accents_and_kerning_gaps_in_extracted_text(self):
        pages = ['M¨ uller et al. (2003); Mart ´ ınez-Sykora et al. 2009; W atanabe et al. (2008); '
                 'Rouppe van der Voort & Antolin (2012).\n'
                 'References\n'
                 'M¨ uller, D. A. N., Hansteen, V. H., & Peter, H. 2003, A&A, 411, 605\n'
                 'Mart ´ ınez-Sykora, J., Hansteen, V., DePontieu, B., & Carlsson, M.\n2009, ApJ, 701, 1569\n'
                 'W atanabe, T., Harra, L. K., & Hara, H. 200 8, ApJ, 678, L67\n'
                 'Dowdy, Jr., J. F., Rabin, D., & Moore, R. L. 1986, Sol. Phys., 105, 35\n'
                 'Rouppe van der Voort, L., & Antolin, P. 2012, J, 1, 1\n'
                 'Alexiades, V ., Amiez, G., & Gremaud, P. A. 1996, J, 1, 2']
        refs = extract(pages)
        self.assertEqual([r['label'] for r in refs],
                         ['Müller 2003', 'Martínez-Sykora 2009', 'Watanabe 2008', 'Dowdy 1986',
                          'Rouppe van der Voort 2012', 'Alexiades 1996'])
        self.assertEqual([r['citation_count'] for r in refs], [1, 1, 1, 0, 1, 0])

    def test_surnames_hyphenated_at_a_line_end_are_rejoined(self):
        refs = extract(['See Mignone & McKin-\nney (2007) and Or-\nlando et al. 2008.\nReferences\n'
                        'Mignone, A., & McKinney, J. C. 2007, MNRAS, 378, 1118\n'
                        'Orlando, S., Bocchino, F., & Reale, F. 2008, ApJ, 678, 274'])
        self.assertEqual([r['citation_count'] for r in refs], [1, 1])

    def test_body_text_after_a_mid_page_heading_still_counts(self):
        # In two-column pages the extracted heading can precede the rest of the left column.
        refs = extract(['Intro.\nReferences\nSee also Smith (2020) here.\nSmith, A. 2020, J, 1, 1'])
        self.assertEqual(refs[0]['citation_count'], 1)
        self.assertEqual(refs[0]['occurrences'][0]['page'], 1)

    def test_scanned_two_column_page_with_entries_above_the_heading_and_ibid_dashes(self):
        pages = ['Bak et al. 1988 and Dennis (1985) are cited.\n'
                 'Babcock, K. L., & Westervelt, R. M. 1990, Phys. Rev. Letters, 64, 2168 Bak, P., Tang, C., & Wiesenfeld, K. 1987, PRL, 59, 381\n'
                 ' . 1988, Phys. Rev. A, 38, 364 Dennis, B. R. 1985, Solar Phys., 100, 465\n'
                 'REFERENCES\n'
                 'Low, B. C. 1990, ARA&A, 28, 491']
        refs = extract(pages)
        self.assertEqual([r['label'] for r in refs],
                         ['Babcock 1990', 'Bak 1987', 'Bak 1988', 'Dennis 1985', 'Low 1990'])
        self.assertEqual([r['citation_count'] for r in refs], [0, 0, 1, 1, 0])

    def test_numeric_markers_tolerate_inner_spaces(self):
        pages = ['Known [ 1], range [2 – 3] and list [ 3, 1].\nReferences\n[1] One 2020.\n[2] Two 2021.\n[3] Three 2022.']
        self.assertEqual([r['citation_count'] for r in extract(pages)], [2, 1, 2])

    def test_author_list_suggestions_remain_ambiguous(self):
        ref = {'raw': 'Guarrasi, M., Reale, F., Orlando, S. 2014, A&A, 564, A48',
               'authors': 'Guarrasi', 'year': '2014'}
        documents = [
            {'id': 1, 'title': 'Unrelated title', 'authors': 'Guarrasi M., Reale F., Orlando S.',
             'year': None, 'metadata': {'candidates': {'year': [2014]}}},
            {'id': 2, 'title': 'Another title', 'authors': 'Guarrasi M., Reale F.', 'year': None},
            {'id': 3, 'title': 'Different paper', 'authors': 'Guarrasi M., Reale F.', 'year': 2015},
            {'id': 4, 'title': 'Wrong order', 'authors': 'Reale F., Guarrasi M.', 'year': 2014},
            {'id': 5, 'title': 'One author', 'authors': 'Guarrasi M.', 'year': 2014},
        ]
        self.assertEqual([d['id'] for d in local_candidates(ref, documents, 9)], [1, 2])
        self.assertEqual([d['id'] for d in local_candidates(ref, documents, 1)], [2])

    def test_unicode_and_multiword_author_suggestions(self):
        documents = [
            {'id': 1, 'title': 'Other', 'authors': 'Török T., Kliem B., Titov V.', 'year': 2004},
            {'id': 2, 'title': 'Other', 'authors': 'van Ballegooijen A. A., Berger M.', 'year': 2014},
            {'id': 3, 'title': 'Other', 'authors': 'Ballegooijen A. A., Berger M.', 'year': 2014},
        ]
        refs = [{'raw': 'Török, T., Kliem, B., & Titov, V. 2004, A&A, 413, L27',
                 'authors': 'Török', 'year': '2004'},
                {'raw': 'van Ballegooijen, A. A., & Berger, M. 2014, ApJ, 787, 87',
                 'authors': 'van Ballegooijen', 'year': '2014'}]
        self.assertEqual([d['id'] for d in local_candidates(refs[0], documents, 9)], [1])
        self.assertEqual([d['id'] for d in local_candidates(refs[1], documents, 9)], [2])

    def test_automatic_links_refresh_and_manual_link_wins(self):
        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
                self.documents = []

            def list_documents(self):
                return self.documents

            def get_document(self, document_id):
                return next(d for d in self.documents if d['id'] == document_id)

            def get_path(self, document_id, annotated=False):
                self.get_document(document_id)
                return 'paper.pdf'

        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            ref = {'raw': 'Smith, A., Jones, B. 2020', 'authors': 'Smith', 'label': 'Smith 2020', 'citation_count': 0,
                   'year': '2020', 'doi': '10.1234/example', 'local_document_id': None}
            library.documents = [{'id': 1, 'title': 'Paper', 'authors': 'Smith A., Jones B.',
                                  'year': 2020, 'doi': '10.1234/example'},
                                 {'id': 2, 'title': 'Other', 'authors': '', 'year': None}]
            analyzed = _store_analysis(library, 9, [dict(ref)])['references'][0]
            self.assertEqual((analyzed['local_document_id'], analyzed['local_match']), (1, 'doi'))
            self.assertIsNone(_load(library, 9)['references'][0]['local_document_id'])
            self.assertEqual(get(library, 9)['references'][0]['local_document_id'], 1)
            library.documents.append({**library.documents[0], 'id': 3})
            self.assertIsNone(get(library, 9)['references'][0]['local_document_id'])
            link(library, 9, '0', 2)
            library.documents.pop()
            self.assertEqual(_store_analysis(library, 9, [dict(ref)])['references'][0]['local_document_id'], 2)
            self.assertEqual(get(library, 9)['references'][0]['local_document_id'], 2)

    def test_publication_match_ambiguity_missing_and_manual_precedence(self):
        class Page:
            def extract_text(self):
                return 'A&A 564, A48 (2014)\nA paper title'

        class Reader:
            pages = [Page()]

        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
                self.documents = [{'id': 1, 'sha256': 'one', 'authors': 'Guarrasi M., Reale F.', 'title': 'Paper'},
                                  {'id': 2, 'sha256': 'two', 'authors': 'Other A.', 'title': 'Other'}]
                self.missing = set()
            def list_documents(self):
                return self.documents
            def get_document(self, document_id):
                return next(d for d in self.documents if d['id'] == document_id)
            def get_path(self, document_id, annotated=False):
                if document_id in self.missing:
                    raise FileNotFoundError
                return 'paper.pdf'

        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            ref = {'id': '0', 'raw': 'Guarrasi, M., Reale, F. 2014, A&A, 564, A48',
                   'authors': 'Guarrasi', 'year': '2014', 'doi': ''}
            _save(library, 9, {'references': [ref], 'status': 'partial',
                               'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
            with patch('paperassist.references.Pdf', return_value=_fake_pdf(Reader.pages)()) as reader:
                result = get(library, 9)['references'][0]
                self.assertEqual((result['local_document_id'], result['local_match']), (1, 'publication'))
                get(library, 9)
                self.assertEqual(reader.call_count, 2)
                library.documents.append({**library.documents[0], 'id': 3, 'sha256': 'three'})
                self.assertIsNone(get(library, 9)['references'][0]['local_document_id'])
                link(library, 9, '0', 2)
                self.assertEqual(get(library, 9)['references'][0]['local_document_id'], 2)
                library.missing.add(2)
                result = get(library, 9)['references'][0]
                self.assertTrue(result['local_missing'])
                self.assertIsNone(result['local_document_id'])
                self.assertEqual(result['manual_target_id'], 2)

    def test_publication_uses_first_author_from_header_when_metadata_is_unhelpful(self):
        class Page:
            def extract_text(self):
                return ('The Astrophysical Journal, 745:152 (21pp), 2012 February 1\n'
                        'A PAPER TITLE\nP. Antolin1, and L. Rouppe van der Voort')
        class Reader:
            pages = [Page()]
        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
            def list_documents(self):
                return [{'id': 2, 'sha256': 'paper', 'authors': 'IOP Publishing'}]
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            ref = {'id': '0', 'raw': 'Antolin, P., & Rouppe van der Voort, L. 2012, ApJ, 745, 152',
                   'authors': 'Antolin', 'year': '2012'}
            _save(library, 1, {'references': [ref], 'status': 'partial',
                               'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
            with patch('paperassist.references.Pdf', return_value=_fake_pdf(Reader.pages)()):
                self.assertEqual(get(library, 1)['references'][0]['local_document_id'], 2)

    def test_only_authors_the_user_typed_outrank_the_printed_header(self):
        class Page:
            def extract_text(self):
                return ('The Astrophysical Journal, 745:152 (21pp), 2012 February 1\n'
                        'A PAPER TITLE\nP. Antolin1, and L. Rouppe van der Voort')
        class Reader:
            pages = [Page()]
        class Library(ReferenceTestLibrary):
            def __init__(self, path, metadata):
                super().__init__(path)
                self.metadata = metadata
            def list_documents(self):
                return [{'id': 2, 'sha256': 'paper', 'authors': 'IOP Publishing', 'metadata_status': 'corrected',
                         'metadata': self.metadata}]
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
        ref = {'id': '0', 'raw': 'Antolin, P., & Rouppe van der Voort, L. 2012, ApJ, 745, 152', 'authors': 'Antolin', 'year': '2012'}
        for manual, expected in ((['title'], 2), (['authors'], None)):
            with (self.subTest(manual=manual), TemporaryDirectory() as directory,
                  Library(Path(directory) / 'library.sqlite', {'manual': manual}) as library):
                _save(library, 1, {'references': [dict(ref)], 'status': 'partial',
                                   'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
                with patch('paperassist.references.Pdf', return_value=_fake_pdf(Reader.pages)()):
                    self.assertEqual(get(library, 1)['references'][0]['local_document_id'], expected)

    def test_manual_links_shared_only_when_reference_and_target_are_unique(self):
        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
            def list_documents(self):
                return [{'id': 2}, {'id': 3}]
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'

        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            ref = {'id': '0', 'raw': 'Smith, A., Jones, B. 2020', 'authors': 'Smith', 'year': '2020'}
            payload = {'references': [ref], 'status': 'partial', 'destination_version': DESTINATION_VERSION,
                       'extraction_version': EXTRACTION_VERSION}
            _save(library, 1, payload)
            _save(library, 4, {'references': [{**ref, 'manual_link': True, 'local_document_id': 2}]})
            result = get(library, 1)['references'][0]
            self.assertEqual((result['local_document_id'], result['local_match']), (2, 'shared_manual'))
            _save(library, 5, {'references': [{**ref, 'manual_link': True, 'local_document_id': 3}]})
            self.assertIsNone(get(library, 1)['references'][0]['local_document_id'])
            _save(library, 4, {'references': [{**ref, 'manual_link': True, 'local_document_id': 4}]})
            _save(library, 5, {'references': []})
            self.assertIsNone(get(library, 1)['references'][0]['local_document_id'])

    def test_self_link_is_preserved_as_invalid_and_can_be_reset(self):
        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
            def list_documents(self):
                return [{'id': 1}, {'id': 2, 'doi': '10.1234/example'}]
            def get_document(self, document_id):
                return next(d for d in self.list_documents() if d['id'] == document_id)
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'

        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            ref = {'id': '0', 'raw': 'Smith, A. 2020', 'authors': 'Smith', 'year': '2020',
                   'doi': '10.1234/example', 'manual_link': True, 'local_document_id': 1}
            _save(library, 1, {'references': [ref], 'status': 'partial',
                               'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
            invalid = get(library, 1)['references'][0]
            self.assertTrue(invalid['local_invalid'])
            self.assertEqual(invalid['manual_target_id'], 1)
            self.assertIsNone(invalid['local_document_id'])
            with self.assertRaisesRegex(ValueError, 'source document'):
                link(library, 1, '0', 1)
            reset = link(library, 1, '0', None)
            self.assertEqual((reset['local_document_id'], reset['local_match']), (2, 'doi'))
            self.assertNotIn('manual_link', reset)

    def test_missing_current_revision_is_not_available(self):
        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
            def list_documents(self):
                return [{'id': 2, 'doi': '10.1234/example'}]
            def get_path(self, document_id, annotated=True):
                if annotated:
                    raise FileNotFoundError('Missing saved revision')
                return 'original.pdf'

        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            ref = {'id': '0', 'raw': 'Smith, A. 2020', 'authors': 'Smith', 'year': '2020',
                   'doi': '10.1234/example'}
            _save(library, 1, {'references': [ref], 'status': 'partial',
                               'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
            self.assertIsNone(get(library, 1)['references'][0]['local_document_id'])

    def test_named_destinations_use_geometry_and_unique_author_year(self):
        class Destination:
            typ, left = '/XYZ', 43
            def __init__(self, top):
                self.top = top

        class Page:
            def extract_text(self, visitor_text):
                for text, y in [('Golub, L., Alpha, M., Other, B. 1980', 516.8), ('Guarrasi, M., Reale, F., Orlando, S. 2014', 507.7),
                                ('Unknown entry without year', 498.7), ('Brown, A., Baker, B., Other, C. 2001', 489.7),
                                ('Smith, A., Other, A. 2005a', 480), ('Smith, B., Other, B. 2005a', 470)]:
                    visitor_text(text, [1, 0, 0, 1, 0, 0], [8, 0, 0, 8, 43, y], None, 1)

        class Reader:
            pages = [Page()]
            named_destinations = {'golub': Destination(526), 'bib31': Destination(517),
                                  'unknown': Destination(508), 'brown': Destination(499),
                                  'bib32': Destination(489),
                                  'figure': Destination(700)}
            def get_destination_page_number(self, destination):
                return 0

        refs = [{'raw': 'Guarrasi, M., Reale, F., Orlando, S. 2014'}, {'raw': 'Golub, L., Alpha, M., Other, B. 1980'},
                {'raw': 'Brown, A., Baker, B., Other, C. 2001'},
                {'raw': 'Smith, A., Other, A. 2005a'}, {'raw': 'Smith, A., Other, A. 2005a'}]
        map_destinations(Reader(), ['References'], refs)
        self.assertEqual(refs[0]['destinations'], ['bib31'])
        self.assertEqual(refs[1]['destinations'], ['golub'])
        self.assertEqual(refs[2]['destinations'], ['brown'])
        self.assertTrue(all(not ref['destinations'] for ref in refs[3:]))

    def test_short_complete_entry_gets_its_destination_but_short_prefix_does_not(self):
        class Destination:
            typ, left = '/XYZ', 43
            def __init__(self, top):
                self.top = top

        class Page:
            def extract_text(self, visitor_text):
                for text, y in [('Reale, F. 2014, LRSP, 11, 4', 516.8), ('Short, A. 1999, ApJ,', 507.7)]:
                    visitor_text(text, [1, 0, 0, 1, 0, 0], [8, 0, 0, 8, 43, y], None, 1)

        class Reader:
            pages = [Page()]
            named_destinations = {'bib74': Destination(526), 'wrapped': Destination(517)}
            def get_destination_page_number(self, destination):
                return 0

        refs = [{'raw': 'Reale, F. 2014, LRSP, 11, 4'}, {'raw': 'Short, A. 1999, ApJ, 512, 377'}]
        map_destinations(Reader(), ['References'], refs)
        self.assertEqual(refs[0]['destinations'], ['bib74'])
        self.assertEqual(refs[1]['destinations'], [])

    def test_numbered_destination_strips_the_printed_label_and_checks_it(self):
        class Destination:
            typ, left = '/XYZ', 77
            def __init__(self, top):
                self.top = top

        class Page:
            def extract_text(self, visitor_text):
                for text, y in [('[1] Ada Lovelace. A study of parallel computation. 2024.', 500),
                                ('[2] Grace Hopper. A study of compilation. 2023.', 470),
                                ('[9] Grace Hopper. A study of compilation. 2023.', 440)]:
                    visitor_text(text, [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 77, y], None, 11)

        class Reader:
            pages = [Page()]
            named_destinations = {'cite.parallel': Destination(513), 'cite.compilers': Destination(483),
                                  'cite.wrong-label': Destination(453)}
            def get_destination_page_number(self, destination):
                return 0

        refs = [{'label': '1', 'raw': 'Ada Lovelace. A study of parallel computation. 2024.'},
                {'label': '2', 'raw': 'Grace Hopper. A study of compilation. 2023.'}]
        map_destinations(Reader(), ['References'], refs)
        self.assertEqual(refs[0]['destinations'], ['cite.parallel'])
        self.assertEqual(refs[1]['destinations'], ['cite.compilers'])

    def test_cached_analysis_migrates_once_preserving_manual_link(self):
        class Page:
            def extract_text(self, **kwargs):
                return 'References'

        class Reader:
            pages = [Page()]
            named_destinations = {}

        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
            def list_documents(self):
                return [{'id': 3}]

        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            ref = {'raw': 'Smith, A. 2020', 'authors': 'Smith', 'year': '2020',
                   'manual_link': True, 'local_document_id': 3}
            _save(library, 1, {'references': [ref], 'status': 'partial'})
            with patch('paperassist.references.Pdf', return_value=_fake_pdf(Reader.pages)()) as reader:
                result = get(library, 1)['references'][0]
                self.assertEqual(result['local_document_id'], 3)
                self.assertEqual(result['destinations'], [])
                get(library, 1)
                reader.assert_called_once()

    def test_destination_full_line_disambiguates_and_normalizes(self):
        class Destination:
            typ, left = '/XYZ', 43
            def __init__(self, top):
                self.top = top

        class Page:
            def extract_text(self, visitor_text):
                fragments = [('Rappazzo, A. F., & Parker, E. N. 2013, ApJ', 43, 500),
                             ('Other column 2013', 318, 500),
                             ('Rappazzo, A. F., & Velli, M. 2013, ApJ', 43, 490),
                             ('Fisher, G., Can', 43, 480), ('ﬁeld, R. 1985, ApJ', 105, 480),
                             ('Müller, D., Hansteen, V., Peter, H. 2003, A&A', 43, 470),
                             ('Smith, A., Jones, B. 2010', 43, 460)]
                for text, x, y in fragments:
                    visitor_text(text, [1, 0, 0, 1, 0, 0], [8, 0, 0, 8, x, y], None, 1)

        class Reader:
            pages = [Page()]
            named_destinations = {str(i): Destination(y + 9) for i, y in enumerate([500, 490, 480, 470, 460])}
            def get_destination_page_number(self, destination):
                return 0

        refs = [{'raw': 'Rappazzo, A. F., & Parker, E. N. 2013, ApJ, 1'},
                {'raw': 'Rappazzo, A. F., & Velli, M. 2013, ApJ, 2'},
                {'raw': 'Fisher, G., Canfield, R. 1985, ApJ, 3'},
                {'raw': 'Mu\u0308ller, D., Hansteen, V., Peter, H. 2003, A&A, 4'},
                {'raw': 'Smith, A., Jones, B. 2010, ApJ, 5'},
                {'raw': 'Smith, A., Jones, B. 2010, ApJ, 6'}]
        map_destinations(Reader(), ['References'], refs)
        self.assertEqual([ref['destinations'] for ref in refs], [['0'], ['1'], ['2'], ['3'], [], []])

    def test_reanalysis_preserves_split_manual_link_and_unmatched_state(self):
        class Page:
            def extract_text(self, **kwargs):
                return ('References\nTesta, P., & Reale, F. 2012, ApJ, 750, L10\n'
                        'Török, T., Kliem, B., & Titov, V. 2004, A&A, 413, L27')

        class Reader:
            pages = [Page()]
            named_destinations = {}

        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
            def list_documents(self):
                return [{'id': 3}, {'id': 4}]

        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            old = {'id': '0', 'label': 'Testa 2012', 'authors': 'Testa', 'year': '2012',
                   'raw': 'Testa, P., & Reale, F. 2012, ApJ, 750, L10 Török, T., Kliem, B., & Titov, V. 2004, A&A, 413, L27',
                   'manual_link': True, 'local_document_id': 3, 'retrieval': {'status': 'found'}}
            missing = {'id': '1', 'label': 'Lost 2001', 'authors': 'Lost', 'year': '2001',
                       'raw': 'Lost, A. 2001', 'manual_link': True, 'local_document_id': 4}
            _save(library, 1, {'references': [old, missing], 'status': 'partial'})
            with patch('paperassist.references.Pdf', return_value=_fake_pdf(Reader.pages)()) as reader:
                refs = get(library, 1)['references']
                by_label = {ref['label']: ref for ref in refs}
                self.assertEqual(by_label['Testa 2012']['local_document_id'], 3)
                self.assertEqual(by_label['Testa 2012']['retrieval']['status'], 'found')
                self.assertTrue(by_label['Lost 2001']['legacy'])
                self.assertEqual(by_label['Lost 2001']['status'], 'Previous reference — verify link')
                self.assertEqual(by_label['Lost 2001']['local_document_id'], 4)
                self.assertEqual(by_label['Török 2004']['authors'], 'Török')
                get(library, 1)
                reader.assert_called_once()


class FakeLibrary(ReferenceTestLibrary):
    """Minimal library whose first pages are described by `pages` (sha -> text)."""

    def __init__(self, path, documents, pages=None):
        super().__init__(path)
        self.documents, self.pages = documents, pages or {}
        self.verified = []

    def list_documents(self):
        return self.documents

    def get_document(self, document_id):
        return next(d for d in self.documents if d['id'] == document_id)

    def get_path(self, document_id, annotated=False):
        return self.get_document(document_id)['sha256']

    def apply_verified(self, document_id, record, filename=None):
        self.verified.append((document_id, record['doi'], filename))


class FakeReader:
    def __init__(self, data):
        key = data.decode() if isinstance(data, (bytes, bytearray)) else data.read().decode()
        text = FakeLibrary.pages[key]
        self.pages = [type('Page', (), {'extract_text': lambda self: text})()]
        self.named_destinations = {}

    def close(self):
        pass


FakeLibrary.pages = {}
BROWNING = {'id': 28, 'sha256': 'browning', 'title': 'Heating the corona by nanoflares', 'year': None,
            'authors': 'Browning P. K., Gerrard C., Hood A. W., Kevis R., Van der Linden R. A. M.',
            'metadata': {'candidates': {'year': [2008]}}}
GERRARD = {'id': '0', 'raw': 'Gerrard, C. L., Arber, T. D., Hood, A. W., & Van der Linden, R. A. M. 2001, A&A, 373, 1089',
           'authors': 'Gerrard', 'year': '2001', 'doi': ''}


class ConflictTests(unittest.TestCase):
    def setUp(self):
        FakeLibrary.pages = {'browning': 'A&A 485, 837-848 (2008)\nHeating the corona by nanoflares',
                             'guarrasi': 'A&A 564, A48 (2014)\nMHD modeling of coronal loops',
                             'other': 'No journal line here'}
        patcher = patch('paperassist.references.Pdf', side_effect=lambda data: FakeReader(data))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def library(self, *documents):
        library = FakeLibrary(Path(self.directory.name) / 'library.sqlite', list(documents), FakeLibrary.pages)
        return self.enterContext(library)

    def save(self, library, *refs):
        _save(library, 9, {'references': list(refs), 'status': 'partial', 'destination_version': DESTINATION_VERSION,
                           'extraction_version': EXTRACTION_VERSION})

    def test_concurrent_link_and_retrieval_preserve_both_changes(self):
        library = self.library(BROWNING)
        other = self.library(BROWNING)
        self.save(library, dict(GERRARD))
        saving, release, linked = threading.Event(), threading.Event(), threading.Event()
        def save(library, document_id, payload):
            if 'retrieval' in payload['references'][0] and not payload['references'][0].get('manual_link'):
                saving.set()
                if not release.wait(3):
                    raise TimeoutError('Concurrent reference update did not finish')
            return _save(library, document_id, payload)
        def link_reference():
            try:
                return link(other, 9, '0', 28)
            finally:
                linked.set()
        with ThreadPoolExecutor(max_workers=2) as executor, patch('paperassist.references._save', side_effect=save):
            first = executor.submit(set_retrieval, library, 9, '0', {'status': 'not_found'})
            self.assertTrue(saving.wait(3))
            second = executor.submit(link_reference)
            linked.wait(.1)
            release.set()
            first.result(timeout=3)
            second.result(timeout=3)
        ref = get(library, 9)['references'][0]
        self.assertEqual(ref['retrieval']['status'], 'not_found')
        self.assertTrue(ref['manual_link'])
        self.assertEqual(ref['manual_target_id'], 28)

    def test_pdf_extraction_does_not_block_reads_or_links_and_merges_latest_state(self):
        library = self.library(BROWNING, {'id': 9, 'sha256': 'other'})
        self.save(library, dict(GERRARD))
        extracting, release = threading.Event(), threading.Event()
        def extract_references(pages, heading):
            extracting.set()
            if not release.wait(3):
                raise TimeoutError('PDF extraction was not released')
            return [{**GERRARD, 'label': 'Gerrard 2001', 'citation_count': 0}]
        def change_reference():
            self.assertEqual(get(library, 9)['references'][0]['id'], '0')
            link(library, 9, '0', 28)
            set_retrieval(library, 9, '0', {'status': 'not_found'})
        with ThreadPoolExecutor(max_workers=2) as executor, \
             patch('paperassist.references._extract_references', side_effect=extract_references):
            analysis = executor.submit(analyze, library, 9)
            try:
                self.assertTrue(extracting.wait(3))
                executor.submit(change_reference).result(timeout=1)
            finally:
                release.set()
            ref = analysis.result(timeout=3)['references'][0]
        self.assertEqual(ref['manual_target_id'], 28)
        self.assertEqual(ref['retrieval']['status'], 'not_found')

    def test_repair_lookup_does_not_block_updates_or_overwrite_latest_state(self):
        FakeLibrary.pages['browning'] += '\nP. K. Browning'
        for changed_target in (False, True):
            with self.subTest(changed_target=changed_target):
                library = self.library({**BROWNING, 'filename': 'download.pdf', 'metadata_status': 'unresolved'},
                                       {'id': 29, 'sha256': 'other', 'filename': 'named.pdf',
                                        'metadata_status': 'corrected'})
                self.save(library, {**GERRARD, 'manual_link': True, 'local_document_id': 28},
                          {**GERRARD, 'id': '1', 'raw': 'Other, A. 2002, Journal, 2, 33'})
                identifying, release = threading.Event(), threading.Event()
                def find(ref):
                    identifying.set()
                    if not release.wait(3):
                        raise TimeoutError('Identity lookup was not released')
                    return 'found', '', {'doi': '10.1234/browning', 'first_author': 'Browning', 'year': 2008}
                def update_references():
                    set_retrieval(library, 9, '0', {'status': 'not_found'})
                    link(library, 9, '0' if changed_target else '1', 29)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    repair = executor.submit(repair_downloads, library, 9, find)
                    try:
                        self.assertTrue(identifying.wait(3))
                        executor.submit(update_references).result(timeout=1)
                    finally:
                        release.set()
                    report = repair.result(timeout=3)
                refs = {ref['id']: ref for ref in get(library, 9)['references']}
                self.assertEqual(refs['0']['retrieval']['status'], 'not_found')
                self.assertEqual(refs['0' if changed_target else '1']['manual_target_id'], 29)
                self.assertEqual(report[0]['status'], 'skipped' if changed_target else 'repaired')
                self.assertEqual(len(library.verified), 0 if changed_target else 1)

    def test_repair_rechecks_document_eligibility_after_lookup(self):
        FakeLibrary.pages['browning'] += '\nP. K. Browning'
        document = {**BROWNING, 'filename': 'download.pdf', 'metadata_status': 'unresolved'}
        library = self.library(document)
        self.save(library, {**GERRARD, 'manual_link': True, 'local_document_id': 28})
        def find(ref):
            document['metadata_status'] = 'corrected'
            return 'found', '', {'doi': '10.1234/browning', 'first_author': 'Browning', 'year': 2008}
        report = repair_downloads(library, 9, find)
        self.assertEqual(report[0]['status'], 'skipped')
        self.assertEqual(library.verified, [])
        self.assertNotIn('identity', get(library, 9)['references'][0])

    def test_unmatched_and_rematched_references_keep_stable_ids(self):
        library = self.library(BROWNING, {'id': 9, 'sha256': 'other'})
        ref = {**GERRARD, 'id': 'legacy-3', 'legacy': True, 'manual_link': True,
               'local_document_id': 28, 'retrieval': {'status': 'found'}}
        self.save(library, ref)
        with patch('paperassist.references._analyze_pdf', return_value=[]):
            unmatched = analyze(library, 9)['references'][0]
        self.assertEqual(unmatched['id'], 'legacy-3')
        with patch('paperassist.references._analyze_pdf', return_value=[{
                **GERRARD, 'id': '0', 'label': 'Gerrard 2001', 'citation_count': 0}]):
            matched = analyze(library, 9)['references'][0]
        self.assertEqual(matched['id'], 'legacy-3')
        self.assertNotIn('legacy', matched)
        self.assertTrue(matched['manual_link'])
        self.assertEqual(matched['retrieval']['status'], 'found')

    def test_reanalysis_add_remove_reorder_never_reuses_reference_ids(self):
        library = self.library(BROWNING, {'id': 9, 'sha256': 'other'})
        rows = {}
        for name in ('Alpha', 'Beta', 'Gamma', 'Delta'):
            rows[name] = {**GERRARD, 'raw': f'{name}, A. 2001, Journal, 1, 22',
                          'authors': name, 'label': f'{name} 2001', 'citation_count': 0}
        def run(*names):
            with patch('paperassist.references._analyze_pdf', return_value=[dict(rows[name]) for name in names]):
                refs = analyze(library, 9)['references']
            self.assertEqual(len({ref['id'] for ref in refs}), len(refs))
            return {ref['authors']: ref for ref in refs}
        initial = run('Alpha', 'Beta')
        set_retrieval(library, 9, initial['Alpha']['id'], {'status': 'not_found'})
        changed = run('Beta', 'Gamma')
        self.assertEqual(changed['Beta']['id'], initial['Beta']['id'])
        self.assertEqual(changed['Alpha']['id'], initial['Alpha']['id'])
        self.assertTrue(changed['Alpha']['legacy'])
        reordered = run('Gamma', 'Beta', 'Alpha')
        self.assertEqual({name: ref['id'] for name, ref in reordered.items()},
                         {name: ref['id'] for name, ref in changed.items()})
        self.assertNotIn('legacy', reordered['Alpha'])
        run('Alpha')
        added = run('Alpha', 'Delta')
        self.assertNotIn(added['Delta']['id'], {ref['id'] for ref in changed.values()})
        run()
        # Alpha's acquisition record remains, but even discarded rows keep their IDs reserved.
        last = run('Beta')
        self.assertNotEqual(last['Beta']['id'], initial['Beta']['id'])

    def test_old_preserved_row_without_id_does_not_collide_with_legacy_id(self):
        library = self.library(BROWNING, {'id': 9, 'sha256': 'other'})
        without_id = {**GERRARD, 'raw': 'Other, A. 2002, Journal, 2, 33',
                      'retrieval': {'status': 'not_found'}}
        without_id.pop('id')
        self.save(library, without_id, {**GERRARD, 'id': 'legacy-0',
                                      'retrieval': {'status': 'not_found'}})
        with patch('paperassist.references._analyze_pdf', return_value=[]):
            refs = analyze(library, 9)['references']
        self.assertEqual(len({ref['id'] for ref in refs}), 2)
        self.assertIn('legacy-0', {ref['id'] for ref in refs})

    def test_wrong_manual_link_is_kept_but_flagged(self):
        library = self.library(BROWNING)
        self.save(library, {**GERRARD, 'manual_link': True, 'local_document_id': 28})
        ref = get(library, 9)['references'][0]
        self.assertEqual((ref['local_document_id'], ref['manual_link']), (28, True))
        self.assertEqual(ref['local_conflict'], 'Linked PDF is A&A 485, 837 (2008); reference is A&A 373, 1089 (2001)')

    def test_matching_manual_link_is_not_flagged(self):
        library = self.library(BROWNING)
        ref = {**GERRARD, 'raw': 'Browning, P. K., Gerrard, C. 2008, A&A, 485, 837', 'year': '2008',
               'authors': 'Browning', 'manual_link': True, 'local_document_id': 28}
        self.save(library, ref)
        self.assertNotIn('local_conflict', get(library, 9)['references'][0])

    def test_confirmed_doi_and_year_contradictions_are_flagged(self):
        library = self.library({'id': 2, 'sha256': 'other', 'doi': '10.1/a', 'year': 2010})
        self.save(library, {**GERRARD, 'doi': '10.1/b', 'manual_link': True, 'local_document_id': 2})
        self.assertIn('DOI 10.1/a', get(library, 9)['references'][0]['local_conflict'])
        library = self.library({'id': 2, 'sha256': 'other', 'year': 2010})
        self.save(library, {**GERRARD, 'manual_link': True, 'local_document_id': 2})
        self.assertIn('from 2010', get(library, 9)['references'][0]['local_conflict'])

    def test_candidates_exclude_documents_whose_publication_contradicts_the_reference(self):
        evidence = lambda document: {'publication': ['A&A', '485', '837', '2008']}
        document = {**BROWNING, 'metadata': {}}
        self.assertEqual([d['id'] for d in local_candidates(GERRARD, [document], 9)], [28])
        self.assertEqual(local_candidates(GERRARD, [document], 9, evidence), [])
        self.assertEqual(local_candidates(GERRARD, [BROWNING], 9), [])  # the first-page year disagrees too

    def test_candidates_exclude_first_page_years_far_from_the_reference_year(self):
        document = {**BROWNING, 'metadata': {'candidates': {'year': [2007, 2008]}}}
        self.assertEqual(local_candidates({**GERRARD, 'year': '2001'}, [document], 9), [])
        self.assertEqual(len(local_candidates({**GERRARD, 'year': '2009'}, [document], 9)), 1)

    def test_candidates_prefer_documents_led_by_the_first_author(self):
        ref = {'raw': 'Guarrasi, M., Reale, F. 2014, A&A, 564, A48', 'authors': 'Guarrasi', 'year': '2014'}
        documents = [{'id': 1, 'title': 'x', 'authors': 'Peres G., Guarrasi M., Reale F.', 'year': 2014},
                     {'id': 2, 'title': 'y', 'authors': 'Guarrasi M., Reale F.', 'year': 2014}]
        self.assertEqual([d['id'] for d in local_candidates(ref, documents, 9)], [2, 1])

    def test_first_page_doi_links_only_without_contradiction(self):
        paper = {'id': 3, 'sha256': 'guarrasi', 'authors': 'Guarrasi M., Reale F.', 'title': 'Paper',
                 'metadata': {'candidates': {'doi': ['10.1051/0004-6361/201322439']}}}
        ref = {'id': '0', 'raw': 'Guarrasi, M., Reale, F. 2014, A&A, 564, A48', 'authors': 'Guarrasi', 'year': '2014',
               'doi': '10.1051/0004-6361/201322439'}
        library = self.library(paper)
        self.save(library, ref)
        found = get(library, 9)['references'][0]
        self.assertEqual((found['local_document_id'], found['local_match']), (3, 'first_page_doi'))
        FakeLibrary.pages['revised'] = 'A&A 570, A1 (2014)\nMHD modeling'
        library = self.library({**paper, 'sha256': 'revised'})
        self.assertIsNone(get(library, 9)['references'][0]['local_document_id'])

    def test_retrieval_identity_is_stored_and_supplies_the_doi(self):
        library = self.library(BROWNING)
        self.save(library, {**GERRARD})
        identity = {'doi': '10.1051/0004-6361:20010080', 'title': 'T', 'provider': 'crossref'}
        set_retrieval(library, 9, '0', {'version': 2, 'status': 'not_found', 'summary': 'x', 'attempts': [], 'identity': identity})
        ref = get(library, 9)['references'][0]
        self.assertEqual((ref['doi'], ref['identity']), (identity['doi'], identity))
        self.assertNotIn('identity', ref['retrieval'])
        self.assertEqual((ref['retrieval']['version'], ref['status']), (2, 'not_found'))

    def test_repair_names_only_unnamed_documents_whose_first_author_is_on_the_page(self):
        # Small-caps author lines extract with a split surname.
        FakeLibrary.pages['download'] = 'Nanoflares in a twisted loop\nA. F. R APPAZZO 1, M. V ELLI 2'
        FakeLibrary.pages['elsewhere'] = 'Something unrelated'
        named = {'id': 70, 'sha256': 'download', 'filename': 'download.pdf', 'metadata_status': 'needs_review'}
        elsewhere = {'id': 71, 'sha256': 'elsewhere', 'filename': 'download.pdf', 'metadata_status': 'needs_review'}
        corrected = {'id': 72, 'sha256': 'other', 'filename': 'download.pdf', 'metadata_status': 'corrected'}
        library = self.library(named, elsewhere, corrected)
        identity = {'doi': '10.1088/x', 'year': 2013, 'first_author': 'Rappazzo', 'publication': ['APJ', '771', '76', '2013']}
        refs = [{'id': str(i), 'raw': f'Rappazzo, A. F. 2013, ApJ, 771, 7{i}', 'authors': 'Rappazzo', 'year': '2013',
                 'identity': identity, 'manual_link': True, 'local_document_id': target}
                for i, target in enumerate((70, 71, 72))]
        self.save(library, *refs)
        report = repair_downloads(library, 9)
        self.assertEqual([item['status'] for item in report], ['repaired', 'skipped'])
        self.assertEqual(library.verified, [(70, '10.1088/x', 'Rappazzo_2013_ApJ_771_76.pdf')])


WINEBARGER = 'Winebarger, A. R., Schmelz, J. T., Warren, H. P., Hulburt, E. O., Saar, S. H., Kashyap, V. L., 2011, Submitted.'
CAPTIONS = ('Figure Captions\nFigure 1 Active region observed on 2010 June 19 using SDO/AIA. All 6 images are coaligned.\n'
            'Figure 2 Normalized SDO/AIA temperature response functions (Smith 2020).')


def raws(pages):
    return [ref['raw'] for ref in extract(pages)]


class BibliographyBoundaryTests(unittest.TestCase):
    def test_real_bibliography_takes_precedence_over_a_table_of_contents_heading(self):
        refs = extract(['Contents\n1 Introduction\n2 References\n22',
                        'References\nProject Alpha (2020)\nA table of prior work.',
                        'Brown (2019) points to this result.\nReferences\n'
                        'Brown, C. 2019, A&A, 1, 2\nWhite, D. 2018, A&A, 3, 4'])
        self.assertEqual([ref['label'] for ref in refs], ['Brown 2019', 'White 2018'])
        self.assertEqual(refs[0]['citation_count'], 1)

    def test_wrapped_book_volume_is_not_a_duplicate_numbered_entry(self):
        refs = extract(['References\n[1] A. Alpha, Methods of analysis, vol.\n'
                        '1. New York: Example Press, 1983.\n[2] B. Beta, ApJ, 2, 3, 1984.'])
        self.assertEqual([ref['label'] for ref in refs], ['1', '2'])
        self.assertIn('vol. 1. New York', refs[0]['raw'])

    def test_running_header_glued_to_the_next_reference(self):
        refs = extract(['References\nBrown, C. 2019, A&A, 1, 2',
                        'Brown et al.: A&A, 1, 2 (2024)Ḱeppens, R. 2012, ApJ, 5, 6\n'
                        'Nistic`o, G. 2013, A&A, 552, A57'])
        self.assertEqual([ref['label'] for ref in refs], ['Brown 2019', 'Ḱeppens 2012', 'Nisticò 2013'])
        self.assertNotIn('2024', refs[1]['raw'])

    def test_references_glued_to_a_thesis_or_page_number_are_separate(self):
        refs = extract(['References\nSmith, A. 2010, PhD thesisM\u0308uller, D. 2011, ApJ, 1, 2'
                        'López Fuentes, M. C. 2012, ApJ, 3, 4'])
        self.assertEqual([ref['label'] for ref in refs], ['Smith 2010', 'M\u0308uller 2011', 'López Fuentes 2012'])
        self.assertEqual(refs[0]['raw'], 'Smith, A. 2010, PhD thesis')

    def test_bibliography_retry_breaks_publication_key_tie_with_distinct_author_year_entries(self):
        from paperassist.references import _bibliography_keys, _better_bibliography

        prefix = 'References\nAlpha, A. 2000, ApJ, 10, 1\n'
        visual = extract([prefix + 'Lin, N. 2013, Sol. Phys., 226, 239'])
        retry = extract([prefix + 'Lin, N. 2005, Sol. Phys., 226, 239\nLionello, F. 2013, ÁpJ, 773, 134'])
        self.assertEqual(len(_bibliography_keys(visual)), len(_bibliography_keys(retry)))
        self.assertTrue(_better_bibliography(visual, retry))

    def test_entry_stops_before_figure_captions_that_follow_the_list(self):
        refs = extract(['Body cites Winebarger et al. 2011 and Smith (2020).\nReferences\n'
                        'Smith, A., Jones, B. 2020, ApJ, 745, 152\n' + WINEBARGER + '\n' + CAPTIONS])
        self.assertEqual([ref['raw'] for ref in refs], ['Smith, A., Jones, B. 2020, ApJ, 745, 152', WINEBARGER])
        # Captions after the list are still not body text, so "Smith 2020" there adds no citation.
        self.assertEqual(refs[0]['citation_count'], 1)

    def test_headings_and_labels_after_the_list_end_it(self):
        for heading in ('Tables', 'TABLE 1. Loop parameters', 'Table II', 'Fig. 6.— Light curves', 'Figure Legends',
                        'Appendix A: Derivation', 'Supplementary material', 'Acknowledgements', 'Acknowledgments. We thank',
                        '7. ACRONYMS', 'List of Figures', 'Data Availability'):
            with self.subTest(heading):
                entries = raws(['References\nBrown, C. 2019, A&A, 1, 2\nWhite, D. 2018, A&A, 3, 4\n' + heading + '\nSmith, E. 2017, ApJ, 5, 6'])
                # A caption only closes the entry before it; a section heading ends the list for good.
                resumes = heading.startswith(('Fig.', 'TABLE ', 'Table '))
                self.assertEqual(len(entries), 3 if resumes else 2, heading)
                self.assertEqual(entries[1], 'White, D. 2018, A&A, 3, 4')

    def test_a_wrapped_title_that_starts_like_a_heading_stays_in_its_entry(self):
        pages = ['References\nGradshteyn, I. S., & Ryzhik, I. M. 2007, in\nTable of integrals and products\n(New York: Academic Press)\n'
                 'Brown, C. 2019, A&A, 1, 2']
        self.assertEqual(len(raws(pages)), 2)
        self.assertIn('Table of integrals', raws(pages)[0])
        pages = ['References\nBrown, C. 2019, A&A, 1, 2\nWhite, D. 2018, Figures of merit for\nsolar loops. ApJ, 3, 4']
        self.assertEqual(raws(pages)[1], 'White, D. 2018, Figures of merit for solar loops. ApJ, 3, 4')

    def test_figures_floating_between_reference_pages_do_not_end_the_list(self):
        pages = ['Text.\nReferences\nBaty, H. 2000, A&A, 353, 1074\nFigure 14. As Figure 10(a), for the\nsimulation with constant resistivity.\n'
                 '14 The Astrophysical Journal, 830:21 (15pp), 2016 October 10 Reale et al.',
                 'Bradshaw, S., & Cargill, P. 2006, A&A, 458, 987\nReale, F., & Landi, E. 2012, A&A, 543, A90']
        self.assertEqual(raws(pages), ['Baty, H. 2000, A&A, 353, 1074', 'Bradshaw, S., & Cargill, P. 2006, A&A, 458, 987',
                                       'Reale, F., & Landi, E. 2012, A&A, 543, A90'])

    def test_prose_after_a_caption_does_not_start_an_entry(self):
        pages = ['References\nBaty, H. 2000, A&A, 353, 1074\nFigure 3. Snapshots of the strands; the mixing, i.e. the\n'
                 'appearance, i.e. affecting only the nearest neighbors of each strand.\nde Groof, A. 2004, A&A, 415, 1141']
        self.assertEqual(raws(pages), ['Baty, H. 2000, A&A, 353, 1074', 'de Groof, A. 2004, A&A, 415, 1141'])

    def test_acknowledgements_before_the_list_remain_body_text(self):
        pages = ['Acknowledgements\nWe thank Brown (2019) for the code.\nReferences\nBrown, C. 2019, A&A, 1, 2']
        self.assertEqual(extract(pages)[0]['citation_count'], 1)

    def test_pages_of_axis_ticks_are_not_part_of_the_list(self):
        axis = '– 21 –\n0 20 40 60\nTime [min]\n105\n106\n107\nTemperature\n(K)\n0\n1\n2\n3\n4'
        entries = raws(['References\nBrown, C. 2019, A&A, 1, 2\nWhite, D. 2018, A&A, 3, 4\nOpen entry 2017, Notes', axis,
                        'Smith, E. 2016, ApJ, 5, 6'])
        self.assertNotIn('Temperature', ' '.join(entries))
        self.assertEqual(entries[-1], 'Smith, E. 2016, ApJ, 5, 6')


class CompleteCitationTests(unittest.TestCase):
    def test_a_finished_citation_ignores_headers_and_stray_lines(self):
        pages = ['References\nTam, A. 2015, A&A, 580, A122\nArticle number, page 15 of 16\nWarren, H. P. 2011, ApJ, 73 4, 90\n'
                 'This preprint was prepared with the AAS LATEX macros v5.2.\n– 31 –\n'
                 'Zhou, Y. 2017, ApJ, 839, 9 (21pp), 2017\n27 ( a ) ( c ) ( b )']
        self.assertEqual(raws(pages), ['Tam, A. 2015, A&A, 580, A122', 'Warren, H. P. 2011, ApJ, 73 4, 90',
                                       'Zhou, Y. 2017, ApJ, 839, 9 (21pp), 2017'])

    def test_every_kind_of_terminal_citation_closes_the_entry(self):
        for entry in ('Smith, A. 2020, Journal, 4, 2020', 'Smith, A. 2020, Phys. Rev. Lett. 99, 1 (2020).',
                      'Smith, A. 2020, doi:10.1086/123456', 'Smith, A. 2020, arXiv:2001.01234 [astro-ph.SR]',
                      'Smith, A. 2020, ApJ, in press', 'Smith, A. 2020, submitted to ApJ', 'Smith, A. 2020, in prep.',
                      'Smith, A. 2020, Solar MHD. Cambridge University Press', 'Smith, A. 2020, Ph.D. thesis, University of Oslo',
                      'Smith, A. 2008, in ASP Conf. Ser. 383, Subsurface Activity, ed. R. Howe (San Francisco: ASP), 235'):
            with self.subTest(entry):
                self.assertEqual(raws([f'References\n{entry}\nCaption-like stray text\nBrown, C. 2019, A&A, 1, 2']),
                                 [entry, 'Brown, C. 2019, A&A, 1, 2'])

    def test_wraps_pages_and_urls_may_still_continue_a_finished_citation(self):
        for entry, more in (('Smith, A. 2020, doi:10.1086/', '123456'), ('Smith, A. 2020, ApJ, 745, 152', '–160'),
                            ('Smith, A. 2020, ApJ, 745, 152', '(Erratum 2021)'), ('Smith, A. 2020, ApJ, 745, 152', 'arXiv:2001.01234'),
                            ('Smith, A. 2020, ApJ, 745, 152,', 'with an erratum')):
            with self.subTest(entry, more=more):
                self.assertEqual(raws([f'References\n{entry}\n{more}\nBrown, C. 2019, A&A, 1, 2'])[0], f'{entry} {more}')

    def test_an_unfinished_citation_collects_its_lines_across_a_page_header(self):
        pages = ['Body.\nReferences\nBrown, C. 2019, A&A, 1, 2\nWinebarger, A. R., Schmelz, J. T., Warren, H. P., Hulburt, E. O.,\n'
                 'Saar, S. H., & Kashyap, V. L. 2011, ApJ,',
                 '– 18 –\n740, 2\nWhite, D. 2018, A&A, 3, 4\nWood, E. 2010, in Springer,\nHeidelberg']
        entries = raws(pages)
        self.assertEqual(len(entries), 4)
        self.assertTrue(entries[1].endswith('2011, ApJ, – 18 – 740, 2'))
        self.assertEqual(entries[3], 'Wood, E. 2010, in Springer, Heidelberg')

    def test_a_publisher_or_status_word_in_the_author_list_is_not_the_end(self):
        entry = 'Lemen, J. R., Springer, L., Press, W. H., Books, A. 2012, Sol. Phys., 275, 17'
        self.assertEqual(raws([f'References\n{entry}\nBrown, C. 2019, A&A, 1, 2'])[0], entry)

    def test_an_author_list_without_a_comma_starts_an_entry_once_the_previous_one_ended(self):
        pages = ['Smith (2020), Hood et al. (2009).\nReferences\nSmith, A. 2020, ApJ, 745, 152\n'
                 'Hood A. W., Browning P. K., Van der Linden, R. A. M. 2009, A&A, 506, 913']
        refs = extract(pages)
        self.assertEqual([ref['label'] for ref in refs], ['Smith 2020', 'Hood 2009'])
        self.assertEqual([ref['citation_count'] for ref in refs], [1, 1])
        refs = extract(['References\nZacharias, P. 2011, A&A, 531, A97\nZel’dovich, Ya. B., & Raizer, Yu. P. 1967, Physics, 1, 2'])
        self.assertEqual([ref['label'] for ref in refs], ['Zacharias 2011', 'Zel’dovich 1967'])
        # Inside an unfinished entry the same shape is a wrapped author list.
        self.assertEqual(len(raws(['References\nHood, A. W., Browning P. K., Van der Linden,\nKlimchuk J. A. 2009, A&A, 506, 913'])), 1)


class OutlierTests(unittest.TestCase):
    def test_an_entry_far_beyond_the_median_is_cut_at_its_first_citation(self):
        junk = ' '.join(['stray words from a figure caption'] * 12)
        entry = 'Smith, A. 2020, The long story of an unfinished entry'
        pages = ['References\n' + '\n'.join(f'Author{chr(97 + i)}, A. 20{10 + i}, Notes {i}' for i in range(6)) +
                 f'\n{entry}\nJournal of Tests, 12, 34 (2020) {junk}']
        refs = extract(pages)
        self.assertTrue(refs[-1]['raw'].endswith('Journal of Tests, 12, 34 (2020)'))
        self.assertNotIn('stray words', refs[-1]['raw'])
        self.assertEqual(len(refs), 7)

    def test_a_long_author_list_is_left_alone_and_unfinished_entries_cannot_be_cut(self):
        authors = ', '.join(f'Name{chr(65 + i)}, {chr(65 + i)}.' for i in range(20))
        lines = [f'Author{chr(97 + i)}, A. 20{10 + i}, ApJ, {i}, 1' for i in range(6)]
        legit = f'Consortium, A., Springer, L., Press, W. H., {authors} 2014, Sol. Phys., 289, 2733'
        refs = extract(['References\n' + '\n'.join(lines) + '\n' + legit])
        self.assertEqual(refs[-1]['raw'], legit)
        open_entry = 'Plain, A. 2019, ' + 'no citation end in this one ' * 30
        refs = extract(['References\n' + '\n'.join(lines) + '\n' + open_entry])
        self.assertEqual(refs[-1]['raw'], open_entry.strip())


class ShortenedEntryCarryOverTests(unittest.TestCase):
    def test_manual_link_stays_with_the_previous_entry_when_two_prefixes_match(self):
        with TemporaryDirectory() as directory, Catalog(directory) as library:
            raw = 'Smith, J. 2001, Journal 5, 100'
            entry = {'label': 'Smith 2001', 'authors': 'Smith', 'year': '2001', 'doi': '', 'citation_count': 1}
            old = {**entry, 'id': '7', 'raw': f'{raw}. Brown, C. 2005, Journal 7, 25',
                   'manual_link': True, 'local_document_id': 2}
            _save(library, 1, {'references': [old], 'status': 'partial'})
            result = _store_analysis(library, 1, [{**entry, 'raw': raw}, {**entry, 'raw': raw[:-3]}])

        first, second, legacy = result['references']
        for fresh in (first, second):
            self.assertNotEqual(fresh['id'], '7')
            self.assertFalse(fresh.get('manual_link'))
        self.assertTrue(legacy['legacy'])
        self.assertEqual((legacy['id'], legacy['manual_target_id']), ('7', 2))

    def test_links_and_retrieval_of_an_over_merged_entry_follow_the_shortened_entry(self):
        class Page:
            def extract_text(self, **kwargs):
                return (f'References\nBrown, C. 2019, A&A, 1, 2\nBaty, H. 2000, A&A, 353, 1074\nFigure 14. As Figure 10(a), for the simulation.\n'
                        f'{WINEBARGER}\n{CAPTIONS}')

        class Reader:
            pages = [Page()]
            named_destinations = {}

        class Library(ReferenceTestLibrary):
            def __init__(self, path):
                super().__init__(path)
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
            def list_documents(self):
                return [{'id': 3}]

        old_raw = f'{WINEBARGER} Figure Captions Figure 1 Active region observed on 2010 June 19 using SDO/AIA. All 6 images are coaligned.'
        with TemporaryDirectory() as directory, Library(Path(directory) / 'library.sqlite') as library:
            old = {'id': '1', 'label': 'Winebarger 2011', 'authors': 'Winebarger', 'year': '2011', 'raw': old_raw,
                   'manual_link': True, 'local_document_id': 3, 'retrieval': {'status': 'found'}}
            # A short entry must match too: the prefix rule is not reserved for long ones.
            short = {'id': '2', 'label': 'Baty 2000', 'authors': 'Baty', 'year': '2000', 'manual_link': True, 'local_document_id': 3,
                     'raw': 'Baty, H. 2000, A&A, 353, 1074 Figure 14. As Figure 10(a), for the simulation.'}
            _save(library, 1, {'references': [old, short], 'status': 'partial', 'extraction_version': EXTRACTION_VERSION - 1})
            with patch('paperassist.references.Pdf', return_value=_fake_pdf(Reader.pages)()):
                refs = get(library, 1)['references']
        self.assertEqual([ref['raw'] for ref in refs], ['Brown, C. 2019, A&A, 1, 2', 'Baty, H. 2000, A&A, 353, 1074', WINEBARGER])
        self.assertEqual(refs[2]['local_document_id'], 3)
        self.assertEqual(refs[2]['retrieval']['status'], 'found')
        self.assertEqual(refs[1]['local_document_id'], 3)
        self.assertFalse(any(ref.get('legacy') for ref in refs))


class LineNumberAndHeadingTests(unittest.TestCase):
    @staticmethod
    def _mock_reader(page_fragments, width=595.0):
        class Box:
            right = width

        class Page:
            mediabox = Box()

            def __init__(self, frags):
                self._frags = frags

            def extract_text(self, visitor_text=None, **_kwargs):
                out = []
                for text, x, y, size in self._frags:
                    out.append(text)
                    if visitor_text is not None:
                        visitor_text(text, [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, x, y], None, size)
                return ''.join(out)

        class Reader:
            pages = [Page(frags) for frags in page_fragments]
            named_destinations = {}

        return Reader()

    def test_margin_line_numbers_are_stripped_from_headings_and_entries(self):
        reader = self._mock_reader([
            [
                ('Introduction discusses Liu et al. (2010)', 37, 700, 9), ('10', 19, 700, 6.6), ('\n', 37, 700, 9),
                ('and Rollett et al. (2016).', 37, 600, 9), ('20', 19, 600, 6.6), ('\n', 37, 600, 9),
                ('Right column text continues.', 310, 700, 9), ('30', 571, 700, 6.6), ('\n', 310, 700, 9),
            ],
            [
                ('More prose in body.', 37, 720, 9), ('40', 19, 720, 6.6), ('\n', 37, 720, 9),
                ('References', 37, 615, 10.4), ('50', 16, 615, 6.6), ('\n', 37, 615, 10.4),
                ('Liu, Y., Davies, J. A. 2010, ApJL, 710, L82', 37, 520, 8), ('60', 16, 520, 6.6), ('\n', 37, 520, 8),
                ('Rollett, T., Möstl, C. 2016, ApJ, 824, 131', 37, 430, 8), ('70', 16, 430, 6.6), ('\n', 37, 430, 8),
            ],
        ])
        pages, ignored = _extract_pages(reader)
        self.assertEqual(sum(len(coords) for coords in ignored.values()), 7)
        refs = extract(pages)
        self.assertEqual([r['label'] for r in refs], ['Liu 2010', 'Rollett 2016'])
        self.assertEqual([r['citation_count'] for r in refs], [1, 1])
        self.assertEqual(refs[0]['raw'], 'Liu, Y., Davies, J. A. 2010, ApJL, 710, L82')
        self.assertEqual(refs[1]['raw'], 'Rollett, T., Möstl, C. 2016, ApJ, 824, 131')

    def test_pagewise_line_numbers_are_stripped(self):
        reader = self._mock_reader([
            [
                ('Prose citing Smith (2020).\n', 45, 700, 9),
                ('5', 20, 680, 7), ('line a\n', 45, 680, 9),
                ('10', 20, 600, 7), ('line b\n', 45, 600, 9),
                ('15', 20, 520, 7), ('line c\n', 45, 520, 9),
                ('20', 20, 440, 7), ('line d\n', 45, 440, 9),
            ],
            [
                ('5', 20, 700, 7), ('References\n', 45, 700, 10),
                ('10', 20, 620, 7), ('Smith, A. 2020, A&A, 1, 2\n', 45, 620, 8),
                ('15', 20, 540, 7), ('Brown, B. 2021, A&A, 3, 4\n', 45, 540, 8),
                ('20', 20, 460, 7), ('end\n', 45, 460, 8),
            ],
        ])
        pages, ignored = _extract_pages(reader)
        self.assertEqual(sum(len(coords) for coords in ignored.values()), 8)
        refs = extract(pages)
        self.assertEqual([r['label'] for r in refs], ['Smith 2020', 'Brown 2021'])
        self.assertEqual(refs[0]['citation_count'], 1)

    def test_axis_ticks_and_page_numbers_are_not_stripped(self):
        # Y-axis ticks increase bottom-to-top (y ascending), unlike top-to-bottom line numbers.
        reader = self._mock_reader([
            [
                ('Prose citing Smith (2020).\n', 80, 700, 9),
                ('1', 20, 50, 7), ('\n', 20, 50, 7),
                ('0', 20, 100, 7), (' ', 20, 100, 7),
                ('5', 20, 150, 7), (' ', 20, 150, 7),
                ('10', 18, 200, 7), (' ', 18, 200, 7),
                ('15', 18, 250, 7), ('\n', 18, 250, 7),
            ],
            [
                ('More prose.\n', 80, 700, 9),
                ('2', 20, 50, 7), ('\n', 20, 50, 7),
                ('References\n', 80, 600, 10),
                ('Smith, A. 2020, ApJ, 10, 20\n', 80, 550, 8),
            ],
        ])
        pages, ignored = _extract_pages(reader)
        self.assertEqual(ignored, {})
        self.assertEqual([r['label'] for r in extract(pages)], ['Smith 2020'])

    def test_heading_variants_and_fallback_with_entries(self):
        for heading in ('7. References', 'References:', 'Literature Cited', 'References640'):
            with self.subTest(heading=heading):
                refs = extract([
                    f'See Smith (2020) and Brown (2021).\n{heading}\n'
                    'Smith, A. 2020, ApJ, 1, 2\nBrown, B. 2021, A&A, 3, 4'
                ])
                self.assertEqual([r['label'] for r in refs], ['Smith 2020', 'Brown 2021'])
                self.assertEqual([r['citation_count'] for r in refs], [1, 1])

        # Fallback must not trigger on stray prose lacking following bibliography entries.
        self.assertEqual(extract(['References 1\nThis is a normal paragraph.\nNothing cited here.']), [])

    def test_analyze_pdf_reuses_extracted_chunks_without_second_extract_text(self):
        from io import BytesIO
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, TextStringObject
        from paperassist import references
        from paperassist.native.engine import Pdf
        from paperassist.references import _analyze_pdf

        writer = PdfWriter()
        font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
        page = writer.add_blank_page(600, 700)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(
            b'BT /F1 10 Tf 80 650 Td (See Smith (2020) here.) Tj '
            b'0 -50 Td (References) Tj '
            b'0 -50 Td (Smith, A. 2020, ApJ, 10, 20) Tj ET'
        )
        page[NameObject('/Contents')] = writer._add_object(stream)
        writer.add_named_destination_array(TextStringObject('cite.smith2020'), ArrayObject([
            writer.pages[0].indirect_reference, NameObject('/XYZ'), FloatObject(80), FloatObject(562), FloatObject(0)]))
        writer.add_annotation(0, DictionaryObject({
            NameObject('/Subtype'): NameObject('/Link'),
            NameObject('/Rect'): ArrayObject(FloatObject(v) for v in (100, 648, 160, 662)),
            NameObject('/Dest'): TextStringObject('cite.smith2020'),
        }))
        buf = BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()

        class StubLibrary:
            def read_pdf(self, document_id, annotated=False):
                return pdf_bytes, 0

        opened = []
        shared_args = []
        real_crosscheck = references._crosscheck_links

        def open_pdf(data):
            instance = Pdf(data)
            opened.append(instance)
            return instance

        def tracked_crosscheck(pdf_arg, heading, refs):
            shared_args.append(pdf_arg)
            return real_crosscheck(pdf_arg, heading, refs)

        with patch('paperassist.references.Pdf', side_effect=open_pdf) as pdf_ctor, \
             patch('paperassist.references._crosscheck_links', side_effect=tracked_crosscheck), \
             patch('pypdf.PdfReader', side_effect=AssertionError('PdfReader must not be called')):
            refs = _analyze_pdf(StubLibrary(), 1)
        self.assertEqual(pdf_ctor.call_count, 1)
        self.assertEqual(len(opened), 1)
        self.assertIs(shared_args[0], opened[0])
        self.assertTrue(opened[0].is_closed)
        self.assertEqual(refs[0]['destinations'], ['cite.smith2020'])
        self.assertEqual(refs[0]['citation_count'], 1)

    def test_resolve_checks_pdf_availability_without_n_plus_one_get_path(self):
        from io import BytesIO
        from pypdf import PdfWriter
        from paperassist.references import _resolve

        buf = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.write(buf)
        pdf_bytes = buf.getvalue()

        with TemporaryDirectory() as directory:
            with Catalog(Path(directory)) as library:
                source = library.import_stream(BytesIO(pdf_bytes), 'source.pdf')
                cited = library.import_stream(BytesIO(pdf_bytes + b'\n% cited\n'), 'cited.pdf')
                library.update_metadata(cited['id'], {'doi': '10.1234/cited'})
                docs = library.list_documents()
                payload = {'references': [{'id': '0', 'raw': 'Smith, A. 2020, ApJ, 1, 2', 'authors': 'Smith',
                                           'year': '2020', 'doi': '10.1234/cited'}], 'status': 'partial'}
                with patch.object(library, 'get_path', side_effect=AssertionError('get_path should not be called')):
                    resolved = _resolve(library, source['id'], payload, docs)
                self.assertEqual(resolved['references'][0]['local_document_id'], cited['id'])

    def test_resolve_uses_indexed_manual_links_without_parsing_other_analyses(self):
        import json
        from io import BytesIO
        from pypdf import PdfWriter
        from paperassist.references import _resolve

        buf = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.write(buf)
        pdf_bytes = buf.getvalue()

        with TemporaryDirectory() as directory, Catalog(Path(directory)) as library:
            docs = [library.import_stream(BytesIO(pdf_bytes + f'\n% {i}\n'.encode()), f'p{i}.pdf') for i in range(6)]
            target = docs[5]
            ref = {'id': '0', 'raw': 'Smith, A., & Jones, B. 2020, ApJ, 1, 2', 'authors': 'Smith', 'year': '2020'}
            for other in docs[1:5]:
                _save(library, other['id'], {'references': [{**ref, 'manual_link': True, 'local_document_id': target['id']}],
                                             'status': 'partial', 'destination_version': DESTINATION_VERSION,
                                             'extraction_version': EXTRACTION_VERSION})
            _save(library, docs[0]['id'], {'references': [{**dict(ref), 'id': '0'}], 'status': 'partial',
                                           'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
            with patch('paperassist.references.json.loads', wraps=json.loads) as loads:
                resolved = get(library, docs[0]['id'])
            self.assertEqual((resolved['references'][0]['local_document_id'], resolved['references'][0]['local_match']),
                             (target['id'], 'shared_manual'))
            # Only docs[0]'s analysis payload is decoded; docs[1..4]'s manual links come from the SQL index.
            self.assertFalse(any('manual_link' in call.args[0] for call in loads.call_args_list))
            self.assertEqual(sum('destination_version' in call.args[0] for call in loads.call_args_list), 1)
            link(library, docs[1]['id'], '0', None)
            _resolve(library, docs[0]['id'], {'references': [dict(ref)]}, docs)

    def test_arxiv_and_bibcode_local_reference_matching(self):
        from paperassist.references import _resolve

        class StubLibrary(ReferenceTestLibrary):
            def __init__(self, path, docs):
                super().__init__(path)
                self.docs = docs
            def list_documents(self):
                return self.docs
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'

        docs = [
            {'id': 1, 'sha256': 's1', 'authors': 'Smith A.', 'year': 2024, 'metadata': {'verified': {'arxiv_id': '2401.12345v2'}}},
            {'id': 2, 'sha256': 's2', 'authors': 'Brown B.', 'year': 2023, 'metadata': {'candidates': {'arxiv': ['2305.54321']}}},
            {'id': 3, 'sha256': 's3', 'authors': 'Hopper G.', 'year': 1984, 'metadata': {'verified': {'bibcode': '1984ApJ...279..857R'}}},
            {'id': 9, 'sha256': 's9', 'authors': 'Source'},
        ]
        with TemporaryDirectory() as directory, StubLibrary(Path(directory) / 'lib.sqlite', docs) as library:
            payload = {'references': [
                {'id': '0', 'raw': 'Smith, A. 2024, arXiv:2401.12345', 'authors': 'Smith', 'year': '2024'},
                {'id': '1', 'raw': 'Brown, B. 2023, arXiv:2305.54321', 'authors': 'Brown', 'year': '2023'},
                {'id': '2', 'raw': 'Wrong, Z. 2023, arXiv:2305.54321', 'authors': 'Wrong', 'year': '2023'},
                {'id': '3', 'raw': 'Hopper, G. 1984, ApJ, 279, 857', 'authors': 'Hopper', 'year': '1984',
                 'identity': {'bibcode': '1984apj...279..857r'}},
            ]}
            resolved = _resolve(library, 9, payload, docs)['references']
            self.assertEqual((resolved[0]['local_document_id'], resolved[0]['local_match']), (1, 'arxiv'))
            self.assertEqual((resolved[1]['local_document_id'], resolved[1]['local_match']), (2, 'first_page_arxiv'))
            self.assertIsNone(resolved[2]['local_document_id'])
            self.assertEqual((resolved[3]['local_document_id'], resolved[3]['local_match']), (3, 'bibcode'))


class PdfiumTwoColumnAndNumericTests(unittest.TestCase):
    @staticmethod
    def _build_pdf(pages_ops, named_dests=(), *, rotation=0):
        from io import BytesIO
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, TextStringObject

        writer = PdfWriter()
        font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
        for ops in pages_ops:
            page = writer.add_blank_page(600, 800)
            page.rotate(rotation)
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
            stream = DecodedStreamObject()
            stream.set_data('\n'.join(ops).encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
        for name, page_idx, left, top in named_dests:
            writer.add_named_destination_array(TextStringObject(name), ArrayObject([
                writer.pages[page_idx].indirect_reference, NameObject('/XYZ'), FloatObject(left), FloatObject(top), FloatObject(0)]))
        buf = BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def test_pdfium_ignores_vertical_imprint_beside_bibliography(self):
        from paperassist.native.engine import Pdf

        ops = [
            'BT /F1 10 Tf 45 560 Td (Prior work [1] supports the result described on this page.) Tj ET',
            'BT /F1 12 Tf 45 540 Td (References) Tj ET',
            'BT /F1 9 Tf 45 520 Td (1. Alpha A. 2020 A study of how example data can support a published scientific result.) Tj ET',
            'BT /F1 9 Tf 45 505 Td (2. Beta B. 2021 A follow-up study that evaluates an alternative explanation for the result.) Tj ET',
            'BT /F1 9 Tf 0 -1 1 0 570 567 Tm (Journal imprint and publisher information) Tj ET',
        ]
        for rotation, page_ops in ((0, ops), (90, ['q 0 1 -1 0 600 0 cm', *ops, 'Q'])):
            with self.subTest(rotation=rotation):
                pdf = Pdf(self._build_pdf([page_ops], rotation=rotation))
                try:
                    pages, _ = _extract_pages(pdf)
                    self.assertIn('References', pages[0].splitlines())
                    refs = extract(pages)
                    self.assertEqual([ref['label'] for ref in refs], ['1', '2'])
                    self.assertEqual([ref['citation_count'] for ref in refs], [1, 0])
                    self.assertEqual(refs[0]['raw'], 'Alpha A. 2020 A study of how example data can support a published scientific result.')
                finally:
                    pdf.close()

    def test_pdfium_page_number_does_not_merge_adjacent_reference_lines(self):
        from paperassist.native.engine import Pdf

        ops = [
            'BT /F1 10 Tf 45 760 Td (1. Alpha A. 2020 A long reference.) Tj ET',
            'BT /F1 9 Tf 565 755 Td (1) Tj ET',
            'BT /F1 10 Tf 58 749 Td (A continuation. Example Journal 3, 4.) Tj ET',
        ]
        pdf = Pdf(self._build_pdf([ops]))
        try:
            pages, _ = _extract_pages(pdf)
            self.assertIn('1. Alpha A. 2020 A long reference.', pages[0].splitlines())
            self.assertIn('A continuation. Example Journal 3, 4.', pages[0].splitlines())
        finally:
            pdf.close()

    def test_pdfium_attaches_a_separately_drawn_accent_to_its_letter(self):
        from paperassist.native.engine import Pdf

        ops = [
            'BT /F1 10 Tf 40 740 Td (Muller \\(2003\\) is cited here.) Tj ET',
            'BT /F1 12 Tf 40 700 Td (References) Tj ET',
            'BT /F1 10 Tf 40 680 Td (Muller, D. 2003, ApJ, 411, 605) Tj ET',
            'BT /F1 10 Tf 48.5 740 Td (\\310) Tj ET',
            'BT /F1 10 Tf 48.5 680 Td (\\310) Tj ET',
        ]
        pdf = Pdf(self._build_pdf([ops]))
        try:
            pages, _ = _extract_pages(pdf)
            refs = extract(pages)
            self.assertEqual([ref['label'] for ref in refs], ['Müller 2003'])
            self.assertEqual(refs[0]['citation_count'], 1)
        finally:
            pdf.close()

    def test_pdfium_recovers_a_heading_beside_an_acknowledgments_sidebar(self):
        from types import SimpleNamespace

        from paperassist.native.engine import Pdf
        from paperassist.references import _analyze_pdf

        ops = [
            'BT /F1 10 Tf 180 680 Td (Earlier work [1] supports this result.) Tj ET',
            'BT /F1 12 Tf 180 640 Td (References) Tj ET',
            'BT /F1 9 Tf 180 616 Td ([1] Alpha, A. 2020, ApJ, 1, 1) Tj ET',
            'BT /F1 9 Tf 35 640 Td (Acknowledgments) Tj ET',
            'BT /F1 9 Tf 35 625 Td (Support for this study.) Tj ET',
            'BT /F1 9 Tf 180 588 Td ([2] Beta, B. 2021, ApJ, 2, 2) Tj ET',
        ]
        data = self._build_pdf([ops])
        pdf = Pdf(data)
        try:
            pages, _ = _extract_pages(pdf)
            self.assertEqual(extract(pages), [])
        finally:
            pdf.close()
        library = SimpleNamespace(read_pdf=lambda document_id, annotated: (data, 0))
        refs = _analyze_pdf(library, 1)
        self.assertEqual([ref['label'] for ref in refs], ['1', '2'])
        self.assertEqual([ref['citation_count'] for ref in refs], [1, 0])

    def test_pdfium_recovers_numbered_references_after_neighbors_end_the_visual_list(self):
        from types import SimpleNamespace

        from paperassist.native.engine import Pdf
        from paperassist.references import _analyze_pdf

        ops = [
            'BT /F1 10 Tf 35 740 Td (Earlier work [1] supports the result.) Tj ET',
            'BT /F1 9 Tf 35 640 Td (Acknowledgments) Tj ET',
            'BT /F1 12 Tf 320 700 Td (References) Tj ET',
            'BT /F1 9 Tf 320 683 Td ([1] Alpha, A. 2020, ApJ, 1, 1) Tj ET',
            'BT /F1 9 Tf 320 668 Td ([2] Beta, B. 2021, ApJ, 2, 2) Tj ET',
            'BT /F1 9 Tf 320 640 Td ([3] Gamma, C. 2022, ApJ, 3, 3) Tj ET',
            'BT /F1 9 Tf 320 610 Td ([4] Delta, D. 2023, ApJ, 4, 4) Tj ET',
        ]
        data = self._build_pdf([ops])
        pdf = Pdf(data)
        try:
            pages, _ = _extract_pages(pdf)
            self.assertEqual([ref['label'] for ref in extract(pages)], ['1', '2'])
        finally:
            pdf.close()
        library = SimpleNamespace(read_pdf=lambda document_id, annotated: (data, 0))
        refs = _analyze_pdf(library, 1)
        self.assertEqual([ref['label'] for ref in refs], ['1', '2', '3', '4'])
        self.assertEqual([ref['citation_count'] for ref in refs], [1, 0, 0, 0])

    def test_two_column_pdfium_reads_left_column_before_right_column_with_spanning_bands(self):
        from paperassist.native.engine import Pdf

        ops = [
            # Full-width spanning title (> 52% width)
            'BT /F1 14 Tf 60 740 Td (A Full Width Title About Solar Coronal Loops) Tj ET',
            # Interleaved stream order across left and right columns
            'BT /F1 10 Tf 40 680 Td (Left one discusses Smith \\(2020\\).) Tj ET',
            'BT /F1 10 Tf 320 680 Td (Right one cites Brown \\(2021\\).) Tj ET',
            'BT /F1 10 Tf 40 660 Td (Left two continues the section.) Tj ET',
            'BT /F1 10 Tf 320 660 Td (Right two continues further.) Tj ET',
            'BT /F1 10 Tf 40 640 Td (Left three finishes body.) Tj ET',
            'BT /F1 10 Tf 320 640 Td (Right three precedes refs.) Tj ET',
            # Full-width spanning References heading across center gutter
            'BT /F1 12 Tf 255 600 Td (References) Tj ET',
            # Two-column bibliography entries
            'BT /F1 9 Tf 40 560 Td (Alpha, A. 2019, ApJ, 1, 1) Tj ET',
            'BT /F1 9 Tf 320 560 Td (Brown, B. 2021, ApJ, 3, 3) Tj ET',
            'BT /F1 9 Tf 40 540 Td (Beta, C. 2018, ApJ, 2, 2) Tj ET',
            'BT /F1 9 Tf 320 540 Td (Smith, A. 2020, ApJ, 4, 4) Tj ET',
            'BT /F1 9 Tf 40 520 Td (Gamma, D. 2017, ApJ, 5, 5) Tj ET',
            'BT /F1 9 Tf 320 520 Td (Zeta, E. 2016, ApJ, 6, 6) Tj ET',
        ]
        pdf_bytes = self._build_pdf(
            [ops],
            named_dests=[
                ('cite.brown', 0, 320, 570),
                ('cite.smith', 0, 40, 530),  # intentional y below Beta
            ],
        )
        pdf = Pdf(pdf_bytes)
        try:
            pages, ignored = _extract_pages(pdf)
            lines = pages[0].splitlines()
            self.assertEqual(lines[0], 'A Full Width Title About Solar Coronal Loops')
            self.assertEqual(lines[1:7], [
                'Left one discusses Smith (2020).',
                'Left two continues the section.',
                'Left three finishes body.',
                'Right one cites Brown (2021).',
                'Right two continues further.',
                'Right three precedes refs.',
            ])
            self.assertEqual(lines[7], 'References')
            self.assertEqual(lines[8:], [
                'Alpha, A. 2019, ApJ, 1, 1',
                'Beta, C. 2018, ApJ, 2, 2',
                'Gamma, D. 2017, ApJ, 5, 5',
                'Brown, B. 2021, ApJ, 3, 3',
                'Smith, A. 2020, ApJ, 4, 4',
                'Zeta, E. 2016, ApJ, 6, 6',
            ])
            refs = extract(pages)
            by_label = {r['label']: r for r in refs}
            self.assertEqual(by_label['Smith 2020']['citation_count'], 1)
            self.assertEqual(by_label['Brown 2021']['citation_count'], 1)
            map_destinations(pdf, pages, refs, ignored)
            self.assertEqual(by_label['Brown 2021']['destinations'], ['cite.brown'])
        finally:
            pdf.close()

    def test_pdfium_strips_gutter_and_margin_line_numbers_and_promotes_geometric_superscripts(self):
        from paperassist.native.engine import Pdf

        ops = [
            # Left column with margin line numbers (x=18) and gutter line numbers (x=295)
            'BT /F1 7 Tf 18 700 Td (1) Tj ET',
            'BT /F1 10 Tf 1 0 0 1 45 700 Tm (As shown earlier) Tj /F1 6 Tf 1 0 0 1 125 704 Tm (1-3) Tj ET',
            'BT /F1 7 Tf 295 700 Td (5) Tj ET',
            'BT /F1 10 Tf 1 0 0 1 320 700 Tm (Scaling as 10) Tj /F1 6 Tf 1 0 0 1 378 704 Tm (6) Tj /F1 10 Tf 1 0 0 1 385 700 Tm ( cm) Tj /F1 6 Tf 1 0 0 1 402 704 Tm (-3) Tj ET',
            'BT /F1 7 Tf 18 680 Td (2) Tj ET',
            'BT /F1 10 Tf 45 680 Td (Line two body.) Tj ET',
            'BT /F1 7 Tf 295 680 Td (6) Tj ET',
            'BT /F1 10 Tf 320 680 Td (Right line two.) Tj ET',
            'BT /F1 7 Tf 18 660 Td (3) Tj ET',
            'BT /F1 10 Tf 45 660 Td (Line three body.) Tj ET',
            'BT /F1 7 Tf 295 660 Td (7) Tj ET',
            'BT /F1 10 Tf 320 660 Td (Right line three.) Tj ET',
            'BT /F1 7 Tf 18 640 Td (4) Tj ET',
            'BT /F1 10 Tf 45 640 Td (Line four body.) Tj ET',
            'BT /F1 7 Tf 295 640 Td (8) Tj ET',
            'BT /F1 10 Tf 320 640 Td (Right line four.) Tj ET',
            'BT /F1 12 Tf 45 580 Td (References) Tj ET',
            'BT /F1 9 Tf 45 550 Td ([1] Alpha, A. 2020, ApJ, 1, 1) Tj ET',
            'BT /F1 9 Tf 45 535 Td ([2] Beta, B. 2021, ApJ, 2, 2) Tj ET',
            'BT /F1 9 Tf 45 520 Td ([3] Gamma, C. 2022, ApJ, 3, 3) Tj ET',
            'BT /F1 9 Tf 45 505 Td ([6] Six, F. 2023, ApJ, 6, 6) Tj ET',
        ]
        pdf = Pdf(self._build_pdf([ops]))
        try:
            pages, ignored = _extract_pages(pdf)
            self.assertEqual(len(ignored.get(0, ())), 8)
            self.assertIn('earlier¹⁻³', pages[0])
            self.assertNotIn('10⁶', pages[0])
            self.assertNotIn('cm⁻³', pages[0])
            refs = extract(pages)
            by_label = {r['label']: r for r in refs}
            self.assertEqual([by_label[k]['citation_count'] for k in ('1', '2', '3', '6')], [1, 1, 1, 0])
            self.assertEqual(by_label['1']['occurrences'][0]['marker'], '1-3')
        finally:
            pdf.close()

    def test_unicode_superscript_and_parenthesized_numeric_citations_and_guards(self):
        pages = [
            'Prior work¹˒²⁻⁴ and shown.¹⁻³ Skip 10⁶, cm⁻³, s⁻¹, x².\n'
            'Compare (1) and (1, 2–3). Skip Eq. (1), Eqs. (1, 2), Equation (2), Fig. (2), '
            'Figure (3), Table (1), Sec. (2), Section (3), Model (1), Case (2), Step (1).\n'
            'Also skip a = (1), x + (2), (3) = y, year (2020), out-of-range (9), and equation line:\n'
            'E = mc^2   (1)\n'
            '(2)\n'
            'References\n'
            '[1] Alpha, A. 2020, ApJ, 1, 1\n'
            '[2] Beta, B. 2021, ApJ, 2, 2\n'
            '[3] Gamma, C. 2022, ApJ, 3, 3\n'
            '[4] Delta, D. 2023, ApJ, 4, 4\n'
        ]
        refs = extract(pages)
        self.assertEqual([r['citation_count'] for r in refs], [4, 3, 3, 1])

    def test_three_author_oxford_comma_does_not_leak_to_single_author(self):
        pages = [
            'Smith, Jones, & Brown 2020 and Smith, Jones, and Brown (2020) and Smith, Jones & Brown 2020.\n'
            'Brown (2020) is separate.\n'
            'References\n'
            'Smith, A., Jones, B., & Brown, C. 2020, ApJ, 1, 1\n'
            'Brown, C. 2020, ApJ, 2, 2\n'
        ]
        refs = extract(pages)
        self.assertEqual([r['citation_count'] for r in refs], [3, 1])


class OcrAndUnheadedBibliographyTests(unittest.TestCase):
    @staticmethod
    def _build_pdf(pages_ops, named_dests=()):
        return PdfiumTwoColumnAndNumericTests._build_pdf(pages_ops, named_dests=named_dests)

    @staticmethod
    def _write_sidecar(cache_dir, sha256, page_idx, size, word_entries):
        from paperassist import ocr
        ocr._write_sidecar_page(
            cache_dir,
            sha256,
            {'page': page_idx, 'size': list(size), 'rotation': 0, 'dpi': 200.0, 'words': word_entries, 'lines': []},
            '5.5.0',
        )

    def test_ocr_sidecars_recover_scanned_and_incomplete_heading_bibliographies(self):
        from io import BytesIO
        from pypdf import PdfWriter

        # 1) Pure scanned PDF (blank vector pages with OCR sidecar text including tall margin imprint).
        buf = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(600, 800)
        writer.add_blank_page(600, 800)
        writer.write(buf)
        scan_bytes = buf.getvalue()

        with TemporaryDirectory() as directory, Catalog(Path(directory)) as library:
            doc = library.import_stream(BytesIO(scan_bytes), 'scan.pdf')
            sha = doc['sha256']
            cache_dir = Path(directory) / 'cache' / 'ocr'

            def fake_recognize(pdf_path, cdir, target_sha, pages=None, **_kwargs):
                self._write_sidecar(
                    cdir, target_sha, 0, (600, 800),
                    [
                        {'text': 'Earlier', 'bbox': [50, 60, 95, 70], 'line': 0},
                        {'text': 'Alpha', 'bbox': [100, 60, 140, 70], 'line': 0},
                        {'text': '(2020)', 'bbox': [145, 60, 185, 70], 'line': 0},
                    ],
                )
                self._write_sidecar(
                    cdir, target_sha, 1, (600, 800),
                    [
                        # Tall narrow rotated margin stamp beside the heading must be ignored.
                        {'text': '1977Journ', 'bbox': [18, 80, 24, 130], 'line': 0},
                        {'text': 'References', 'bbox': [240, 95, 360, 108], 'line': 1},
                        {'text': 'Alpha,', 'bbox': [50, 130, 95, 140], 'line': 2},
                        {'text': 'A.', 'bbox': [100, 130, 115, 140], 'line': 2},
                        {'text': '2020,', 'bbox': [120, 130, 155, 140], 'line': 2},
                        {'text': 'ApJ,', 'bbox': [160, 130, 190, 140], 'line': 2},
                        {'text': '1,', 'bbox': [195, 130, 210, 140], 'line': 2},
                        {'text': '2', 'bbox': [215, 130, 225, 140], 'line': 2},
                    ],
                )
                return {'status': 'completed', 'pages_recognized': 2, 'pages_cached': 0, 'total_pages': 2, 'cache_hit': False}

            with patch('paperassist.ocr.recognize_pages', side_effect=fake_recognize) as run_ocr:
                passive = analyze(library, doc['id'], allow_ocr=False, resolve=False)
                self.assertEqual(passive['references'], [])
                self.assertTrue(passive.get('ocr_pending'))
                run_ocr.assert_not_called()

                loaded = get(library, doc['id'], resolve=False)
                self.assertEqual(run_ocr.call_count, 1)
                self.assertFalse(loaded.get('ocr_pending'))
                self.assertEqual([r['label'] for r in loaded['references']], ['Alpha 2020'])
                self.assertEqual(loaded['references'][0]['citation_count'], 1)
                self.assertTrue((cache_dir / 'pages').exists())

        # 2) Hybrid PDF: native text has entries without printed heading; OCR sidecar supplies only the heading.
        hybrid_bytes = self._build_pdf([
            ['BT /F1 10 Tf 50 720 Td (Body cites Alpha \\(2020\\) and Beta \\(2021\\).) Tj ET'],
            [
                'BT /F1 9 Tf 50 680 Td (Alpha, A. 2020, ApJ, 1, 2) Tj ET',
                'BT /F1 9 Tf 50 660 Td (Beta, B. 2021, ApJ, 3, 4) Tj ET',
                'BT /F1 9 Tf 50 640 Td (Gamma, C. 2022, ApJ, 5, 6) Tj ET',
                'BT /F1 9 Tf 50 620 Td (Delta, D. 2023, ApJ, 7, 8) Tj ET',
                'BT /F1 9 Tf 50 600 Td (Epsilon, E. 2024, ApJ, 9, 10) Tj ET',
            ],
        ])
        with TemporaryDirectory() as directory, Catalog(Path(directory)) as library:
            doc = library.import_stream(BytesIO(hybrid_bytes), 'hybrid.pdf')

            def hybrid_ocr(pdf_path, cdir, target_sha, pages=None, **_kwargs):
                self._write_sidecar(
                    cdir, target_sha, 1, (600, 800),
                    [
                        {'text': 'REFERENCES', 'bbox': [250, 90, 350, 102], 'line': 0},
                    ],
                )
                return {'status': 'completed', 'pages_recognized': 1, 'pages_cached': 0, 'total_pages': 1, 'cache_hit': False}

            with patch('paperassist.ocr.recognize_pages', side_effect=hybrid_ocr):
                refs = analyze(library, doc['id'], resolve=False)['references']
            self.assertEqual(len(refs), 5)
            by_label = {r['label']: r for r in refs}
            self.assertEqual(by_label['Alpha 2020']['citation_count'], 1)
            self.assertEqual(by_label['Beta 2021']['citation_count'], 1)

    def test_unheaded_numbered_and_superscript_bibliographies_with_links_and_negatives(self):
        from io import BytesIO
        from paperassist.native.engine import Pdf
        from paperassist.references import _analyze_pdf

        # 1) Unheaded bracketed list after a body numbered procedure + continuation page with axis ticks & FIG. 4.
        bracketed_pages = [
            'The model has four steps:\n'
            '1. Initialization of the grid.\n'
            '2. Boundary evolution step.\n'
            '3. Energy calculation step.\n'
            '4. Final relaxation step.\n'
            'Prior studies [1, 2] and recent results [4] confirm the trend.\n'
            '[1] Alpha, A. 2001, ApJ, 10, 20.\n'
            '[2] Beta, B. 2002, Sol. Phys. 30, 40.\n'
            '[3] Gamma, C. 2003, A&A, 50, 60;\n',
            '0\n10\n20\n30\n40\n50\n60\nTime\nFIG. 4. Simulated energy curve.\n'
            '7\n'
            '[4] Delta, D. 2004, Phys. Rev. Lett. 91, 100.\n'
            '[5] Epsilon, E. 2005, J. Fluid Mech. 120, 10.\n',
        ]
        refs = extract(bracketed_pages)
        self.assertEqual([r['label'] for r in refs], ['1', '2', '3', '4', '5'])
        self.assertEqual([r['citation_count'] for r in refs], [1, 1, 0, 1, 0])
        self.assertNotIn('Time', refs[2]['raw'])
        self.assertNotIn('FIG. 4', refs[2]['raw'])

        # 2) Space-separated unheaded list (including "Surname, I." author format and URL entry).
        space_pages = [
            'Observations¹˒⁴˒⁵ and early theory² show scaling.\n'
            '1 Alpha, A., The Astrophysical Journal 330, 474 (1988).\n'
            '2 E. N. Parker, Solar Physics 121, 271 (1989).\n'
            '3 Beta, B., and Gamma, C., Astronomy & Astrophysics 529, A101 (2011).\n'
            '4 URL: https://example.org/catalog.\n'
            '5 Delta, D., Phys. Rev. Lett. 92, 108501 (2004).\n',
        ]
        refs = extract(space_pages)
        self.assertEqual([r['label'] for r in refs], ['1', '2', '3', '4', '5'])
        self.assertEqual([r['citation_count'] for r in refs], [1, 1, 0, 1, 1])

        # 3) RevTeX raised-superscript unheaded two-column PDFium PDF with destinations.
        ops_p1 = [
            'BT /F1 10 Tf 1 0 0 1 50 740 Tm (Early studies) Tj /F1 6 Tf 1 0 0 1 112 744 Tm (1,3) Tj '
            '/F1 10 Tf 1 0 0 1 125 740 Tm (and work) Tj /F1 6 Tf 1 0 0 1 170 744 Tm (2) Tj ET',
            # Raised line-leading superscript numbers "1".."4" before fs=8 author names, plus tall math in left col
            'BT /F1 10 Tf 50 660 Td (Side formula in left column.) Tj ET',
            'BT /F1 18 Tf 80 630 Td (X) Tj ET',
            'BT /F1 6 Tf 1 0 0 1 317 690 Tm (1) Tj /F1 8 Tf 1 0 0 1 325 688 Tm (Alpha, A. 2001, ApJ, 10, 20.) Tj ET',
            'BT /F1 6 Tf 1 0 0 1 317 678 Tm (2) Tj /F1 8 Tf 1 0 0 1 325 676 Tm (Beta, B. 2002, Sol. Phys. 30, 40.) Tj ET',
            'BT /F1 6 Tf 1 0 0 1 317 666 Tm (3) Tj /F1 8 Tf 1 0 0 1 325 664 Tm (Gamma, C. 2003, A&A, 50, 60.) Tj ET',
            'BT /F1 6 Tf 1 0 0 1 317 654 Tm (4) Tj /F1 8 Tf 1 0 0 1 325 652 Tm (Delta, D. 2004, Phys. Rev. Lett. 91, 100.) Tj ET',
        ]
        data = self._build_pdf([ops_p1], named_dests=[('cite.alpha', 0, 317, 692), ('cite.delta', 0, 317, 656)])
        from types import SimpleNamespace
        library = SimpleNamespace(read_pdf=lambda document_id, annotated=False: (data, 0))
        pdf_refs = _analyze_pdf(library, 1)
        self.assertEqual([r['label'] for r in pdf_refs], ['1', '2', '3', '4'])
        self.assertEqual([r['citation_count'] for r in pdf_refs], [1, 1, 1, 0])
        self.assertEqual(pdf_refs[0]['destinations'], ['cite.alpha'])
        self.assertEqual(pdf_refs[3]['destinations'], ['cite.delta'])

        # 4) Negative controls: numbered sections without pub cues, equation/footnote pages, and non-sequential tables.
        self.assertEqual(extract([
            'See Section 1 and Section 2.\n'
            '1. Introduction\nWe present mission goals.\n'
            '2. Objectives\nObservations cover three bands.\n'
            '3. Payload\nInstruments measure fields.\n'
            '4. Operations\nTelemetry uses standard links.\n'
        ]), [])
        self.assertEqual(extract([
            'As shown in (1) and (2):\n'
            '1. First footnote note in passing (Journal 1, 2001).\n'
            '2. Second footnote note in passing.\n'
            '3. Third item with no publication details.\n'
            '4. Fourth item with no publication details.\n'
        ]), [])

    def test_organization_and_title_led_entries_under_references_heading(self):
        from paperassist.citations import citation_matches

        pages = [
            'Standards boards (ACME, 1982) and working groups (Space Data Workshop.\n'
            '1983) defined the archive format.\n'
            'References\n'
            'ACME, Data Archiving and Exchange, Vol. 1: Issues\n'
            'and Recommendations. Committee on Data Archiving, Space\n'
            'Board, National Research Council, 1982.\n'
            'Space Data Workshop, Archive Conference Publication 2343, Jane Doe, Chair, December 1,1983.\n'
        ]
        refs = extract(pages)
        self.assertEqual([r['label'] for r in refs], ['ACME 1982', 'Space Data Workshop 1983'])
        self.assertEqual([r['citation_count'] for r in refs], [1, 1])
        self.assertEqual(citation_matches(refs, '(ACME, 1982)'), [refs[0]])
        self.assertEqual(citation_matches(refs, '(Space Data Workshop. 1983)'), [refs[1]])
