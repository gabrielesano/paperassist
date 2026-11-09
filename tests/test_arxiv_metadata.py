"""arXiv metadata accepts one corroborated identity and never downloads a PDF."""
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from paperassist.arxiv_metadata import lookup
from paperassist.settings import Settings


def entry(identifier="2610.12345v1", title="Parallel models for text generation", author="Ada Lovelace", year=2026, doi=""):
    return (f"<entry><id>https://arxiv.org/abs/{identifier}</id><title>{title}</title>"
            f"<author><name>{author}</name></author><published>{year}-10-01T00:00:00Z</published>"
            f"<arxiv:doi>{doi}</arxiv:doi></entry>")


def feed(*entries, total=None):
    total = len(entries) if total is None else total
    return ("<feed xmlns='http://www.w3.org/2005/Atom' xmlns:arxiv='http://arxiv.org/schemas/atom' "
            "xmlns:opensearch='http://a9.com/-/spec/opensearch/1.1/'>"
            f"<opensearch:totalResults>{total}</opensearch:totalResults>" + "".join(entries) + "</feed>").encode()


class ArxivMetadataTests(unittest.TestCase):
    def document(self, identifiers=("2610.12345v1",), **changes):
        document = {"title": "Parallel models for text generation", "authors": "Ada Lovelace", "year": None,
                    "metadata_status": "needs_review", "metadata": {
                        "sources": {"title": "pdf_info"}, "candidates": {"arxiv": list(identifiers), "title": []}}}
        document.update(changes)
        return document

    def lookup(self, document, data, cfg=None):
        with patch("paperassist.arxiv_metadata.retrieval._get", return_value=data) as get:
            with patch("paperassist.retrieval._download_pdf") as download:
                record = lookup(document, cfg or Settings(), threading.Event())
        download.assert_not_called()
        return record, get

    def test_explicit_identifier_fetches_complete_metadata_without_pdf_download(self):
        record, get = self.lookup(self.document(), feed(entry(doi="10.1234/models")))
        self.assertEqual((record["title"], record["authors"], record["year"], record["doi"]),
                         ("Parallel models for text generation", "Ada Lovelace", 2026, "10.1234/models"))
        self.assertEqual(record["first_author"], "Lovelace")
        self.assertEqual(record["provider"], "arxiv")
        self.assertEqual(parse_qs(urlsplit(get.call_args.args[0]).query)["id_list"], ["2610.12345v1"])
        self.assertTrue(get.call_args.kwargs["fresh"])

    def test_strong_pdf_identifier_does_not_require_existing_metadata(self):
        document = self.document(title="download", authors="")
        document["metadata"].update(sources={"title": "filename"}, identifier_sources={"arxiv": {"2610.12345v1": "pdf_info"}})
        record, _ = self.lookup(document, feed(entry()))
        self.assertIsNotNone(record)

    def test_citation_or_filename_identifier_requires_title_and_available_author(self):
        for title, author in (("Another unrelated scientific title", "Ada Lovelace"),
                              ("Parallel models for text generation", "Grace Hopper")):
            with self.subTest(title=title, author=author):
                record, _ = self.lookup(self.document(title=title, authors=author), feed(entry()))
                self.assertIsNone(record)

    def test_multiline_header_corroborates_title_and_author_without_info_metadata(self):
        document = self.document(title="download", authors="")
        document["metadata"].update(sources={"title": "filename"}, header_text="Parallel models for\ntext generation\nAda Lovelace")
        record, _ = self.lookup(document, feed(entry()))
        self.assertIsNotNone(record)

    def test_title_and_author_search_is_bounded_and_rejects_homonymous_results(self):
        document = self.document(identifiers=())
        record, get = self.lookup(document, feed(entry()))
        self.assertIsNotNone(record)
        params = parse_qs(urlsplit(get.call_args.args[0]).query)
        self.assertEqual(params["max_results"], ["10"])
        self.assertIn('au:"Lovelace"', params["search_query"][0])
        for data in (feed(entry(), entry("2610.12346v1")), feed(entry(), total=11),
                     feed(entry(author="Grace Hopper"))):
            record, _ = self.lookup(document, data)
            self.assertIsNone(record)

    def test_explicit_ids_are_batched_and_unrequested_records_are_rejected(self):
        document = self.document(identifiers=("2610.12345v1", "2610.12346"))
        record, get = self.lookup(document, feed(entry("2610.12347")))
        self.assertIsNone(record)
        get.assert_called_once()
        self.assertEqual(parse_qs(urlsplit(get.call_args.args[0]).query)["id_list"], ["2610.12345v1,2610.12346"])

    def test_disabled_corrected_cancelled_and_unsearchable_papers_make_no_requests(self):
        cancel = threading.Event()
        for document, cfg in ((self.document(), Settings({"enabled": {"arxiv": False}})),
                              (self.document(metadata_status="corrected"), Settings()),
                              (self.document(identifiers=(), authors=""), Settings()),
                              (self.document(identifiers=tuple(f"2610.{n:05}" for n in range(6))), Settings())):
            with patch("paperassist.arxiv_metadata.retrieval._get") as get:
                self.assertIsNone(lookup(document, cfg, cancel))
                get.assert_not_called()
        cancel.set()
        with patch("paperassist.arxiv_metadata.retrieval._get") as get:
            self.assertIsNone(lookup(self.document(), Settings(), cancel))
            get.assert_not_called()

    def test_atom_outage_falls_back_to_explicit_abstract_citation_metadata(self):
        html = b'''<html><head><meta name="citation_title" content="Parallel models for text generation">
            <meta name="citation_author" content="Lovelace, Ada"><meta name="citation_date" content="2026/10/01">
            <meta name="citation_arxiv_id" content="2610.12345"></head></html>'''
        for failure in (OSError("Temporary API failure"), b"invalid XML"):
            with self.subTest(failure=failure):
                with patch("paperassist.arxiv_metadata.retrieval._get", side_effect=[failure, html]) as get:
                    record = lookup(self.document(), Settings(), threading.Event())
                self.assertEqual(record["authors"], "Ada Lovelace")
                self.assertEqual(record["evidence"]["method"], "abstract_meta")
                self.assertEqual(get.call_args.args[0], "https://arxiv.org/abs/2610.12345v1")

    def test_wrong_fallback_identifier_and_search_outages_are_reported(self):
        html = b'''<meta name="citation_title" content="Parallel models for text generation">
            <meta name="citation_author" content="Ada Lovelace"><meta name="citation_date" content="2026/10/01">
            <meta name="citation_arxiv_id" content="2610.99999">'''
        with patch("paperassist.arxiv_metadata.retrieval._get", side_effect=[OSError("Offline"), html]):
            with self.assertRaisesRegex(ValueError, "different identifier"):
                lookup(self.document(), Settings(), threading.Event())
        with patch("paperassist.arxiv_metadata.retrieval._get", side_effect=OSError("Offline")) as get:
            with self.assertRaises(OSError):
                lookup(self.document(identifiers=()), Settings(), threading.Event())
            get.assert_called_once()

    def test_confirmed_doi_conflict_is_rejected_but_own_arxiv_doi_is_an_alias(self):
        document = self.document(doi="10.1234/confirmed")
        document["metadata"]["identifier_sources"] = {"arxiv": {"2610.12345v1": "pdf_info"}}
        record, _ = self.lookup(document, feed(entry(doi="10.1234/different")))
        self.assertIsNone(record)
        document["doi"] = "10.48550/arXiv.2610.12345"
        record, _ = self.lookup(document, feed(entry(doi="10.1234/different")))
        self.assertIsNotNone(record)

    def test_requested_version_is_exact_and_unversioned_html_still_needs_corroboration(self):
        record, _ = self.lookup(self.document(), feed(entry(identifier="2610.12345v2")))
        self.assertIsNone(record)
        document = self.document(title="download", authors="")
        document["metadata"].update(sources={"title": "filename"}, identifier_sources={"arxiv": {"2610.12345v1": "pdf_info"}})
        html = b'''<meta name="citation_title" content="Parallel models for text generation">
            <meta name="citation_author" content="Ada Lovelace"><meta name="citation_date" content="2026/10/01">
            <meta name="citation_arxiv_id" content="2610.12345">'''
        with patch("paperassist.arxiv_metadata.retrieval._get", side_effect=[OSError("Offline"), html]):
            self.assertIsNone(lookup(document, Settings(), threading.Event()))

    def test_unversioned_doi_alias_never_bypasses_explicit_pdf_version(self):
        document = self.document(identifiers=("2610.12345", "2610.12345v1"))
        document["metadata"]["identifier_sources"] = {"arxiv": {
            "2610.12345": "pdf_info", "2610.12345v1": "pdf_info"}}
        for returned_version, accepted in (("2610.12345v1", True), ("2610.12345v2", False)):
            with self.subTest(returned_version=returned_version):
                record, get = self.lookup(document, feed(entry(identifier=returned_version)))
                self.assertEqual(record is not None, accepted)
                self.assertEqual(parse_qs(urlsplit(get.call_args.args[0]).query)["id_list"], ["2610.12345v1"])

    def test_conflicting_strong_versions_are_ambiguous_before_querying(self):
        document = self.document(identifiers=("2610.12345v1", "2610.12345v2"))
        document["metadata"]["identifier_sources"] = {"arxiv": {
            "2610.12345v1": "pdf_info", "2610.12345v2": "xmp"}}
        with patch("paperassist.arxiv_metadata.retrieval._get") as get:
            self.assertIsNone(lookup(document, Settings(), threading.Event()))
        get.assert_not_called()

    def test_weak_identifier_rejects_author_conflict_in_header_and_surname_only_in_title(self):
        for header in ("Parallel models for text generation\nGrace Hopper", "Lovelace models for text generation"):
            document = self.document(title=header.splitlines()[0], authors="")
            document["metadata"]["header_text"] = header
            record, _ = self.lookup(document, feed(entry(title=document["title"])))
            self.assertIsNone(record)

    def test_author_formats_with_initials_and_surname_search_use_the_surname(self):
        for authors, name in (("Lovelace A., Hopper G.", "Ada Lovelace"), ("A. Lovelace et al.", "Ada Lovelace"),
                              ("van der Voort L. R.", "Luc Rouppe van der Voort"), ("Jerčić V.", "Vedad Jerčić")):
            with self.subTest(authors=authors):
                record, _ = self.lookup(self.document(authors=authors), feed(entry(author=name)))
                self.assertIsNotNone(record)
        record, _ = self.lookup(self.document(authors="Hopper G., Lovelace A."), feed(entry()))
        self.assertIsNone(record)
        record, get = self.lookup(self.document(identifiers=(), authors="Lovelace A."), feed(entry()))
        self.assertIn('au:"Lovelace"', parse_qs(urlsplit(get.call_args.args[0]).query)["search_query"][0])

    def test_header_author_survives_title_candidates_that_include_the_author_line(self):
        author_line = "Ada Lovelace1, Grace Hopper1 and Alan Turing2"
        document = self.document(authors="")
        document["metadata"].update(header_text=f"Parallel models for text generation\n{author_line}",
                                    candidates={"arxiv": ["2610.12345v1"], "title": [author_line]})
        self.assertIsNotNone(self.lookup(document, feed(entry()))[0])
        self.assertIsNone(self.lookup(document, feed(entry(author="Edsger Dijkstra")))[0])

    def test_short_titles_are_not_matched_by_squashed_header_text(self):
        document = self.document(title="download", authors="")
        document["metadata"].update(sources={"title": "filename"}, header_text="Parallel models\nAda Lovelace")
        self.assertIsNone(self.lookup(document, feed(entry(title="Parallel models")))[0])

    def test_fallback_skips_one_missing_page_and_never_requests_more_than_two(self):
        document = self.document(identifiers=("2610.12344", "2610.12345"))
        html = b'''<meta name="citation_title" content="Parallel models for text generation">
            <meta name="citation_author" content="Ada Lovelace"><meta name="citation_date" content="2026/10/01">
            <meta name="citation_arxiv_id" content="2610.12345">'''
        missing = HTTPError("https://arxiv.org/abs/2610.12344", 404, "Missing", {}, None)
        with patch("paperassist.arxiv_metadata.retrieval._get", side_effect=[OSError("Offline"), missing, html]) as get:
            record = lookup(document, Settings(), threading.Event())
        self.assertIsNotNone(record)
        self.assertEqual(get.call_count, 3)
        with patch("paperassist.arxiv_metadata.retrieval._get", side_effect=OSError("Offline")) as get:
            with self.assertRaises(OSError):
                lookup(self.document(identifiers=("2610.12344", "2610.12345", "2610.12346")), Settings(), threading.Event())
        get.assert_called_once()
