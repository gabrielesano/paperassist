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
from paperassist.workspace import Workspace


class MetadataRefreshTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workspace = Workspace(Path(folder.name) / "library")
        self.addCleanup(self.workspace.close)
        self.library = self.workspace.library
        arxiv = patch("paperassist.metadata_refresh.arxiv_metadata.lookup", return_value=None)
        arxiv.start()
        self.addCleanup(arxiv.stop)
        network = patch("paperassist.retrieval._get", return_value=b'{"message":{"items":[]}}')
        network.start()
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
            done = self.finish(self.workspace.refresh_metadata())
        lookup.assert_not_called()
        again = self.library.get_document(doc["id"])
        for key in ("title", "authors", "year", "doi", "metadata_status", "work_id"):
            self.assertEqual(again[key], corrected[key], key)
        self.assertEqual(again["revision"], 1)
        self.assertEqual(self.library.get_path(doc["id"], annotated=False).read_bytes(), original)
        self.assertEqual(self.library.get_notes(doc["id"]), [{"text": "Keep this note"}])
        self.assertEqual(self.library.get_view(doc["id"]), {"page": 1, "scale": "page-width"})
        self.assertEqual(done["result"]["preserved"], 1)

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
