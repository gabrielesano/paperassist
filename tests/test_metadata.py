import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paperassist.metadata import PARSER_VERSION, extract_metadata, extract_ocr_metadata, useful


def line(text, top, height, confidence=95):
    """A synthetic OCR line; words are laid out left to right."""
    words = [{"text": word, "bbox": [60 + 40 * index, top, 90 + 40 * index, top + height], "confidence": confidence}
             for index, word in enumerate(text.split())]
    return {"text": text, "bbox": [60, top, 60 + 40 * len(words), top + height], "confidence": confidence,
            "words": list(range(len(words))), "_words": words}


def page_report(lines, number=0, size=(612, 792)):
    words = []
    for entry in lines:
        offset = len(words)
        words.extend(entry.pop("_words"))
        entry["words"] = [offset + index for index in entry["words"]]
    return {"pages": [{"page": number, "size": list(size), "words": words, "lines": lines,
                       "text": "\n".join(entry["text"] for entry in lines)}]}


def body_lines(start=300):
    return [line(f"ordinary sentence number {number} of the paper body", start + 12 * number, 8) for number in range(8)]


class OcrMetadataTests(unittest.TestCase):
    def test_title_authors_and_provenance_come_from_the_prominent_lines(self):
        report = page_report([line("Heating of Coronal", 60, 20), line("Loops by Waves", 85, 20),
                              line("Ada Lovelace and Grace Hopper", 120, 11), line("Abstract", 180, 10), *body_lines()])
        result = extract_ocr_metadata(report)
        self.assertEqual(result["title"], "Heating of Coronal Loops by Waves")
        self.assertEqual(result["authors"], "Ada Lovelace; Grace Hopper")
        self.assertEqual(result["sources"], {"title": "ocr_title", "authors": "ocr_authors"})
        self.assertEqual(result["evidence"]["title_confidence"], 95)

    def test_capital_titles_at_body_size_are_recognised_but_author_lines_are_not_merged(self):
        report = page_report([line("THE ASTROPHYSICAL JOURNAL, 330:474-479, 1988", 20, 8), line("NANOFLARES AND THE SOLAR CORONA", 60, 9),
                              line("E. N. PARKER", 75, 9), line("ABSTRACT", 110, 9), *body_lines()])
        result = extract_ocr_metadata(report)
        self.assertEqual(result["title"], "NANOFLARES AND THE SOLAR CORONA")
        self.assertEqual(result["authors"], "E. N. PARKER")

    def test_body_year_and_ocr_doi_are_only_candidates(self):
        report = page_report([line("Heating of Coronal Loops", 60, 20), line("Ada Lovelace", 100, 11),
                              line("Observed in 1998 and doi:10.1234/loops.5.", 140, 8), *body_lines()])
        result = extract_ocr_metadata(report)
        self.assertIsNone(result["year"])
        self.assertNotIn("year", result["sources"])
        self.assertIn(1998, result["candidates"]["year"])
        self.assertEqual(result["candidates"]["doi"], ["10.1234/loops.5"])
        self.assertEqual(result["identifier_sources"]["doi"], {"10.1234/loops.5": "ocr"})

    def test_explicit_arxiv_submission_stamp_may_supply_the_year(self):
        report = page_report([line("Heating of Coronal Loops", 60, 20), line("Ada Lovelace", 100, 11),
                              line("arXiv:1807.00395v1 [astro-ph.SR] 1 Jul 2018", 140, 8), *body_lines()])
        result = extract_ocr_metadata(report)
        self.assertEqual((result["year"], result["sources"]["year"]), (2018, "ocr_arxiv_submission_date"))

    def test_low_confidence_or_untyped_text_yields_no_title(self):
        noisy = page_report([line("Heating of Coronal Loops", 60, 20, confidence=40), *body_lines()])
        plain = page_report([line("Heating of Coronal Loops", 60, 8), *body_lines()])
        self.assertEqual(extract_ocr_metadata(noisy), {})
        self.assertEqual(extract_ocr_metadata(plain), {})
        self.assertEqual(extract_ocr_metadata({"pages": []}), {})

    def test_cover_page_is_skipped_for_the_next_page(self):
        cover = page_report([line("Downloaded from the publisher archive", 60, 20)], number=0)["pages"][0]
        paper = page_report([line("Heating of Coronal Loops", 60, 20), line("Ada Lovelace", 100, 11), *body_lines()], number=1)["pages"][0]
        self.assertEqual(extract_ocr_metadata({"pages": [cover, paper]})["title"], "Heating of Coronal Loops")


