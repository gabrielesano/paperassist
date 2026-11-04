import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paperassist.metadata import extract_metadata


class MetadataTests(unittest.TestCase):
    def test_first_page_doi_is_only_a_candidate(self):
        page = SimpleNamespace(get_contents=lambda: None,
                               extract_text=lambda: 'A cited work doi:10.1234/cited. arXiv:1505.05333')
        reader = SimpleNamespace(is_encrypted=False, pages=[page], metadata=None, xmp_metadata=None)
        with patch('paperassist.metadata.PdfReader', return_value=reader):
            result = extract_metadata(Path('unused'), 'untitled.pdf')
        self.assertIsNone(result['doi'])
        self.assertEqual(result['candidates']['doi'], ['10.1234/cited'])
        self.assertEqual(result['candidates']['arxiv'], ['1505.05333'])
        self.assertEqual(result['sources']['title'], 'filename')
        self.assertIsNone(result['year'])

    def test_bibcode_and_first_page_boilerplate_remain_candidates(self):
        examples = [
            ('1978ApJ...220..643R', '© 1984. The American Astronomical Society. All rights reserved. Printed in U.S.A.', 'Roberts1984'),
            ('Untitled', 'Typeset using LATEX default style in AASTeX62', 'Antolin2018'),
            ('', 'George B. Field', 'Field-1965-Thermal-instability'),
            ('', 'Università degli Studi di Palermo', 'Tesi_Gabriele_Sano'),
        ]
        for info_title, text, filename in examples:
            with self.subTest(filename=filename):
                page = SimpleNamespace(get_contents=lambda: None, extract_text=lambda: text)
                info = SimpleNamespace(title=info_title, author='')
                reader = SimpleNamespace(is_encrypted=False, pages=[page], metadata=info, xmp_metadata=None)
                with patch('paperassist.metadata.PdfReader', return_value=reader):
                    result = extract_metadata(Path('unused'), filename + '.pdf')
                self.assertEqual(result['title'], filename)
                self.assertEqual(result['sources']['title'], 'filename')
                self.assertNotIn(text, result['candidates']['title'])

    def test_first_page_title_remains_reviewable_candidate(self):
        title = 'PLUTO: a Numerical Code for Computational Astrophysics'
        page = SimpleNamespace(get_contents=lambda: None, extract_text=lambda: title)
        reader = SimpleNamespace(is_encrypted=False, pages=[page], metadata=None, xmp_metadata=None)
        with patch('paperassist.metadata.PdfReader', return_value=reader):
            result = extract_metadata(Path('unused'), 'Mignone2007.pdf')
        self.assertEqual(result['title'], 'Mignone2007')
        self.assertIn(title, result['candidates']['title'])
