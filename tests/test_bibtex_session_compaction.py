"""Tests for BibTeX export, working/ snapshot compaction, and opt-in session restoration."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from support import QtTestCase, make_pdf, needs_qt, pdf_available

from paperassist import references, settings
from paperassist.bibtex import export_documents_bibtex, format_document_bibtex, format_reference_bibtex
from paperassist.library import Library
from paperassist.workspace import Workspace

if pdf_available:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QPushButton

    from paperassist.native import app as native_app
    from paperassist.native.library_dialog import LibraryDialog
    from paperassist.native.window import MainWindow


def _pdf_bytes(width=72):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


class BibtexUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = Library(Path(self.tmp.name) / "library")
        self.addCleanup(self.library.close)

    def test_unverified_and_manual_only_documents_return_none(self):
        doc = self.library.import_stream(io.BytesIO(_pdf_bytes(72)), "unverified.pdf")
        self.assertIsNone(format_document_bibtex(doc))
        corrected = self.library.update_metadata(doc["id"], {"title": "Manual Title", "authors": "Ada Lovelace", "year": 2020})
        self.assertIsNone(format_document_bibtex(corrected))

    def test_verified_article_preprint_escaping_corrections_and_key_disambiguation(self):
        d1 = self.library.import_stream(io.BytesIO(_pdf_bytes(80)), "a.pdf")
        d2 = self.library.import_stream(io.BytesIO(_pdf_bytes(90)), "b.pdf")
        d3 = self.library.import_stream(io.BytesIO(_pdf_bytes(100)), "c.pdf")
        d4 = self.library.import_stream(io.BytesIO(_pdf_bytes(110)), "d.pdf")

        # Verified article with special LaTeX chars and user correction on title.
        self.library.apply_verified(d1["id"], {
            "provider": "crossref",
            "title": "100% Energy & Flux_1 in {Loops} ~ #1 ^ $2 \\test",
            "authors": "A. F. Rappazzo, M. Velli",
            "year": 2013,
            "doi": "10.1088/0004-637x/771/2/76",
            "journal": "A&A",
            "publication": ["A&A", "771", "76", "2013"],
            "first_author": "Rappazzo",
        })
        v1 = self.library.update_metadata(d1["id"], {"title": "Corrected & 50% _Loop_"})
        bib1 = format_document_bibtex(v1)
        self.assertIsNotNone(bib1)
        self.assertTrue(bib1.startswith("@article{Rappazzo2013,"))
        self.assertIn("author = {A. F. Rappazzo and M. Velli}", bib1)
        self.assertIn(r"title = {Corrected \& 50\% \_Loop\_}", bib1)
        self.assertIn(r"journal = {A\&A}", bib1)
        self.assertIn("volume = {771}", bib1)
        self.assertIn("pages = {76}", bib1)
        self.assertIn("doi = {10.1088/0004-637x/771/2/76}", bib1)

        # Colliding keys get a, b suffixes; arXiv-only preprint uses @misc.
        v2 = self.library.apply_verified(d2["id"], {
            "provider": "arxiv",
            "title": "Second Paper & More",
            "authors": "Rappazzo, A. F.",
            "year": 2013,
            "arxiv_id": "1305.1234v1",
            "first_author": "Rappazzo",
        })
        v3 = self.library.apply_verified(d3["id"], {
            "provider": "ads",
            "title": "Third Paper",
            "authors": "Ada Lovelace; Grace Hopper",
            "year": 2013,
            "bibcode": "2013ApJ...771...99R",
            "publication": ["APJ", "771", "99", "2013"],
            "first_author": "Rappazzo",
        })
        self.assertTrue(format_document_bibtex(v2).startswith("@misc{Rappazzo2013,"))
        self.assertIn("eprint = {1305.1234v1}", format_document_bibtex(v2))
        self.assertIn("archivePrefix = {arXiv}", format_document_bibtex(v2))

        text, exported, skipped = self.library.export_bibtex([v1["id"], d4["id"], v2["id"], v3["id"]])
        self.assertEqual((exported, skipped), (3, 1))
        self.assertIn("@article{Rappazzo2013,", text)
        self.assertIn("@misc{Rappazzo2013a,", text)
        self.assertIn("@article{Rappazzo2013b,", text)
        self.assertIn("bibcode = {2013ApJ...771...99R}", text)
        self.assertIn("author = {Ada Lovelace and Grace Hopper}", text)

        # Escape all LaTeX special characters safely without double-escaping.
        escaped = format_document_bibtex({
            "title": r"A \ B {C} & % $ # _ ~ ^",
            "authors": "O'Neill, J.",
            "year": 2022,
            "metadata_status": "verified",
            "metadata": {"verified": {"provider": "crossref", "doi": "10.1/x", "journal": "Sol. Phys."}},
        })
        self.assertIn(r"title = {A \textbackslash{} B \{C\} \& \% \$ \# \_ \textasciitilde{} \textasciicircum{}}", escaped)

    def test_format_reference_bibtex_uses_verified_linked_doc_or_identity(self):
        self.assertIsNone(format_reference_bibtex({"id": "1", "title": "Unverified ref"}))
        identity_ref = {
            "id": "2",
            "identity": {
                "provider": "crossref",
                "title": "Coronal & Loop Waves",
                "authors": "J. Smith, K. Brown",
                "year": 2018,
                "doi": "10.1000/waves",
                "journal": "ApJ",
                "publication": ["APJ", "850", "12", "2018"],
                "first_author": "Smith",
            },
        }
        bib = format_reference_bibtex(identity_ref)
        self.assertIsNotNone(bib)
        self.assertTrue(bib.startswith("@article{Smith2018,"))
        self.assertIn(r"title = {Coronal \& Loop Waves}", bib)

        doc = self.library.import_stream(io.BytesIO(_pdf_bytes(120)), "linked.pdf")
        verified = self.library.apply_verified(doc["id"], {
            "provider": "crossref",
            "title": "Linked Verified Work",
            "authors": "G. Hopper",
            "year": 2020,
            "doi": "10.1000/linked",
            "journal": "Science",
            "first_author": "Hopper",
        })
        self.assertIn("@article{Hopper2020,", format_reference_bibtex({"id": "3"}, linked_document=verified))

        # export_documents_bibtex works on standalone list too.
        text, exported, skipped = export_documents_bibtex([verified, {"id": 99, "metadata_status": "needs_review", "metadata": {}}])
        self.assertEqual((exported, skipped), (1, 1))
        self.assertIn("Linked Verified Work", text)


class SnapshotCompactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = Library(Path(self.tmp.name) / "library")
        self.addCleanup(self.library.close)

    def test_compaction_removes_only_verified_superseded_snapshots_with_safety_guards(self):
        payload = _pdf_bytes(72)
        doc = self.library.import_stream(io.BytesIO(payload), "paper.pdf")
        doc_id = doc["id"]
        self.library.save_pdf(doc_id, payload, 0)
        self.library.save_pdf(doc_id, payload, 1)
        self.library.save_pdf(doc_id, payload, 2)
        rev1 = self.library.working / f"{doc_id}-1.pdf"
        rev2 = self.library.working / f"{doc_id}-2.pdf"
        rev3 = self.library.working / f"{doc_id}-3.pdf"

        # Untracked / future / unknown-id / zero-rev files must never be removed.
        future = self.library.working / f"{doc_id}-4.pdf"
        unknown = self.library.working / "9999-1.pdf"
        zero_rev = self.library.working / f"{doc_id}-0.pdf"
        future.write_bytes(b"future")
        unknown.write_bytes(b"unknown")
        zero_rev.write_bytes(b"zero")

        # If current revision fails integrity verification, compaction refuses to touch old revisions!
        saved_bytes = rev3.read_bytes()
        rev3.write_bytes(b"corrupt")
        self.assertEqual(self.library.superseded_snapshots(), {"count": 0, "bytes": 0})
        self.assertEqual(self.library.compact_snapshots(), {"removed": 0, "bytes_freed": 0})
        self.assertTrue(rev1.is_file() and rev2.is_file())

        # Restore current revision bytes: now rev1 and rev2 are superseded.
        rev3.write_bytes(saved_bytes)
        expected_bytes = rev1.stat().st_size + rev2.stat().st_size
        self.assertEqual(self.library.superseded_snapshots(), {"count": 2, "bytes": expected_bytes})
        result = self.library.compact_snapshots()
        self.assertEqual(result, {"removed": 2, "bytes_freed": expected_bytes})
        self.assertFalse(rev1.exists())
        self.assertFalse(rev2.exists())
        self.assertTrue(rev3.is_file())
        self.assertTrue(future.is_file() and unknown.is_file() and zero_rev.is_file())
        self.assertEqual(self.library.superseded_snapshots(), {"count": 0, "bytes": 0})


@needs_qt
class BibtexCompactionAndSessionUiTests(QtTestCase):
    def setUp(self):
        super().setUp()
        self.workspace = Workspace(self.dir / "library")
        self.addCleanup(self.workspace.close)
        self.p1 = make_pdf(self.dir / "one.pdf", pages=2)
        self.p2 = make_pdf(self.dir / "two.pdf", pages=3)
        self.p3 = make_pdf(self.dir / "three.pdf", pages=4)
        self.docs = [
            self.workspace.library.import_stream(io.BytesIO(path.read_bytes()), path.name)
            for path in (self.p1, self.p2, self.p3)
        ]

    def make_window(self):
        window = MainWindow(self.client, self.workspace)
        window.resize(1280, 820)
        window.show()
        self.addCleanup(self.cleanup_window, window)
        return window

    def test_library_dialog_copy_bibtex_and_compact_revisions(self):
        d1, d2, _ = self.docs
        self.workspace.library.apply_verified(d1["id"], {
            "provider": "crossref",
            "title": "Verified Corona",
            "authors": "Ada Lovelace",
            "year": 2021,
            "doi": "10.1000/corona",
            "journal": "ApJ",
            "publication": ["APJ", "900", "1", "2021"],
            "first_author": "Lovelace",
        })
        payload = self.workspace.library.get_path(d1["id"], annotated=False).read_bytes()
        self.workspace.library.save_pdf(d1["id"], payload, 0)
        self.workspace.library.save_pdf(d1["id"], payload, 1)

        dialog = LibraryDialog(self.workspace)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.typing.stop)
        dialog.show()
        self.pump()

        self.assertTrue(dialog.compact_button.isVisibleTo(dialog))
        self.assertTrue(dialog.compact_button.isEnabled())
        self.assertFalse(dialog.copy_bibtex_button.isEnabled())

        # Select only unverified d2 -> warns cleanly.
        item2 = next(dialog.table.topLevelItem(i) for i in range(dialog.table.topLevelItemCount())
                     if dialog.table.topLevelItem(i).data(0, Qt.UserRole) == d2["id"])
        dialog.table.clearSelection()
        item2.setSelected(True)
        dialog.copy_bibtex_button.click()
        self.assertEqual(dialog.message.text(), "Verify metadata first to copy BibTeX.")

        # Select verified d1 + unverified d2 -> copies 1 entry and reports skipped.
        item1 = next(dialog.table.topLevelItem(i) for i in range(dialog.table.topLevelItemCount())
                     if dialog.table.topLevelItem(i).data(0, Qt.UserRole) == d1["id"])
        item1.setSelected(True)
        dialog.copy_bibtex_button.click()
        self.assertIn("@article{Lovelace2021,", self.app.clipboard().text())
        self.assertEqual(dialog.message.text(), "Copied 1 BibTeX entry (1 unverified paper skipped).")

        # Compact revisions confirms and updates button state.
        with patch.object(dialog, "confirm_compact", return_value=False):
            dialog.compact_button.click()
        self.assertTrue(dialog.compact_button.isVisibleTo(dialog))
        self.assertTrue(dialog.compact_button.isEnabled())
        with patch.object(dialog, "confirm_compact", return_value=True):
            dialog.compact_button.click()
        self.assertFalse(dialog.compact_button.isEnabled())
        self.assertFalse(dialog.compact_button.isVisibleTo(dialog))
        self.assertIn("Removed 1 superseded revision", dialog.message.text())

    def test_references_panel_expanded_card_shows_copy_bibtex_when_verified(self):
        source, target, unverified = self.docs
        self.workspace.library.apply_verified(target["id"], {
            "provider": "crossref",
            "title": "Cited Verified Target",
            "authors": "M. Velli",
            "year": 2019,
            "doi": "10.1000/velli",
            "journal": "SoPh",
            "publication": ["SOPH", "294", "10", "2019"],
            "first_author": "Velli",
        })
        refs = [
            {"id": "1", "raw": "Velli, M. 2019, SoPh, 294, 10"},
            {"id": "2", "raw": "Unverified 2020"},
            {"id": "3", "raw": "Identity ref", "identity": {
                "provider": "arxiv", "title": "Preprint Study", "authors": "A. Author",
                "year": 2024, "arxiv_id": "2401.00001v1", "first_author": "Author",
            }},
        ]
        references._save(self.workspace.library, source["id"], {
            "status": "complete",
            "extraction_version": references.EXTRACTION_VERSION,
            "destination_version": references.DESTINATION_VERSION,
            "references": refs,
        })
        references.link(self.workspace.library, source["id"], "1", target["id"])
        references.link(self.workspace.library, source["id"], "2", unverified["id"])
        window = self.make_window()
        window.acquisition.main_opened = lambda _doc_id: None
        window.open_library_document(source["id"])
        self.pump(lambda: window.details.list.count() == 3)

        # Card 0 (verified linked document) has Copy BibTeX.
        window.details._expand(window.details.list.item(0))
        card0 = window.details.list.itemWidget(window.details.list.item(0))
        btn0 = next((b for b in card0.findChildren(QPushButton) if b.text() == "Copy BibTeX"), None)
        self.assertIsNotNone(btn0)
        btn0.click()
        self.assertIn("@article{Velli2019,", self.app.clipboard().text())

        # Card 1 (unverified linked document without identity) has NO Copy BibTeX.
        window.details._expand(window.details.list.item(1))
        card1 = window.details.list.itemWidget(window.details.list.item(1))
        btn1 = next((b for b in card1.findChildren(QPushButton) if b.text() == "Copy BibTeX"), None)
        self.assertIsNone(btn1)

        # Card 2 (verified identity) has Copy BibTeX.
        window.details._expand(window.details.list.item(2))
        card2 = window.details.list.itemWidget(window.details.list.item(2))
        btn2 = next((b for b in card2.findChildren(QPushButton) if b.text() == "Copy BibTeX"), None)
        self.assertIsNotNone(btn2)
        btn2.click()
        self.assertIn("@misc{Author2024,", self.app.clipboard().text())

    def test_session_save_restore_preserves_tabs_splits_labels_and_suppresses_acquisition(self):
        d1, d2, d3 = self.docs
        window = self.make_window()
        window.acquisition.main_opened = lambda _doc_id: None
        tab0 = window.open_library_document(d1["id"])
        window.set_tab_label(tab0, "Solar Loop")
        quick = window.open_library_document_in_quick_view(d2["id"], tab=tab0)
        tab0.shares[Qt.Vertical] = 0.6
        tab0.shares[Qt.Horizontal] = 0.35
        tab0.activate(tab0.main)  # active pane on tab0 is main, even though quick exists
        tab1 = window.open_library_document(d3["id"])
        self.pump(lambda: tab1.main.view.has_page(0))
        window.tabs.setCurrentIndex(0)
        self.pump(lambda: tab0.main.view.has_page(0) and quick.view.has_page(0))
        window.close()

        saved = self.workspace.get_session()
        self.assertEqual(saved["active_tab"], 0)
        self.assertEqual(len(saved["tabs"]), 2)
        self.assertEqual(saved["tabs"][0]["main_id"], d1["id"])
        self.assertEqual(saved["tabs"][0]["quick_id"], d2["id"])
        self.assertEqual(saved["tabs"][0]["label"], "Solar Loop")
        self.assertEqual(saved["tabs"][0]["active_pane"], "main")
        self.assertAlmostEqual(saved["tabs"][0]["shares"]["vertical"], 0.6)
        self.assertAlmostEqual(saved["tabs"][0]["shares"]["horizontal"], 0.35)

        # Delete d3 before restoring; restore_session skips d3 gracefully and suppresses acquisition.
        self.workspace.library.remove_documents([d3["id"]])
        self.workspace.close()

        # Opt in to restore_session=True: reopens d1+quick(d2), skips deleted d3, suppresses acquisition.
        settings.update_preferences({"restore_session": True})
        seen = {}

        def check_restored():
            windows = [w for w in self.app.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible()]
            if not windows:
                return QTimer.singleShot(20, check_restored)
            win = windows[0]
            try:
                seen["tabs"] = win.tabs.count()
                if seen["tabs"]:
                    seen["label"] = win.tabs.widget(0).label
                    seen["main_id"] = win.tabs.widget(0).main.view.doc.source.document_id
                    seen["quick_id"] = win.tabs.widget(0).quick.view.doc.source.document_id
                    seen["active_is_main"] = win.tabs.widget(0).active is win.tabs.widget(0).main
                    seen["share_v"] = win.tabs.widget(0).shares[Qt.Vertical]
                seen["acq_pending"] = set(win.acquisition.pending)
                seen["acq_dialogs"] = dict(win.acquisition.dialogs)
            finally:
                win.close()

        QTimer.singleShot(20, check_restored)
        self.assertEqual(native_app.run(["--data-dir", str(self.workspace.data_dir)]), 0)
        self.assertEqual(seen["tabs"], 1)
        self.assertEqual(seen["label"], "Solar Loop")
        self.assertEqual(seen["main_id"], d1["id"])
        self.assertEqual(seen["quick_id"], d2["id"])
        self.assertTrue(seen["active_is_main"])
        self.assertAlmostEqual(seen["share_v"], 0.6)
        self.assertEqual(seen["acq_pending"], set())
        self.assertEqual(seen["acq_dialogs"], {})

        # Opt out (restore_session=False): even with session.json present, startup shows Welcome with 0 tabs.
        settings.update_preferences({"restore_session": False})
        default_seen = {}

        def check_default():
            windows = [w for w in self.app.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible()]
            if not windows:
                return QTimer.singleShot(20, check_default)
            win = windows[0]
            try:
                default_seen["tabs"] = win.tabs.count()
                default_seen["is_welcome"] = win.reader_stack.currentWidget() is win.welcome
            finally:
                win.close()

        QTimer.singleShot(20, check_default)
        self.assertEqual(native_app.run(["--data-dir", str(self.workspace.data_dir)]), 0)
        self.assertEqual(default_seen, {"tabs": 0, "is_welcome": True})


if __name__ == "__main__":
    unittest.main()