class MetadataTests(unittest.TestCase):
    def test_first_page_doi_is_only_a_candidate(self):
        page = SimpleNamespace(get_contents=lambda: None,
                               extract_text=lambda **kwargs: 'A cited work doi:10.1234/cited. arXiv:1505.05333')
        reader = SimpleNamespace(is_encrypted=False, pages=[page], metadata=None, xmp_metadata=None)
        with patch('paperassist.metadata.PdfReader', return_value=reader):
            result = extract_metadata(Path('unused'), 'untitled.pdf')
        self.assertIsNone(result['doi'])
        self.assertEqual(result['candidates']['doi'], ['10.1234/cited'])
        self.assertEqual(result['candidates']['arxiv'], ['1505.05333'])
        self.assertEqual(result['sources']['title'], 'arxiv_id')
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
                page = SimpleNamespace(get_contents=lambda: None, extract_text=lambda **kwargs: text)
                info = SimpleNamespace(title=info_title, author='')
                reader = SimpleNamespace(is_encrypted=False, pages=[page], metadata=info, xmp_metadata=None)
                with patch('paperassist.metadata.PdfReader', return_value=reader):
                    result = extract_metadata(Path('unused'), filename + '.pdf')
                self.assertEqual(result['title'], filename)
                self.assertEqual(result['sources']['title'], 'filename')
                self.assertNotIn(text, result['candidates']['title'])

    def test_first_page_title_remains_reviewable_candidate(self):
        title = 'PLUTO: a Numerical Code for Computational Astrophysics'
        page = SimpleNamespace(get_contents=lambda: None, extract_text=lambda **kwargs: title)
        reader = SimpleNamespace(is_encrypted=False, pages=[page], metadata=None, xmp_metadata=None)
        with patch('paperassist.metadata.PdfReader', return_value=reader):
            result = extract_metadata(Path('unused'), 'Mignone2007.pdf')
        self.assertEqual(result['title'], 'Mignone2007')
        self.assertIn(title, result['candidates']['title'])

    def extract(self, text='', filename='download.pdf', info=None, xmp=None, spans=()):
        def extract_text(visitor_text):
            for value, y, size in spans:
                visitor_text(value, [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 40, y], None, size)
            return text
        page = SimpleNamespace(get_contents=lambda: None, extract_text=extract_text,
                               mediabox=SimpleNamespace(height=800), rotation=0)
        reader = SimpleNamespace(is_encrypted=False, pages=[page], metadata=info, xmp_metadata=xmp)
        with patch('paperassist.metadata.PdfReader', return_value=reader):
            return extract_metadata(Path('unused'), filename)

    def test_explicit_info_and_xmp_identifiers_keep_individual_provenance(self):
        info = SimpleNamespace(title='download', author='', get={
            '/DOI': 'https://doi.org/10.1234/OWN', '/arXivID': '2501.12345v2'}.get)
        xmp = SimpleNamespace(dc_identifier=['doi:10.1234/xmp', 'https://arxiv.org/abs/2401.12345v1'],
                              dc_title={}, dc_creator=[])
        result = self.extract('A citation doi:10.1234/cited', info=info, xmp=xmp)
        self.assertEqual(result['identifier_sources']['doi'], {
            '10.1234/own': 'pdf_info', '10.1234/xmp': 'xmp', '10.1234/cited': 'first_page'})
        self.assertEqual(result['identifier_sources']['arxiv'], {
            '2501.12345v2': 'pdf_info', '2401.12345v1': 'xmp'})
        self.assertIsNone(result['doi'])
        self.assertEqual(result['parser_version'], PARSER_VERSION)

    def test_arxiv_urls_filenames_and_arxiv_dois_are_candidates(self):
        for value, filename, expected, source in (
            ('https://arxiv.org/pdf/astro-ph/0601001v2.pdf', 'paper.pdf', 'astro-ph/0601001v2', 'first_page'),
            ('', '2406.07524v2.pdf', '2406.07524v2', 'filename'),
            ('doi:10.48550/arXiv.2501.12345', 'paper.pdf', '2501.12345', 'first_page')):
            with self.subTest(value=value, filename=filename):
                result = self.extract(value, filename)
                self.assertEqual(result['candidates']['arxiv'], [expected])
                self.assertEqual(result['identifier_sources']['arxiv'][expected], source)
                self.assertIsNone(result['year'])

    def test_multiline_prominent_title_and_authors_replace_generic_name_offline(self):
        lines = ['Parallel Sampling for', 'Discrete Language Models', 'Ada Lovelace*1, Grace Hopper2',
                 'Department of Computer Science, Example University', 'Abstract',
                 'We present an unrelated description of a method.']
        spans = [(lines[0], 720, 18), (lines[1], 699, 18), (lines[2], 665, 12),
                 (lines[3], 645, 10), (lines[4], 620, 10), (lines[5], 600, 10)]
        result = self.extract('\n'.join(lines), spans=spans)
        self.assertEqual(result['title'], 'Parallel Sampling for Discrete Language Models')
        self.assertEqual(result['authors'], 'Ada Lovelace; Grace Hopper')
        self.assertEqual(result['sources']['title'], 'first_page_typography')
        self.assertEqual(result['sources']['authors'], 'first_page_authors')
        self.assertIsNone(result['year'])
        self.assertNotIn('unrelated description', result['header_text'])
        self.assertEqual(result['candidates']['title'][0], result['title'])

    def test_author_names_institutions_and_abstract_body_cannot_be_display_titles(self):
        examples = [
            [('Ada Lovelace', 720, 18), ('Example University', 690, 10), ('Abstract', 660, 10),
             ('We present an unrelated description of a method.', 640, 10)],
            [('Example University', 720, 18), ('Department of Computer Science', 690, 10),
             ('Abstract', 660, 10), ('A short description of the method.', 640, 10)],
            [('Abstract', 720, 10), ('A Prominent Description Of Our Method', 690, 18),
             ('Another sentence describing our approach.', 665, 10), ('Further body prose.', 640, 10)],
        ]
        for spans in examples:
            with self.subTest(spans=spans):
                result = self.extract('\n'.join(line[0] for line in spans), spans=spans)
                self.assertEqual(result['title'], 'download')
                self.assertEqual(result['authors'], '')

    def test_printed_arxiv_submission_date_is_provisional_year_not_creation_date(self):
        info = SimpleNamespace(title='A full paper title', author='', get={
            '/CreationDate': "D:19980101000000Z"}.get)
        result = self.extract('arXiv:2610.14162v1 [cs.CL] 9 Oct 2026\nCited work appeared in 1999.', info=info)
        self.assertEqual(result['year'], 2026)
        self.assertEqual(result['sources']['year'], 'arxiv_submission_date')
        for text in ('arXiv:2610.14162v1\nCited works 1998, 2001 and 2020.', 'Published 2026. Created 1998.'):
            with self.subTest(text=text):
                self.assertIsNone(self.extract(text, info=info)['year'])

    def test_generic_titles_are_rejected_and_ambiguous_ids_do_not_become_display_titles(self):
        for value in ('download', 'download (2)', 'file_2', 'unknown', 'Untitled'):
            self.assertFalse(useful(value), value)
        result = self.extract('arXiv:2501.12345v1')
        self.assertEqual(result['title'], 'arXiv:2501.12345v1')
        self.assertEqual(result['sources']['title'], 'arxiv_id')
        self.assertEqual(self.extract('arXiv:2501.12345v1 and arXiv:2501.12346v1')['title'], 'download')
        ambiguous = self.extract('arXiv:2501.12345v1 [cs.CL] 9 Jan 2025\narXiv:2501.12346v1')
        self.assertIsNone(ambiguous['year'])

    def test_figure_labels_and_organization_names_are_not_authors(self):
        lines = ['A study of discrete token generation', 'Inception Labs', 'Ada Lovelace, Grace Hopper',
                 'Example University', 'Project Page: example.org', 'Cross Entropy', 'Abstract']
        info = SimpleNamespace(title=lines[0], author='', get={}.get)
        result = self.extract('\n'.join(lines), info=info)
        self.assertEqual(result['authors'], 'Ada Lovelace; Grace Hopper')

    def test_title_continuations_may_be_a_single_word_or_include_model_numbers(self):
        for first, second in [('Text Diffusion with Encoder-Decoder', 'Transformers'),
                              ('Generating Diverse High-Fidelity Images', 'with VQ-VAE-2')]:
            lines = [first, second, 'Ada Lovelace', 'Abstract', 'A short description of our results.']
            spans = [(first, 720, 18), (second, 699, 18), (lines[2], 665, 12),
                     (lines[3], 640, 10), (lines[4], 620, 10), ('Additional body text.', 600, 10)]
            result = self.extract('\n'.join(lines), spans=spans)
            self.assertEqual(result['title'], first + ' ' + second)

    def test_later_arxiv_revision_date_is_not_the_original_paper_year(self):
        for stamp, expected in [('arXiv:1312.3005v3 [cs.CL] 4 Mar 2014', None),
                                 ('arXiv:1312.3005v1 [cs.CL] 4 Dec 2013', 2013),
                                 ('arXiv:astro-ph/0601001v2 4 Mar 2007', None),
                                 ('arXiv:astro-ph/0601001v1 4 Jan 2006', 2006)]:
            with self.subTest(stamp=stamp):
                result = self.extract(stamp)
                self.assertEqual(result['year'], expected)
                self.assertIn(int(stamp[-4:]), result['candidates']['year'])
