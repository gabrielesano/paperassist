"""Imported PDFs need title and independent author evidence for Crossref searches."""

import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from paperassist import crossref_metadata
from paperassist.settings import Settings


class CrossrefMetadataTests(unittest.TestCase):
    def setUp(self):
        self.cancel = threading.Event()
        self.cfg = Settings()
        self.doc = {"title": "The cooling of coronal loops", "authors": "Ada Lovelace; Grace Hopper",
                    "doi": None, "year": None, "metadata_status": "needs_review",
                    "metadata": {"candidates": {"title": [], "doi": []}, "header_text": ""}}

    def item(self, **changes):
        return {"DOI": "10.1234/loops", "title": [self.doc["title"]],
                "author": [{"given": "Ada", "family": "Lovelace"}], "issued": {"date-parts": [[2021]]}, **changes}

    def search(self, *items, document=None):
        data = json.dumps({"message": {"items": items}}).encode()
        with patch("paperassist.retrieval._get", return_value=data) as get:
            result = crossref_metadata.lookup(document or self.doc, self.cfg, self.cancel)
        return result, get

    def test_title_search_accepts_one_matching_author_and_preserves_fresh_flag(self):
        result, get = self.search(self.item())
        self.assertEqual(result["doi"], "10.1234/loops")
        get.assert_called_once()
        query = parse_qs(urlsplit(get.call_args.args[0]).query)
        self.assertEqual(query["query.bibliographic"], [self.doc["title"]])
        self.assertEqual(query["query.author"], ["Ada Lovelace"])
        self.assertEqual(query["rows"], ["5"])
        self.assertTrue(get.call_args.kwargs["fresh"])

    def test_title_only_wrong_author_and_conflicting_year_never_match(self):
        cases = [self.item(title=["The heating of coronal loops"]),
                 self.item(author=[{"given": "Bob", "family": "Smith"}])]
        for item in cases:
            with self.subTest(item=item):
                self.assertIsNone(self.search(item)[0])
        self.doc["year"] = 2022
        self.assertIsNone(self.search(self.item())[0])

    def test_arxiv_submission_year_does_not_constrain_a_later_journal_publication(self):
        self.doc["year"] = 2019
        self.doc["metadata"]["sources"] = {"year": "arxiv_submission_date"}
        self.assertEqual(self.search(self.item())[0]["year"], 2021)

    def test_cancellation_during_a_doi_request_does_not_return_a_record(self):
        self.doc["doi"] = "10.1234/loops"
        def cancelled(*args, **kwargs):
            self.cancel.set()
            return {"doi": "10.1234/loops"}
        with patch("paperassist.retrieval.crossref_metadata", side_effect=cancelled):
            self.assertIsNone(crossref_metadata.lookup(self.doc, self.cfg, self.cancel))

    def test_missing_author_uses_bounded_header_but_not_abstract_or_title_names(self):
        self.doc["authors"] = ""
        self.doc["metadata"]["header_text"] = self.doc["title"] + "\nAda Lovelace1, Grace Hopper2"
        self.assertIsNotNone(self.search(self.item())[0])
        self.doc["metadata"]["header_text"] = self.doc["title"] + "\nUnrelated authors"
        self.assertIsNone(self.search(self.item())[0])
        self.doc["metadata"]["header_text"] = ""
        result, get = self.search(self.item())
        self.assertIsNone(result)
        get.assert_not_called()
        self.doc["title"] = "Lovelace and the cooling of coronal loops"
        self.doc["metadata"]["header_text"] = self.doc["title"]
        self.assertIsNone(self.search(self.item())[0])

    def test_series_numbers_and_ambiguous_identifiers_are_rejected(self):
        self.doc["title"] += " II"
        self.assertIsNone(self.search(self.item(title=["The cooling of coronal loops I"]))[0])
        self.assertIsNone(self.search(self.item(), self.item(DOI="10.1234/other"))[0])
        self.assertIsNotNone(self.search(self.item(), self.item())[0])

    def test_generic_titles_and_no_independent_author_evidence_do_not_query(self):
        for title in ("download", "Untitled", "Paper 1", "A short title"):
            self.doc["title"] = title
            result, get = self.search(self.item())
            self.assertIsNone(result)
            get.assert_not_called()
        self.doc["title"] = "arXiv:cond-mat/9901234"
        self.doc["metadata"]["sources"] = {"title": "arxiv_id"}
        result, get = self.search(self.item())
        self.assertIsNone(result)
        get.assert_not_called()

    def test_pdf_title_candidates_support_a_single_safe_search(self):
        self.doc["title"] = "download"
        self.doc["metadata"]["candidates"]["title"] = ["A study of particle acceleration in coronal loops"]
        result, get = self.search(self.item(title=self.doc["metadata"]["candidates"]["title"]))
        self.assertIsNotNone(result)
        get.assert_called_once()

    def test_confirmed_doi_is_exact_and_does_not_need_a_title_query(self):
        self.doc["doi"] = "10.1234/loops"
        record = {"doi": "10.1234/loops", "title": "Provider correction", "year": 2022}
        with patch("paperassist.retrieval.crossref_metadata", return_value=record) as doi:
            with patch("paperassist.retrieval._get") as get:
                self.assertEqual(crossref_metadata.lookup(self.doc, self.cfg, self.cancel), record)
        doi.assert_called_once_with("10.1234/loops", self.cfg, fresh=True)
        get.assert_not_called()
        with patch("paperassist.retrieval.crossref_metadata", return_value={"doi": "10.1234/different"}):
            with self.assertRaisesRegex(ValueError, "different DOI"):
                crossref_metadata.lookup(self.doc, self.cfg, self.cancel)

    def test_candidate_404_does_not_discard_another_corroborated_doi(self):
        self.doc["metadata"]["candidates"]["doi"] = ["10.1234/absent", "10.1234/loops"]
        record = {"doi": "10.1234/loops", "title": self.doc["title"], "first_author": "Lovelace", "year": 2021}
        with patch("paperassist.retrieval.crossref_metadata", side_effect=[HTTPError("", 404, "Missing", {}, None), record]) as doi:
            with patch("paperassist.retrieval._get") as get:
                self.assertEqual(crossref_metadata.lookup(self.doc, self.cfg, self.cancel), record)
        self.assertEqual(doi.call_count, 2)
        get.assert_not_called()

    def test_source_controls_manual_corrections_and_cancellation_never_query(self):
        for status, enabled, cancelled in (("corrected", True, False), ("needs_review", False, False),
                                           ("needs_review", True, True)):
            self.doc["metadata_status"] = status
            cfg = Settings({"enabled": {"crossref": enabled}})
            if cancelled:
                self.cancel.set()
            with patch("paperassist.retrieval._get") as get, patch("paperassist.retrieval.crossref_metadata") as doi:
                self.assertIsNone(crossref_metadata.lookup(self.doc, cfg, self.cancel))
            get.assert_not_called()
            doi.assert_not_called()

    def test_more_than_five_doi_candidates_only_allow_one_title_search(self):
        self.doc["metadata"]["candidates"]["doi"] = [f"10.1234/{n}" for n in range(6)]
        with patch("paperassist.retrieval.crossref_metadata") as doi:
            result, get = self.search(self.item())
        self.assertIsNotNone(result)
        get.assert_called_once()
        doi.assert_not_called()


if __name__ == "__main__":
    unittest.main()
