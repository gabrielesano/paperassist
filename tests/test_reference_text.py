"""Bibliography boundaries and text-only citations, including unlinked appendices."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from paperassist import references
from paperassist.library import Library
from paperassist.references import extract


class BibliographyTextTests(unittest.TestCase):
    def test_flattened_bibliography_splits_after_terminal_periods_and_statuses(self):
        refs = extract(['Smith (2000); Brown (2001); Jones (2002); Jones (2003).\nReferences\n'
                        'Smith, A. 2000, ApJ, 1, 2. Brown, B. 2001, private communication. '
                        'Jones, C. 2002, Book of Waves (New York: Academic Press), Chap. 5. '
                        '. 2003, ApJ, 3, 4.'])
        self.assertEqual([ref['label'] for ref in refs], ['Smith 2000', 'Brown 2001', 'Jones 2002', 'Jones 2003'])
        self.assertEqual([ref['citation_count'] for ref in refs], [1, 1, 1, 1])

    def test_flattened_initials_do_not_split_an_unfinished_author_list(self):
        refs = extract(['References\nSmith, A. Brown, B. 2000, ApJ, 1, 2. Jones, C. 2001, ApJ, 3, 4.'])
        self.assertEqual(len(refs), 2)
        self.assertIn('Brown, B.', refs[0]['raw'])

    def test_editors_after_dotted_initials_do_not_start_a_spurious_entry(self):
        refs = extract(['References\nSmith, A. 2008, in Conference Series, Vol. 3, Book Title, ed. R. Howe, '
                        'R. W. Jones, K. S. Brown, & G. J. White, 235–+\n'
                        'Smith, A., Green, B., et al. 2009, ApJ, 4, 5'])
        self.assertEqual([ref['label'] for ref in refs], ['Smith 2008', 'Smith 2009'])
        self.assertIn('R. W. Jones', refs[0]['raw'])

    def test_wrapped_years_with_backreference_pages_do_not_become_entries(self):
        pages = ['See [1, 2].\nReferences\n'
                 '[1] Alice Author. First work. arXiv:2101.00001,\n2021. 6\n'
                 '[2] Bob Writer. Second work. TMLR,\n2026. 1, 3, 18\n']
        refs = extract(pages)
        self.assertEqual([ref['label'] for ref in refs], ['1', '2'])
        self.assertTrue(refs[0]['raw'].endswith('2021. 6'))
        self.assertTrue(refs[1]['raw'].endswith('2026. 1, 3, 18'))
        self.assertEqual([ref['citation_count'] for ref in refs], [1, 1])

    def test_numeric_labels_are_not_confused_with_publication_years(self):
        refs = extract(['References\n[2021] Alice Author. A genuine entry. 2020.\n'
                        '2022. Bob Writer. Another genuine entry. 2019.'])
        self.assertEqual([ref['label'] for ref in refs], ['2021', '2022'])

    def test_venue_in_a_numbered_entry_is_not_a_new_author(self):
        refs = extract(['References\n[1] Alice Author. A conference paper. In Proceedings, pages 12–20,\n'
                        'Brussels, Belgium, October 2018. Association for Computational Linguistics.\n'
                        'doi: 10.1234/example.\n[2] Bob Writer. Another paper. 2020.'])
        self.assertEqual(len(refs), 2)
        self.assertIn('Brussels, Belgium', refs[0]['raw'])
        self.assertIn('10.1234/example', refs[0]['raw'])

    def test_lettered_appendix_ends_the_bibliography_and_preserves_page_numbers(self):
        for heading in ('A Additional Results', 'A. Additional Results', 'A.1 Additional Results',
                        'B Ablations', 'Appendix A: Additional Results', 'Supplementary material'):
            with self.subTest(heading=heading):
                pages = ['Body cites [1].', 'References\n[1] Alice Author. A paper. Journal 4, 2020.\n'
                         + heading + '\nThe appendix cites [1].', 'Later text also cites [1].']
                refs = extract(pages)
                self.assertEqual(len(refs), 1)
                self.assertNotIn(heading, refs[0]['raw'])
                self.assertEqual([o['page'] for o in refs[0]['occurrences']], [1, 2, 3])

    def test_appendix_figure_pages_still_contribute_citations(self):
        refs = extract(['References\n[1] Alice Author. A paper. Journal 4, 2020.\nA Additional Results',
                        '0\n1\n2\n3\n4\n5\n6\n[1]\nTime\nValue'])
        self.assertEqual([o['page'] for o in refs[0]['occurrences']], [2])

    def test_article_starting_with_a_is_not_an_appendix_inside_an_open_entry(self):
        refs = extract(['References\n[1] Alice Author. 2020.\nA Survey of Important Methods.\n'
                        'Journal, 4, 12\n[2] Bob Writer. A second paper. 2021.'])
        self.assertEqual(len(refs), 2)
        self.assertIn('A Survey', refs[0]['raw'])

    def test_lettered_page_heading_after_a_numbered_conference_entry(self):
        refs = extract(['References\n[1] Alice Author and Bob Writer. A study of language models. In ICML, 2026. 3\n16',
                        'A Continuous Language Model Survey\nCompare [1] for this model.'])
        self.assertEqual(len(refs), 1)
        self.assertNotIn('Survey', refs[0]['raw'])
        self.assertEqual([o['page'] for o in refs[0]['occurrences']], [2])

    def test_running_author_headers_are_not_lettered_appendices(self):
        header = 'P. Researcher and E. Scientist: A study of waves'
        refs = extract([header + ' 1\nSmith (2000).',
                        'References\nSmith, A. 2000, ApJ, 1, 2',
                        header + ' 3\nBrown, B. 2001, ApJ, 3, 4'])
        self.assertEqual([ref['label'] for ref in refs], ['Smith 2000', 'Brown 2001'])


    def test_wrapped_author_initials_and_years_are_not_appendix_headings(self):
        refs = extract(['References\nSmith, A., Jones, B.,\nA. Brown, C. Writer. 2020, ApJ, 1, 2\n'
                        'White, D. 2019, ApJ, 3, 4'])
        self.assertEqual(len(refs), 2)
        self.assertIn('A. Brown', refs[0]['raw'])

    def test_removing_backreference_rows_preserves_entry_ids_and_retrieval(self):
        text = ('References\n[1] Alice Author. First work. arXiv:2101.00001,\n2021. 6\n'
                '[2] Bob Writer. Second work. Journal 4, 2020.\n[3] Carol Researcher. A new work. 2022.')
        with TemporaryDirectory() as directory, Library(Path(directory) / 'library') as library:
            references._save(library, 1, {'status': 'partial', 'references': [
                {'id': '0', 'label': '1', 'raw': 'Alice Author. First work. arXiv:2101.00001,',
                 'retrieval': {'status': 'not_found'}},
                {'id': '1', 'label': '2021', 'raw': '6'},
                {'id': '2', 'label': '2', 'raw': 'Bob Writer. Second work. Journal 4, 2020.'}]})
            class FakePdf:
                def __init__(self, _data=b''):
                    self.pages = [SimpleNamespace(extract_text=lambda: text)]
                    self.named_destinations = {}

                def close(self):
                    pass

            with patch.object(library, 'read_pdf', return_value=(b'', 0)), \
                 patch('paperassist.references.Pdf', FakePdf):
                refs = references.analyze(library, 1)['references']
            self.assertEqual({ref['label']: ref['id'] for ref in refs}, {'1': '0', '2': '2', '3': '3'})
            self.assertEqual(refs[0]['retrieval']['status'], 'not_found')


class CitationGrammarTests(unittest.TestCase):
    def test_possessive_author_citations_require_a_year_immediately_after_the_name(self):
        refs = extract(["Smith's (2000) and Smith’s 2000 agree. Smith's model was measured in 2000.\n"
                        'References\nSmith, A. 2000, J, 1, 2'])
        self.assertEqual(refs[0]['citation_count'], 2)

    def test_cross_page_text_is_one_event_with_each_page_retained(self):
        refs = extract(['Earlier Smith et al. are mentioned. At the bottom Smith et al.',
                        '(2000) supports the result.\nReferences\nSmith, A., Jones, B., & Brown, C. 2000, J, 1, 2'])
        self.assertEqual(refs[0]['citation_count'], 1)
        parts = refs[0]['occurrences'][0]['marker_parts']
        self.assertEqual([(part['page'], part['marker'], part['ordinal']) for part in parts],
                         [(1, 'Smith et al.', 1), (2, '(2000', 0)])

    def test_cross_page_matching_does_not_jump_over_blank_bibliography_pages(self):
        refs = extract(['Smith et al.', 'References\nSmith, A., Jones, B., & Brown, C. 2000, J, 1, 2',
                        'Uncited bibliography continuation', 'Appendix A\n(2000) is a measurement.'])
        self.assertEqual(refs[0]['citation_count'], 0)

    def test_explicit_three_author_list_uses_the_known_names(self):
        refs = extract(['Fang, Xia, and Keppens (2013); Fang, Xia & Keppens, 2013. '
                        'Fang discussed Xia and Keppens in 2013.\nReferences\n'
                        'Fang, C., Xia, C., & Keppens, R. 2013, ApJ, 1, 2'])
        self.assertEqual(refs[0]['citation_count'], 2)

    def test_explicit_author_lists_disambiguate_same_first_author_and_year(self):
        refs = extract(['Fang, Xia, and Keppens (2013). Fang, Xia, and Smith (2013).\nReferences\n'
                        'Fang, C., Xia, C., & Keppens, R. 2013, ApJ, 1, 2\n'
                        'Fang, C., Xia, C., & Smith, A. 2013, ApJ, 3, 4'])
        self.assertEqual([ref['citation_count'] for ref in refs], [1, 1])

    def test_known_compound_second_surname_and_second_author_exclusion(self):
        refs = extract(['Landini & Monsignori Fossi (1970).\nReferences\n'
                        'Landini, M., & Monsignori Fossi, B. C. 1970, A&A, 1, 2\n'
                        'Monsignori Fossi, B. C. 1970, A&A, 3, 4'])
        self.assertEqual([ref['citation_count'] for ref in refs], [1, 0])

    def test_compressed_semicolon_year_lists(self):
        refs = extract(['Chou (1999; 2001); Chou 1999; 2001.\nReferences\n'
                        'Chou, A. 1999, J, 1, 2\nChou, A. 2001, J, 3, 4'])
        self.assertEqual([ref['citation_count'] for ref in refs], [2, 2])

    def test_kerning_broken_year_and_et_al(self):
        refs = extract(['Smith et a l. (20 05), Smith et al. 2 0 0 5.\nReferences\n'
                        'Smith, A., Jones, B., & Brown, C. 2005, J, 1, 2'])
        self.assertEqual(refs[0]['citation_count'], 2)

    def test_explicit_three_author_oxford_comma_and_without_comma(self):
        refs = extract(['Smith, Jones, & Brown 2020; Smith, Jones, and Brown (2020); Smith, Jones & Brown 2020.\n'
                        'References\n'
                        'Smith, A., Jones, B., & Brown, C. 2020, ApJ, 1, 2\n'
                        'Brown, C. 2020, ApJ, 3, 4'])
        self.assertEqual([r['citation_count'] for r in refs], [3, 0])

    def test_numeric_superscript_and_parenthesized_citations_in_text(self):
        refs = extract([
            'Observed earlier¹˒²⁻³ and confirmed (1, 3). Ignore Eq. (2), Fig. (1), 10³, cm⁻², (2020).\n'
            'References\n'
            '[1] First Author. 2020, J, 1, 1\n'
            '[2] Second Author. 2021, J, 2, 2\n'
            '[3] Third Author. 2022, J, 3, 3\n'
        ])
        self.assertEqual([r['citation_count'] for r in refs], [2, 1, 2])

    def test_grouped_numeric_ranges_in_prose_split_brackets_prefixed_and_unicode_dashes(self):
        bib = '\nReferences\n' + '\n'.join(f'[{i}] Author{i}, A. 2020, ApJ, 1, {i}' for i in range(1, 20))
        cases = [
            'Refer to papers 10-17.',
            'See Refs. 10–17.',
            'See References 10 to 17.',
            'See papers 10, 11–16, and 17.',
            'See [10]–[17].',
            'See [10]-[17].',
            'See [10] to [17].',
            'See (10)–(17).',
            'See (10) to (17).',
            'See [see 10–17].',
            'See [e.g., 10–17].',
            'See [Refs. 10–17].',
            'See [10–17, and references therein].',
            'See [10\u201017].',
            'See [10\u201217].',
            'See [10--17].',
            'Work¹⁰\u2010¹⁷ shows this.',
        ]
        for body in cases:
            with self.subTest(body=body):
                refs = {r['label']: r['citation_count'] for r in extract([body + bib])}
                self.assertEqual([refs[str(i)] for i in range(10, 18)], [1] * 8)

        # Comma + split range and single Ref.
        refs = {r['label']: r['citation_count'] for r in extract(['See [10], [11]–[17] and Ref. 10.' + bib])}
        self.assertEqual(refs['10'], 2)
        self.assertEqual([refs[str(i)] for i in range(11, 18)], [1] * 7)

        # Negative controls must not falsely count numbers.
        negatives = [
            'In this paper 10 galaxies were observed.',
            'In this paper 10-17 galaxies were observed.',
            'Eqs. (10)–(17) and Figs. (1)–(3) and Section 10-17.',
            'Values [10-17 km] and [Figure 1-3] and papers 10-17 km.',
        ]
        for body in negatives:
            with self.subTest(negative=body):
                refs = {r['label']: r['citation_count'] for r in extract([body + bib])}
                self.assertEqual([refs[str(i)] for i in range(1, 20)], [0] * 19)

        # "papers 10-17 and 3 figures" must count 10..17, not 3.
        refs = {r['label']: r['citation_count'] for r in extract(['Refer to papers 10-17 and 3 figures.' + bib])}
        self.assertEqual(refs['3'], 0)
        self.assertEqual([refs[str(i)] for i in range(10, 18)], [1] * 8)


if __name__ == '__main__':
    unittest.main()
