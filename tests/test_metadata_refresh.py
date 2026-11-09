"""Bulk metadata refresh preserves PDF state and accepts only corroborated identities."""
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from paperassist import metadata_refresh
from paperassist.workspace import Workspace
from test_library import confirm
from test_metadata import body_lines, line, page_report
from test_ocr import needs_ocr, scan_pdf


class MetadataRefreshTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workspace = Workspace(Path(folder.name) / "library")
        self.addCleanup(self.workspace.close)
        self.library = self.workspace.library
        arxiv = patch("paperassist.metadata_refresh.arxiv_metadata.lookup", return_value=None)
        self.arxiv = arxiv.start()
        self.addCleanup(arxiv.stop)
        network = patch("paperassist.retrieval._get", return_value=b'{"message":{"items":[]}}')
        self.network = network.start()
        self.addCleanup(network.stop)

    def add(self, title="The cooling of coronal loops", author="Ada Lovelace"):
        writer = PdfWriter()
        writer.add_blank_page(300, 400)
        writer.add_metadata({"/Title": title, "/Author": author})
        stream = io.BytesIO()
        writer.write(stream)
        return self.library.import_stream(io.BytesIO(stream.getvalue()), "paper.pdf")

    def finish(self, job):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            job = self.workspace.job_status(job["id"])
            if job["status"] in ("completed", "failed", "cancelled"):
                return job
            time.sleep(.002)
        self.fail("Metadata refresh timed out")

    def record(self, **changes):
        return {"provider": "Crossref", "title": "The cooling of coronal loops", "authors": "Ada Lovelace",
                "first_author": "Lovelace", "year": 2021, "doi": "10.1234/loops", **changes}

    def extracted(self, document, **changes):
        return {**document["metadata"], "candidates": {"doi": ["10.1234/loops"]}, **changes}

    def test_local_refresh_preserves_manual_values_original_revisions_notes_and_views(self):
        doc = self.add()
        corrected = self.library.update_metadata(doc["id"], {"title": "My correction", "year": 2019, "doi": "10.1234/manual"})
        original = self.library.get_path(doc["id"], annotated=False).read_bytes()
        self.library.save_pdf(doc["id"], original, 0)
        self.library.save_notes(doc["id"], [{"text": "Keep this note"}])
        self.library.save_view(doc["id"], {"page": 1, "scale": "page-width"})
        with patch("paperassist.retrieval.crossref_metadata") as lookup:
            done = self.finish(self.workspace.refresh_metadata(online=False))
        lookup.assert_not_called()
        again = self.library.get_document(doc["id"])
        for key in ("title", "authors", "year", "doi", "metadata_status", "work_id"):
            self.assertEqual(again[key], corrected[key], key)
        self.assertEqual(again["revision"], 1)
        self.assertEqual(self.library.get_path(doc["id"], annotated=False).read_bytes(), original)
        self.assertEqual(self.library.get_notes(doc["id"]), [{"text": "Keep this note"}])
        self.assertEqual(self.library.get_view(doc["id"]), {"page": 1, "scale": "page-width"})
        self.assertEqual(done["result"]["preserved"], 1)

    def test_a_corrected_paper_that_is_corroborated_becomes_verified_and_keeps_its_corrections(self):
        doc = self.add()
        self.library.update_metadata(doc["id"], {"title": "My title for the loops paper"})
        # The PDF's own title stays a candidate, so the provider's record still agrees with the paper.
        extracted = self.extracted(doc, candidates={"doi": ["10.1234/loops"], "title": ["The cooling of coronal loops"]})
        with patch("paperassist.library.extract_metadata", return_value=extracted):
            with patch("paperassist.retrieval.crossref_metadata", return_value=self.record()) as lookup:
                done = self.finish(self.workspace.refresh_metadata([doc["id"]]))
        lookup.assert_called_once()
        again = self.library.get_document(doc["id"])
        self.assertEqual((again["title"], again["year"], again["doi"], again["metadata_status"]),
                         ("My title for the loops paper", 2021, "10.1234/loops", "verified"))
        self.assertEqual(again["metadata"]["manual"], ["title"])
        self.assertEqual(again["metadata"]["refresh"]["message"], "Verified using Crossref; your corrected title was kept.")
        self.assertEqual((done["result"]["verified"], done["result"]["preserved"]), (1, 1))

    def test_a_corrected_paper_without_a_match_stays_corrected_and_says_what_was_kept(self):
        doc = self.add()
        self.library.update_metadata(doc["id"], {"title": "My title for the loops paper", "year": 2019})
        done = self.finish(self.workspace.refresh_metadata([doc["id"]]))
        again = self.library.get_document(doc["id"])
        self.assertEqual(again["metadata_status"], "corrected")
        self.assertTrue(again["metadata"]["refresh"]["message"].endswith("Your corrected title and year were kept."))
        self.assertEqual(done["result"]["preserved"], 1)

    def test_a_legacy_corrected_paper_with_only_automatic_values_is_verified_like_a_fresh_import(self):
        doc = self.add()
        with self.library._connect() as db:  # corrected by the old rename bug, values untouched
            db.execute("UPDATE documents SET metadata_status = 'corrected' WHERE id = ?", (doc["id"],))
        extracted = self.extracted(doc, authors="Ada Lovelace")
        with patch("paperassist.library.extract_metadata", return_value=extracted):
            with patch("paperassist.retrieval.crossref_metadata", return_value=self.record()):
                self.finish(self.workspace.refresh_metadata([doc["id"]]))
        again = self.library.get_document(doc["id"])
        self.assertEqual((again["metadata_status"], again["year"], again["doi"]), ("verified", 2021, "10.1234/loops"))
        self.assertEqual(again["metadata"]["refresh"]["message"], "Verified using Crossref.")

    def test_selected_ids_are_deduplicated_and_offline_refresh_rereads_candidates(self):
        first, second = self.add(), self.add("Another paper title")
        with patch("paperassist.library.extract_metadata", return_value=self.extracted(first, title="Fresh local title")) as extract:
            with patch("paperassist.retrieval.crossref_metadata") as lookup:
                done = self.finish(self.workspace.refresh_metadata([first["id"], first["id"]], online=False))
        extract.assert_called_once()
        lookup.assert_not_called()
        self.assertEqual(done["progress"], {"completed": 1, "total": 1})
        self.assertEqual(self.library.get_document(first["id"])["title"], "Fresh local title")
        self.assertEqual(self.library.get_document(second["id"])["title"], second["title"])

    def test_candidate_doi_requires_matching_title_and_available_author(self):
        doc = self.add()
        for record, verified in ((self.record(), True), (self.record(title="A completely different work"), False),
                                 (self.record(first_author="Smith"), False)):
            with self.subTest(record=record):
                fresh = self.add(record["title"] + str(verified))
                with patch("paperassist.library.extract_metadata", return_value=self.extracted(doc)):
                    with patch("paperassist.retrieval.crossref_metadata", return_value=record) as lookup:
                        done = self.finish(self.workspace.refresh_metadata([fresh["id"]]))
                self.assertEqual(done["status"], "completed")
                self.assertEqual(done["result"]["errors"], [])
                again = self.library.get_document(fresh["id"])
                self.assertEqual(again["metadata_status"], "verified" if verified else "needs_review")
                self.assertEqual(again["doi"], record["doi"] if verified else None)
                self.assertTrue(lookup.call_args.kwargs["fresh"])

    def test_verified_doi_updates_provider_values_but_disabled_crossref_never_uses_network(self):
        doc = self.add()
        self.library.apply_verified(doc["id"], self.record(year=2018))
        with patch("paperassist.retrieval.crossref_metadata", return_value=self.record(year=2022)):
            self.finish(self.workspace.refresh_metadata([doc["id"]]))
        self.assertEqual(self.library.get_document(doc["id"])["year"], 2022)
        self.workspace.update_settings({"enabled": {"crossref": False}})
        with patch("paperassist.retrieval.crossref_metadata") as lookup:
            self.finish(self.workspace.refresh_metadata())
        lookup.assert_not_called()

    def test_ambiguous_candidates_and_network_errors_preserve_reviewable_extraction(self):
        doc = self.add()
        extracted = self.extracted(doc, candidates={"doi": ["10.1234/one", "10.1234/two"]})
        records = [self.record(doi=doi) for doi in extracted["candidates"]["doi"]]
        with patch("paperassist.library.extract_metadata", return_value=extracted):
            with patch("paperassist.retrieval.crossref_metadata", side_effect=records):
                done = self.finish(self.workspace.refresh_metadata([doc["id"]]))
        self.assertEqual(done["result"]["errors"], [])
        self.assertEqual(self.library.get_document(doc["id"])["metadata_status"], "needs_review")
        with patch("paperassist.library.extract_metadata", return_value=extracted):
            with patch("paperassist.retrieval.crossref_metadata", side_effect=OSError("Offline")):
                done = self.finish(self.workspace.refresh_metadata([doc["id"]]))
        self.assertEqual(done["result"]["errors"], [{"document_id": doc["id"], "error": "Crossref: Offline"}])
        self.assertEqual(done["result"]["documents"][0]["metadata"]["candidates"], extracted["candidates"])
        self.assertIsNone(self.library.get_document(doc["id"])["doi"])

    def test_failures_are_per_paper_and_cancel_keeps_completed_updates(self):
        first, second = self.add(), self.add("Second coronal loops study")
        original = self.library.refresh_metadata
        entered, release = threading.Event(), threading.Event()
        def slow(document_id):
            entered.set()
            release.wait(2)
            return original(document_id)
        with patch.object(self.library, "refresh_metadata", side_effect=slow):
            job = self.workspace.refresh_metadata([first["id"], second["id"]], online=False)
            self.assertTrue(entered.wait(2))
            self.workspace.cancel_job(job["id"])
            release.set()
            done = self.finish(job)
        self.assertEqual(done["status"], "cancelled")
        self.assertEqual([doc["id"] for doc in done["result"]["documents"]], [first["id"]])
        self.library.get_path(first["id"], annotated=False).unlink()
        done = self.finish(self.workspace.refresh_metadata(online=False))
        self.assertEqual([doc["id"] for doc in done["result"]["documents"]], [second["id"]])
        self.assertEqual(done["result"]["errors"][0]["document_id"], first["id"])

    def test_invalid_selections_are_rejected(self):
        for ids in ([], [True], ["1"], [0], [-1], "all"):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.workspace.refresh_metadata(ids)

    def test_offline_recovery_repairs_legacy_download_title_authors_and_explicit_preprint_year(self):
        writer = PdfWriter()
        page = writer.add_blank_page(600, 800)
        font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        lines = [(18, 720, "A deterministic model of"), (18, 698, "coronal loop heating"),
                 (11, 670, "Ada Lovelace1 and Grace Hopper2"), (9, 650, "1Example University, 2Example Institute"),
                 (11, 620, "Abstract"), (10, 600, "We study thermal processes in coronal loops."),
                 (10, 585, "The observations constrain the heating mechanism."),
                 (10, 570, "Several models reproduce the measured intensities."),
                 (9, 30, "arXiv:2401.01234v1 [astro-ph.SR] 9 Jan 2024")]
        stream = DecodedStreamObject()
        stream.set_data("\n".join(f"BT /F1 {size} Tf 40 {y} Td ({text}) Tj ET" for size, y, text in lines).encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.add_metadata({"/Title": "download"})
        payload = io.BytesIO()
        writer.write(payload)
        doc = self.library.import_stream(io.BytesIO(payload.getvalue()), "download.pdf")
        self.assertEqual(doc["title"], "A deterministic model of coronal loop heating")
        self.assertIn("Ada Lovelace", doc["authors"])
        self.assertIn("Grace Hopper", doc["authors"])
        self.assertEqual(doc["year"], 2024)
        self.assertEqual(doc["metadata_status"], "needs_review")
        with self.library._connect() as db:
            db.execute("UPDATE documents SET title = 'download', authors = '', year = NULL, extracted = ? WHERE id = ?",
                       (json.dumps({"parser_version": 3, "sources": {"title": "filename"}}), doc["id"]))
        with patch("paperassist.metadata_refresh.arxiv_metadata.lookup") as arxiv:
            with patch("paperassist.metadata_refresh.crossref_metadata.lookup") as crossref:
                done = self.finish(self.workspace.refresh_metadata(online=False))
        arxiv.assert_not_called()
        crossref.assert_not_called()
        recovered = self.library.get_document(doc["id"])
        self.assertEqual((recovered["title"], recovered["authors"], recovered["year"]),
                         (doc["title"], doc["authors"], doc["year"]))
        self.assertEqual(recovered["metadata"]["sources"]["year"], "arxiv_submission_date")
        self.assertIn("Online lookup is off", recovered["metadata"]["refresh"]["message"])
        self.assertEqual((done["result"]["local"], done["result"]["verified"]), (1, 0))

    def test_arxiv_identifier_without_doi_is_enriched_and_retains_identity_evidence(self):
        doc = self.add()
        extracted = self.extracted(doc, candidates={"arxiv": ["2401.01234v1"], "doi": []})
        record = self.record(provider="arxiv", doi=None, arxiv_id="2401.01234v1", evidence={"method": "atom"})
        with patch("paperassist.library.extract_metadata", return_value=extracted):
            with patch("paperassist.metadata_refresh.arxiv_metadata.lookup", return_value=record) as arxiv:
                with patch("paperassist.metadata_refresh.crossref_metadata.lookup") as crossref:
                    done = self.finish(self.workspace.refresh_metadata([doc["id"]]))
        arxiv.assert_called_once()
        crossref.assert_not_called()
        recovered = self.library.get_document(doc["id"])
        self.assertEqual(recovered["metadata_status"], "verified")
        self.assertEqual(recovered["metadata"]["verified"]["arxiv_id"], record["arxiv_id"])
        self.assertEqual(recovered["metadata"]["verified"]["evidence"], record["evidence"])
        self.assertEqual(done["result"]["verified"], 1)

    def add_scan(self, name="scan.pdf", width=300):
        """A page without text or metadata: native extraction knows nothing about it."""
        writer = PdfWriter()
        writer.add_blank_page(width, 400)
        stream = io.BytesIO()
        writer.write(stream)
        return self.library.import_stream(io.BytesIO(stream.getvalue()), name)

    @staticmethod
    def report(*lines, status="completed"):
        pages = page_report([*lines, *body_lines()])["pages"] if lines else []
        return {"status": status, "message": "Offline OCR finished.", "pages": pages, "cache_hit": False}

    def recognised(self):
        return self.report(line("Heating of Coronal Loops", 60, 20), line("Ada Lovelace", 100, 11))

    def refresh(self, ids=None, **options):
        return self.finish(self.workspace.refresh_metadata(ids, **options))

    def test_ocr_mode_must_be_known(self):
        with self.assertRaises(ValueError):
            self.workspace.refresh_metadata(ocr="always")

    def test_off_never_calls_ocr_and_auto_skips_papers_native_extraction_identified(self):
        scan, native = self.add_scan(), self.add()
        with patch("paperassist.metadata_refresh.ocr.recover") as recover:
            self.refresh([scan["id"]], online=False, ocr="off")
            self.refresh([native["id"]], online=False)
        recover.assert_not_called()

    def test_auto_skips_insufficient_metadata_when_the_native_text_is_ample(self):
        doc = self.add_scan()
        text = "Heating of coronal loops by waves in a long and carefully typeset title page " * 3
        with patch("paperassist.metadata_refresh.ocr.recover") as recover:
            with patch("paperassist.library.extract_metadata", return_value=self.extracted(
                    doc, first_page_text_status="extracted", header_text=text)):
                self.refresh([doc["id"]], online=False)
        recover.assert_not_called()

    def test_auto_recovers_a_scan_and_keeps_it_provisional_without_any_http_request(self):
        doc = self.add_scan()
        seen = []

        def recover(path, cache_dir, sha256, cancel, **options):
            options["progress"]({"stage": "recognizing", "page": 1, "attempt": 2})
            seen.append((Path(path).name, Path(cache_dir), sha256, options["language"],
                         self.workspace.job_status(next(iter(self.workspace.jobs)))["progress"]))
            return self.recognised()
        before = self.library.get_path(doc["id"], annotated=False).read_bytes()
        with patch("paperassist.metadata_refresh.ocr.recover", side_effect=recover):
            done = self.refresh(online=False)
        self.network.assert_not_called()
        self.arxiv.assert_not_called()
        self.assertEqual(seen[0][1:4], (self.library.data_dir / "cache" / "ocr", doc["sha256"], "eng"))
        self.assertEqual(seen[0][4], {"completed": 0, "total": 1, "stage": "ocr", "page": 1, "attempt": 2})
        recovered = self.library.get_document(doc["id"])
        self.assertEqual((recovered["title"], recovered["authors"]), ("Heating of Coronal Loops", "Ada Lovelace"))
        self.assertEqual(recovered["metadata_status"], "needs_review")
        self.assertIn("Recovered title and authors with offline OCR; review them.", recovered["metadata"]["refresh"]["message"])
        self.assertEqual((done["result"]["ocr"], done["result"]["errors"]), (1, []))
        self.assertEqual(self.library.get_path(doc["id"], annotated=False).read_bytes(), before)
        self.assertEqual(recovered["revision"], 0)

    def test_force_runs_ocr_but_native_corrected_and_verified_values_win(self):
        native, corrected, verified = self.add(), self.add_scan("a.pdf", 301), self.add_scan("b.pdf", 302)
        self.library.update_metadata(corrected["id"], {"title": "My correction", "authors": "My Author"})
        confirm(self.library, verified["id"], "verified", title="Verified title", authors="Verified Author")
        with patch("paperassist.metadata_refresh.ocr.recover", return_value=self.recognised()) as recover:
            done = self.refresh(online=False, ocr="force")
        self.assertEqual(recover.call_count, 2)  # OCR has nothing to fill once title and authors are the user's
        self.assertEqual(self.library.get_document(native["id"])["title"], "The cooling of coronal loops")
        self.assertEqual(self.library.get_document(corrected["id"])["title"], "My correction")
        confirmed = self.library.get_document(verified["id"])
        self.assertEqual((confirmed["title"], confirmed["authors"], confirmed["metadata_status"]),
                         ("Verified title", "Verified Author", "verified"))
        self.assertEqual(done["result"]["ocr"], 0)

    def test_ocr_still_fills_what_the_user_left_alone(self):
        scan = self.add_scan()
        self.library.update_metadata(scan["id"], {"title": "My title for the scan"})
        with patch("paperassist.metadata_refresh.ocr.recover", return_value=self.recognised()) as recover:
            self.refresh(online=False)
        recover.assert_called_once()
        done = self.library.get_document(scan["id"])
        self.assertEqual((done["title"], done["authors"], done["metadata_status"]),
                         ("My title for the scan", "Ada Lovelace", "corrected"))

    def test_ocr_comes_before_the_online_lookup_so_its_candidates_can_be_corroborated(self):
        doc = self.add_scan()
        report = self.report(line("Heating of Coronal Loops", 60, 20), line("Ada Lovelace", 100, 11),
                             line("doi:10.1234/loops.5", 130, 8))
        seen = []

        def crossref(document, *args, **options):
            seen.append((document["title"], document["metadata"]["candidates"]["doi"]))
            return None
        with patch("paperassist.metadata_refresh.ocr.recover", return_value=report):
            with patch("paperassist.metadata_refresh.crossref_metadata.lookup", side_effect=crossref):
                self.refresh([doc["id"]])
        self.assertEqual(seen, [("Heating of Coronal Loops", ["10.1234/loops.5"])])
        self.assertIsNone(self.library.get_document(doc["id"])["doi"])

    def test_unavailable_failed_and_empty_ocr_keep_existing_values_without_counting_errors(self):
        doc = self.add_scan()
        for status, message, expected in [
                ("unavailable", "Offline OCR is unavailable: Tesseract 5 is not installed.", "Tesseract 5 is not installed."),
                ("timeout", "Offline OCR reached its time limit.", "time limit"),
                ("failed", "Offline OCR failed: boom", "boom"),
                ("empty", "No readable text was found by offline OCR.", "Offline OCR found no readable text.")]:
            with self.subTest(status=status):
                report = {"status": status, "message": message, "pages": [], "cache_hit": False}
                with patch("paperassist.metadata_refresh.ocr.recover", return_value=report):
                    done = self.refresh([doc["id"]], online=False)
                kept = self.library.get_document(doc["id"])
                self.assertEqual((kept["title"], done["result"]["errors"], done["result"]["ocr"]), ("scan", [], 0))
                self.assertIn(expected, kept["metadata"]["refresh"]["message"])
                self.assertEqual(kept["metadata"]["ocr"]["status"], status)

    def test_cancelling_during_ocr_stops_the_job_and_leaves_the_paper_unchanged(self):
        first, second = self.add_scan(), self.add_scan("two.pdf", 301)

        def recover(path, cache_dir, sha256, cancel, **options):
            cancel.set()
            return {"status": "cancelled", "message": "Offline OCR cancelled.", "pages": [], "cache_hit": False}
        with patch("paperassist.metadata_refresh.ocr.recover", side_effect=recover) as call:
            job = self.refresh(online=False)
        self.assertEqual((job["status"], call.call_count), ("cancelled", 1))
        self.assertEqual(self.library.get_document(first["id"])["title"], "scan")
        self.assertNotIn("ocr", self.library.get_document(first["id"])["metadata"])
        self.assertEqual(self.library.get_document(second["id"])["metadata"].get("refresh"), None)

    @needs_ocr
    def test_real_engine_recovers_a_generated_scan_from_a_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            path = scan_pdf(Path(folder) / "Scanned paper è.pdf")
            with path.open("rb") as stream:
                doc = self.library.import_stream(stream, path.name)
            original = self.library.get_path(doc["id"], annotated=False).read_bytes()
            done = self.refresh(online=False)
            again = self.refresh(online=False, ocr="force")
        recovered = self.library.get_document(doc["id"])
        self.assertEqual((recovered["title"], recovered["authors"]), ("Adiabatic Heating of Coronal Loops", "Ada Lovelace; Grace Hopper"))
        self.assertEqual((recovered["metadata"]["sources"]["title"], recovered["year"]), ("ocr_title", None))
        self.assertEqual(done["result"]["ocr"], 1)
        self.assertTrue(recovered["metadata"]["ocr"]["cache_hit"])
        self.assertEqual(again["result"]["ocr"], 0)
        self.assertEqual(self.library.get_path(doc["id"], annotated=False).read_bytes(), original)
        self.assertEqual(recovered["revision"], 0)

    def test_one_provider_failure_does_not_hide_success_from_another(self):
        doc = self.add()
        with patch("paperassist.metadata_refresh.crossref_metadata.lookup", side_effect=OSError("Offline")):
            with patch("paperassist.metadata_refresh.arxiv_metadata.lookup", return_value=self.record(provider="arxiv")):
                done = self.finish(self.workspace.refresh_metadata([doc["id"]]))
        self.assertEqual(done["result"]["errors"], [])
        self.assertEqual(done["result"]["verified"], 1)
        self.assertEqual(self.library.get_document(doc["id"])["metadata"]["refresh"]["message"], "Verified using arXiv.")


if __name__ == "__main__":
    unittest.main()
