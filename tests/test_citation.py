import unittest

from paperassist.citation import (describe, first_surname, generic_publication, journal_code, pdf_name, printed_references,
                                  publication, same_surname, surnames)


class CitationTests(unittest.TestCase):
    def test_journal_abbreviations_and_names_share_one_fingerprint(self):
        cases = {
            'Klimchuk, J. 2006, SoPh, 234, 41': 'SoPh 234, 41 (2006)',
            'Klimchuk 2006, Sol. Phys., 234, 41': 'SoPh 234, 41 (2006)',
            'Sol Phys (2006) 234: 41-77': 'SoPh 234, 41 (2006)',
            'Wedemeyer 2012, Natur, 486, 505': 'Natur 486, 505 (2012)',
            'Wedemeyer 2012, Nature, 486, 505': 'Natur 486, 505 (2012)',
            'De Pontieu 2014, Sci, 346, D315': 'Sci 346, D315 (2014)',
            'Reale 2014, Liv. Rev. Sol. Phys., 11, 4': 'LRSP 11, 4 (2014)',
            'Chen, P. F. 2011, Living Rev. Solar Phys., 8, 1': 'LRSP 8, 1 (2011)',
            'Powell 1999, JCoPh, 154, 284': 'JCoPh 154, 284 (1999)',
            'Powell 1999, J. Comput. Phys., 154, 284': 'JCoPh 154, 284 (1999)',
            'Smith 2007, PhRvL, 99, 021101': 'PhRvL 99, 021101 (2007)',
            'Smith 2007, Phys. Rev. Lett. 99, 021101': 'PhRvL 99, 021101 (2007)',
            'Schindler 1988, JGR, 93, 5547': 'JGR 93, 5547 (1988)',
            'Anders 1989, GeCoA, 53, 197': 'GeCoA 53, 197 (1989)',
            'Feldman 1992, PhyS, 46, 202': 'PhyS 46, 202 (1992)',
            'Hood 1981, GApFD, 17, 297': 'GApFD 17, 297 (1981)',
            'Dere 1997, A&AS, 125, 149': 'A&AS 125, 149 (1997)',
            'Smith 2000, MNRAS, 300, 1': 'MNRAS 300, 1 (2000)',
            'Smith 2000, Astron. Astrophys., 353, 1074': 'A&A 353, 1074 (2000)',
            'A&A 485, 837–848 (2008)': 'A&A 485, 837 (2008)',
            'Astrophys.J. 771 (2013) 76': 'ApJ 771, 76 (2013)',
        }
        for text, expected in cases.items():
            # Reference lists print the year before the journal; page headers print it inside.
            self.assertEqual(describe(publication(text, expected[-5:-1])), expected, text)

    def test_unknown_journal_and_missing_year_give_no_fingerprint(self):
        self.assertIsNone(publication('Alexiades 1996, Int. Journal Num. Meth. in Biomed. Eng. , 12, 31', '1996'))
        self.assertIsNone(publication('ApJ, 170, 228'))

    def test_journal_code_reads_publisher_names(self):
        self.assertEqual(journal_code('The Astrophysical Journal'), 'APJ')
        self.assertEqual(journal_code('Astronomy &amp; Astrophysics'), 'A&A')
        self.assertEqual(journal_code('Living Rev. Solar Phys.'), journal_code('Living Reviews in Solar Physics'))
        self.assertIsNone(journal_code('Journal of Imaginary Results'))

    def test_surnames_ignore_case_diacritics_and_particles(self):
        self.assertTrue(same_surname('Török', 'torok'))
        self.assertTrue(same_surname('Ballegooijen', 'van Ballegooijen'))
        self.assertFalse(same_surname('Reale', 'Real'))
        self.assertEqual(surnames('Török, T., Kliem, B., & Van der Linden, R. A. M. 2004, A&A'),
                         ['Török', 'Kliem', 'Van der Linden'])

    def test_first_surname_reads_every_common_author_format(self):
        cases = {
            'Kohutova P., Verwichte E.': 'Kohutova', 'Dere K. P., Landi E., Mason H. E.': 'Dere',
            'S.P. Moschou et al.': 'Moschou', 'Kohutova, P., Verwichte, E.': 'Kohutova',
            'Lennox L. Cowie; Christopher F. McKee': 'Cowie', 'P. M. EDWIN; B. ROBERTS': 'EDWIN',
            'Jerčić V., Keppens R.': 'Jerčić', 'van der Voort L. R.': 'van der Voort',
            'Aaron van den Oord, Oriol Vinyals': 'van den Oord', 'Müller D. A. N., Peter H.': 'Müller',
            'Aaron Lou, Chenlin Meng': 'Lou', 'J.-L. Lions and R. Temam': 'Lions', 'Andrade Jr., J. S.': 'Andrade', '': '',
        }
        for authors, expected in cases.items():
            with self.subTest(authors=authors):
                self.assertEqual(first_surname(authors), expected)
        self.assertTrue(same_surname('Kohutova', first_surname('Kohutova P., Verwichte E.')))
        self.assertTrue(same_surname('Voort', first_surname('van der Voort L. R.')))
        self.assertFalse(same_surname('Kohutova', first_surname('Verwichte E., Kohutova P.')))

    def test_printed_references_need_journal_volume_and_first_page(self):
        lines = ['1984ApJ. . .279. .857R', 'The Astrophysical Journal, 279:857-865, 1984 April 15', '19 91MNRAS.251. .379R',
                 'Mon. Not. R. astr. Soc. (1991) 251, 379-390', '2013ApJ...771L..29F', 'THE ASTROPHYSICAL JOURNAL, 512:985\u00a8991, 1999 February 20']
        self.assertEqual(printed_references(lines), [['APJ', '279', '857', '1984'], ['MNRAS', '251', '379', '1991'],
                                                     ['APJL', '771', 'L29', '2013'], ['APJ', '512', '985', '1999']])
        for line in ('The Astrophysical Journal, 1984 April 15', 'Received 1983 July 19; accepted 1983 October 5',
                     '2015Key aspectsofcoronalheating', '1984Foo. . .279. .857R', 'Astrophysical Journal 1984'):
            with self.subTest(line=line):
                self.assertEqual(printed_references([line]), [])

    def test_pdf_names_are_ascii_and_fall_back_to_author_year(self):
        fingerprint = ['APJ', '771', '76', '2013']
        self.assertEqual(pdf_name('Rappazzo', 2013, fingerprint), 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertEqual(pdf_name('Török', 2004, ['A&A', '413', 'L27', '2004']), 'Torok_2004_AA_413_L27.pdf')
        self.assertEqual(pdf_name('De Pontieu', '2014', None), 'DePontieu_2014.pdf')
        self.assertEqual(pdf_name('Wedemeyer-Böhm', 2012), 'Wedemeyer-Bohm_2012.pdf')

    def test_unlisted_journal_tail_is_parsed_but_theses_are_not(self):
        self.assertEqual(generic_publication('Foo, B. 2012, Obscure J. Res., 12, E45–50.'), ('2012', '12', 'E45', 'Obscure J. Res.'))
        self.assertEqual(publication('Bareford, P. K. 2015, RSPTA, 373, 20140266', '2015'), ['RSPTA', '373', '20140266', '2015'])
        thesis = 'Tam, K. V. 2014, PhD thesis, St Andrews Univ., http://hdl.handle.net/ 10023/6373'
        self.assertIsNone(generic_publication(thesis))
