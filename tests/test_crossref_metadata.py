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
        for source in ("arxiv_submission_date", "ocr_arxiv_submission_date"):
            with self.subTest(source=source):
                self.doc["metadata"]["sources"] = {"year": source}
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

    def test_author_formats_with_initials_match_the_first_author_surname(self):
        for authors, family in (("Kohutova P., Verwichte E.", "Kohutova"), ("S.P. Moschou et al.", "Moschou"),
                                ("Jerčić V., Keppens R.", "Jerčić"), ("van der Voort L. R.", "van der Voort"),
                                ("van der Voort L. R.", "Voort"), ("P. Antolin, K. Shibata", "Antolin")):
            with self.subTest(authors=authors):
                self.doc["authors"] = authors
                self.assertIsNotNone(self.search(self.item(author=[{"given": "X", "family": family}]))[0])
        self.doc["authors"] = "Kohutova P., Verwichte E."
        for family in ("Verwichte", "Smith", "Kohut"):
            with self.subTest(family=family):
                self.assertIsNone(self.search(self.item(author=[{"given": "X", "family": family}]))[0])

    def test_header_author_survives_title_candidates_that_include_the_author_line(self):
        author_line = "Patrick Antolin,1 Paolo Pagano,1 Ineke De Moortel,1 and Valery Nakariakov2"
        self.doc["authors"] = ""
        self.doc["metadata"].update(header_text=f"{self.doc['title']}\n{author_line}\nSchool of Mathematics")
        self.doc["metadata"]["candidates"]["title"] = [author_line]
        antolin = {"author": [{"given": "Patrick", "family": "Antolin"}]}
        self.assertIsNotNone(self.search(self.item(**antolin))[0])
        self.assertIsNone(self.search(self.item(author=[{"given": "Grace", "family": "Hopper"}]))[0])

    def test_title_split_across_lines_or_glued_matches_only_with_the_author_and_a_long_enough_title(self):
        record = "The evaporation of spherical clouds in a hot gas. I - Classical and saturated mass loss rates"
        self.doc.update(title="THE EVAPORATION OF SPHERICAL CLOUDS IN A HOT GAS. I.", authors="Lennox L. Cowie; Christopher F. McKee")
        self.doc["metadata"].update(header_text="THE EVAPORATION OF SPHERICAL CLOUDS IN A HOT GAS. I.\nCLASSICAL AND SATURATED MASS LOSS RATES\n"
                                                "Lennox L. Cowie")
        cowie = self.item(DOI="10.1086/154911", title=[record], author=[{"given": "Lennox", "family": "Cowie"}])
        part_two = self.item(DOI="10.1086/155350", title=["The evaporation of spherical clouds in a hot gas. II. Effects of radiation"],
                             author=[{"given": "Christopher", "family": "McKee"}])
        self.assertEqual(self.search(part_two, cowie)[0]["doi"], "10.1086/154911")
        self.assertIsNone(self.search(part_two)[0])
        self.assertIsNone(self.search(self.item(**{**cowie, "author": [{"given": "Bob", "family": "Smith"}]}))[0])
        glued = dict(authors="", title="aspectsofcoronalheating. P h i l .T r a n s")
        self.doc.update(glued)
        self.doc["metadata"]["header_text"] = "Citethisarticle: KlimchukJA.2015Key\naspectsofcoronalheating.\nJamesA.Klimchuk"
        self.doc["metadata"]["candidates"]["title"] = ["We highlight 10 key aspects of coronal heating that"]
        klimchuk = self.item(title=["Key aspects of coronal heating"], author=[{"given": "James", "family": "Klimchuk"}])
        self.assertIsNotNone(self.search(klimchuk)[0])
        self.assertIsNone(self.search(self.item(title=["Key aspects of coronal heating"], author=[{"given": "A", "family": "Other"}]))[0])
        self.doc["metadata"]["header_text"] = "Thermal instability\nGeorge B. Field"  # 18 squashed characters
        short = self.item(title=["Thermal instability"], author=[{"given": "George", "family": "Field"}])
        self.doc.update(title="A long unrelated working title here", authors="")
        self.assertIsNone(self.search(short)[0])

    def test_a_title_ending_in_a_numeral_does_not_match_inside_the_next_part_of_a_series(self):
        self.doc.update(title="CORONAL CLOSED STRUCTURES. IV. HYDRODYNAMICAL STABILITY", authors="")
        self.doc["metadata"]["header_text"] = "CORONAL CLOSED STRUCTURES. IV. HYDRODYNAMICAL STABILITY\nG. Peres"
        peres = {"author": [{"given": "G.", "family": "Peres"}]}
        self.assertIsNone(self.search(self.item(title=["Coronal closed structures. I"], **peres))[0])
        self.assertIsNotNone(self.search(self.item(title=["Coronal closed structures. IV. Hydrodynamical stability"], **peres))[0])

    def test_surname_that_is_part_of_an_unlocatable_title_is_not_author_evidence(self):
        self.doc.update(title="Lovelace engines and the cooling of loops", authors="")
        self.doc["metadata"]["header_text"] = "Lovelace-engines&the cooling of loops: a review\nAnonymous"
        self.assertIsNone(self.search(self.item(title=[self.doc["title"]]))[0])

    def reference(self, **changes):
        return self.item(**{"DOI": "10.1086/161956", "title": ["On coronal oscillations"], "author": [{"given": "B.", "family": "Roberts"}],
                            "container-title": ["The Astrophysical Journal"], "volume": "279", "page": "857",
                            "issued": {"date-parts": [[1984]]}, **changes})

    def printed_document(self, header="The Astrophysical Journal, 279:857-865, 1984 April 15\nB. Roberts and P. M. Edwin"):
        self.doc.update(title="Roberts1984", authors="")
        self.doc["metadata"].update(header_text=header, candidates={"title": [], "doi": [], "publication": [["APJ", "279", "857", "1984"]]})

    def test_printed_journal_reference_with_the_first_author_identifies_a_paper_without_a_title(self):
        self.printed_document()
        result, get = self.search(self.reference())
        self.assertEqual(result["doi"], "10.1086/161956")
        query = parse_qs(urlsplit(get.call_args.args[0]).query)
        self.assertEqual(query["query.bibliographic"], ["Astrophysical Journal 279 857"])
        self.assertEqual(query["filter"], ["from-pub-date:1983,until-pub-date:1985"])

    def test_printed_journal_reference_never_accepts_a_wrong_page_volume_author_or_journal(self):
        self.printed_document()
        for changes in ({"page": "858"}, {"volume": "280"}, {"container-title": ["Monthly Notices of the Royal Astronomical Society"]}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.search(self.reference(**changes))[0])
        self.assertIsNone(self.search(self.reference(author=[{"given": "A", "family": "Hollweg"}]))[0])
        self.printed_document(header="The Astrophysical Journal, 279:857-865, 1984 April 15\nDepartment of Applied Mathematics")
        self.assertIsNone(self.search(self.reference())[0])
        self.printed_document(header="")
        self.assertIsNone(self.search(self.reference())[0])

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

    def test_a_corrected_paper_is_searched_with_its_current_values(self):
        self.doc.update(metadata_status="corrected", title="The cooling of coronal loops revisited")
        self.doc["metadata"]["manual"] = ["title"]
        self.doc["metadata"]["candidates"]["title"] = ["The cooling of coronal loops"]
        result, get = self.search(self.item())
        self.assertEqual(result["doi"], "10.1234/loops")
        self.assertEqual(parse_qs(urlsplit(get.call_args.args[0]).query)["query.bibliographic"], [self.doc["title"]])

    def test_source_controls_and_cancellation_never_query(self):
        for status, enabled, cancelled in (("needs_review", False, False), ("needs_review", True, True)):
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
