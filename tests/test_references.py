import unittest

from paperassist.references import (extract, local_candidates, get, link, repair_downloads, set_retrieval, _save,
                                    map_destinations, _publication, DESTINATION_VERSION, EXTRACTION_VERSION)
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch


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
        pages = ['See [1–3] and [1, 3].\nReferences\n[1] Alpha 2020. First.\n[2] Beta 2021. Second.\n[3] Gamma 2022. Third.']
        refs = extract(pages)
        self.assertEqual([r['citation_count'] for r in refs], [2, 1, 2])
        self.assertTrue(all(r['count_status'] == 'partial' for r in refs))
        self.assertEqual(refs[0]['occurrences'][0]['page'], 1)

    def test_author_year_and_no_heading(self):
        refs = extract(['Smith et al. (2020) and Smith (2020).\nReferences\nSmith, A. 2020. A useful paper.'])
        self.assertEqual(refs[0]['citation_count'], 2)
        self.assertEqual(extract(['No bibliography here']), [])

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
        class Library:
            def __init__(self, path):
                self.db_path = path
                self.documents = []

            def list_documents(self):
                return self.documents

            def get_document(self, document_id):
                return next(d for d in self.documents if d['id'] == document_id)

            def get_path(self, document_id, annotated=False):
                self.get_document(document_id)
                return 'paper.pdf'

        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
            ref = {'id': '0', 'raw': 'Smith, A., Jones, B. 2020', 'authors': 'Smith',
                   'year': '2020', 'doi': '10.1234/example', 'local_document_id': None}
            _save(library, 9, {'references': [ref], 'status': 'partial', 'destination_version': DESTINATION_VERSION,
                               'extraction_version': EXTRACTION_VERSION})
            library.documents = [{'id': 1, 'title': 'Paper', 'authors': 'Smith A., Jones B.',
                                  'year': 2020, 'doi': '10.1234/example'},
                                 {'id': 2, 'title': 'Other', 'authors': '', 'year': None}]
            self.assertEqual(get(library, 9)['references'][0]['local_document_id'], 1)
            library.documents.append({**library.documents[0], 'id': 3})
            self.assertIsNone(get(library, 9)['references'][0]['local_document_id'])
            link(library, 9, '0', 2)
            library.documents.pop()
            self.assertEqual(get(library, 9)['references'][0]['local_document_id'], 2)

    def test_publication_match_ambiguity_missing_and_manual_precedence(self):
        class Page:
            def extract_text(self):
                return 'A&A 564, A48 (2014)\nA paper title'

        class Reader:
            pages = [Page()]

        class Library:
            def __init__(self, path):
                self.db_path = path
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

        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
            ref = {'id': '0', 'raw': 'Guarrasi, M., Reale, F. 2014, A&A, 564, A48',
                   'authors': 'Guarrasi', 'year': '2014', 'doi': ''}
            _save(library, 9, {'references': [ref], 'status': 'partial',
                               'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
            with patch('paperassist.references.PdfReader', return_value=Reader()) as reader:
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
        class Library:
            def __init__(self, path):
                self.db_path = path
            def list_documents(self):
                return [{'id': 2, 'sha256': 'paper', 'authors': 'IOP Publishing'}]
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
            ref = {'id': '0', 'raw': 'Antolin, P., & Rouppe van der Voort, L. 2012, ApJ, 745, 152',
                   'authors': 'Antolin', 'year': '2012'}
            _save(library, 1, {'references': [ref], 'status': 'partial',
                               'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
            with patch('paperassist.references.PdfReader', return_value=Reader()):
                self.assertEqual(get(library, 1)['references'][0]['local_document_id'], 2)

    def test_only_authors_the_user_typed_outrank_the_printed_header(self):
        class Page:
            def extract_text(self):
                return ('The Astrophysical Journal, 745:152 (21pp), 2012 February 1\n'
                        'A PAPER TITLE\nP. Antolin1, and L. Rouppe van der Voort')
        class Reader:
            pages = [Page()]
        class Library:
            def __init__(self, path, metadata):
                self.db_path, self.metadata = path, metadata
            def list_documents(self):
                return [{'id': 2, 'sha256': 'paper', 'authors': 'IOP Publishing', 'metadata_status': 'corrected',
                         'metadata': self.metadata}]
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
        ref = {'id': '0', 'raw': 'Antolin, P., & Rouppe van der Voort, L. 2012, ApJ, 745, 152', 'authors': 'Antolin', 'year': '2012'}
        for manual, expected in ((['title'], 2), (['authors'], None)):
            with self.subTest(manual=manual), TemporaryDirectory() as directory:
                library = Library(Path(directory) / 'library.sqlite', {'manual': manual})
                _save(library, 1, {'references': [dict(ref)], 'status': 'partial',
                                   'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION})
                with patch('paperassist.references.PdfReader', return_value=Reader()):
                    self.assertEqual(get(library, 1)['references'][0]['local_document_id'], expected)

    def test_manual_links_shared_only_when_reference_and_target_are_unique(self):
        class Library:
            def __init__(self, path):
                self.db_path = path
            def list_documents(self):
                return [{'id': 2}, {'id': 3}]
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'

        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
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
        class Library:
            def __init__(self, path):
                self.db_path = path
            def list_documents(self):
                return [{'id': 1}, {'id': 2, 'doi': '10.1234/example'}]
            def get_document(self, document_id):
                return next(d for d in self.list_documents() if d['id'] == document_id)
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'

        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
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
        class Library:
            def __init__(self, path):
                self.db_path = path
            def list_documents(self):
                return [{'id': 2, 'doi': '10.1234/example'}]
            def get_path(self, document_id, annotated=True):
                if annotated:
                    raise FileNotFoundError('Missing saved revision')
                return 'original.pdf'

        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
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

        class Library:
            def __init__(self, path):
                self.db_path = path
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
            def list_documents(self):
                return [{'id': 3}]

        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
            ref = {'raw': 'Smith, A. 2020', 'authors': 'Smith', 'year': '2020',
                   'manual_link': True, 'local_document_id': 3}
            _save(library, 1, {'references': [ref], 'status': 'partial'})
            with patch('paperassist.references.PdfReader', return_value=Reader()) as reader:
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

        class Library:
            def __init__(self, path):
                self.db_path = path
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
            def list_documents(self):
                return [{'id': 3}, {'id': 4}]

        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
            old = {'id': '0', 'label': 'Testa 2012', 'authors': 'Testa', 'year': '2012',
                   'raw': 'Testa, P., & Reale, F. 2012, ApJ, 750, L10 Török, T., Kliem, B., & Titov, V. 2004, A&A, 413, L27',
                   'manual_link': True, 'local_document_id': 3, 'retrieval': {'status': 'found'}}
            missing = {'id': '1', 'label': 'Lost 2001', 'authors': 'Lost', 'year': '2001',
                       'raw': 'Lost, A. 2001', 'manual_link': True, 'local_document_id': 4}
            _save(library, 1, {'references': [old, missing], 'status': 'partial'})
            with patch('paperassist.references.PdfReader', return_value=Reader()) as reader:
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


class FakeLibrary:
    """Minimal library whose first pages are described by `pages` (sha -> text)."""

    def __init__(self, path, documents, pages=None):
        self.db_path, self.documents, self.pages = path, documents, pages or {}
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
    def __init__(self, sha):
        text = FakeLibrary.pages[sha]
        self.pages = [type('Page', (), {'extract_text': lambda self: text})()]


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
        patcher = patch('paperassist.references.PdfReader', side_effect=lambda path: FakeReader(path))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def library(self, *documents):
        return FakeLibrary(Path(self.directory.name) / 'library.sqlite', list(documents), FakeLibrary.pages)

    def save(self, library, *refs):
        _save(library, 9, {'references': list(refs), 'status': 'partial', 'destination_version': DESTINATION_VERSION,
                           'extraction_version': EXTRACTION_VERSION})

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
        self.assertTrue(refs[-1].get('truncated'))
        self.assertTrue(refs[-1]['raw'].endswith('Journal of Tests, 12, 34 (2020)'))
        self.assertFalse(any(ref.get('truncated') for ref in refs[:-1]))

    def test_a_long_author_list_is_left_alone_and_unfinished_entries_cannot_be_cut(self):
        authors = ', '.join(f'Name{chr(65 + i)}, {chr(65 + i)}.' for i in range(20))
        lines = [f'Author{chr(97 + i)}, A. 20{10 + i}, ApJ, {i}, 1' for i in range(6)]
        legit = f'Consortium, A., Springer, L., Press, W. H., {authors} 2014, Sol. Phys., 289, 2733'
        refs = extract(['References\n' + '\n'.join(lines) + '\n' + legit])
        self.assertEqual(refs[-1]['raw'], legit)
        self.assertFalse(refs[-1].get('truncated'))
        open_entry = 'Plain, A. 2019, ' + 'no citation end in this one ' * 30
        refs = extract(['References\n' + '\n'.join(lines) + '\n' + open_entry])
        self.assertEqual(refs[-1]['raw'], open_entry.strip())
        self.assertFalse(refs[-1].get('truncated'))


class ShortenedEntryCarryOverTests(unittest.TestCase):
    def test_links_and_retrieval_of_an_over_merged_entry_follow_the_shortened_entry(self):
        class Page:
            def extract_text(self, **kwargs):
                return (f'References\nBrown, C. 2019, A&A, 1, 2\nBaty, H. 2000, A&A, 353, 1074\nFigure 14. As Figure 10(a), for the simulation.\n'
                        f'{WINEBARGER}\n{CAPTIONS}')

        class Reader:
            pages = [Page()]
            named_destinations = {}

        class Library:
            def __init__(self, path):
                self.db_path = path
            def get_path(self, document_id, annotated=False):
                return 'paper.pdf'
            def list_documents(self):
                return [{'id': 3}]

        old_raw = f'{WINEBARGER} Figure Captions Figure 1 Active region observed on 2010 June 19 using SDO/AIA. All 6 images are coaligned.'
        with TemporaryDirectory() as directory:
            library = Library(Path(directory) / 'library.sqlite')
            old = {'id': '1', 'label': 'Winebarger 2011', 'authors': 'Winebarger', 'year': '2011', 'raw': old_raw,
                   'manual_link': True, 'local_document_id': 3, 'retrieval': {'status': 'found'}}
            # A short entry must match too: the prefix rule is not reserved for long ones.
            short = {'id': '2', 'label': 'Baty 2000', 'authors': 'Baty', 'year': '2000', 'manual_link': True, 'local_document_id': 3,
                     'raw': 'Baty, H. 2000, A&A, 353, 1074 Figure 14. As Figure 10(a), for the simulation.'}
            _save(library, 1, {'references': [old, short], 'status': 'partial', 'extraction_version': EXTRACTION_VERSION - 1})
            with patch('paperassist.references.PdfReader', return_value=Reader()):
                refs = get(library, 1)['references']
        self.assertEqual([ref['raw'] for ref in refs], ['Brown, C. 2019, A&A, 1, 2', 'Baty, H. 2000, A&A, 353, 1074', WINEBARGER])
        self.assertEqual(refs[2]['local_document_id'], 3)
        self.assertEqual(refs[2]['retrieval']['status'], 'found')
        self.assertEqual(refs[1]['local_document_id'], 3)
        self.assertFalse(any(ref.get('legacy') for ref in refs))
