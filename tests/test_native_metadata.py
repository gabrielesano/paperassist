"""Metadata correction and import estimates in the native Library window, on disposable libraries."""
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_native_support import make_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from paperassist.native.library_dialog import LibraryDialog, pdf_files, size_text
    from paperassist.native.metadata_dialog import MetadataDialog
    from paperassist.native.renderer import RenderClient
    from paperassist.native.window import MainWindow
    from paperassist.workspace import Workspace


@needs_qt
class MetadataCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name).resolve()
        config = patch("paperassist.paths.config_file", return_value=self.dir / "config.json")
        config.start()
        self.addCleanup(config.stop)
        self.workspace = Workspace(self.dir / "library")
        self.addCleanup(self.workspace.close)
        self.library = self.workspace.library

    def add(self, name, pages):
        path = make_pdf(self.dir / name, pages=pages)
        return self.library.import_stream(io.BytesIO(path.read_bytes()), name), path

    def pump(self, until, seconds=10):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end and not until():
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(until(), "timed out")


class CorrectionTests(MetadataCase):
    def test_a_correction_survives_importing_the_same_file_again(self):
        document, path = self.add("a.pdf", 3)
        self.library.update_metadata(document["id"], {"title": "Corrected title", "authors": "Ada Lovelace", "year": 2019, "doi": "10.1234/abc"})
        result = self.library.import_paths([path])
        self.assertEqual((result["imported"], result["duplicates"]), (0, 1))
        again = self.library.get_document(document["id"])
        self.assertEqual((again["title"], again["authors"], again["year"], again["doi"], again["metadata_status"]),
                         ("Corrected title", "Ada Lovelace", 2019, "10.1234/abc", "corrected"))

    def test_the_dialog_keeps_typed_values_and_explains_a_rejection(self):
        document, _ = self.add("a.pdf", 3)
        dialog = MetadataDialog(self.library, document)
        saved = []
        dialog.saved.connect(saved.append)
        for field, text in (("title", "New title"), ("year", "20x9"), ("doi", "not a doi")):
            dialog.fields[field].setText(text)
        dialog.save()
        self.assertIn("Year", dialog.error.text())
        dialog.fields["year"].setText("2019")
        dialog.save()
        self.assertEqual(dialog.error.text(), "Invalid DOI")
        self.assertEqual(dialog.fields["title"].text(), "New title")
        dialog.fields["title"].setText("")
        dialog.fields["doi"].setText("https://doi.org/10.1234/ABC")
        dialog.save()
        self.assertEqual(dialog.error.text(), "Title cannot be empty")
        self.assertEqual(saved, [])
        self.assertEqual(self.library.get_document(document["id"])["title"], document["title"])  # nothing was half-saved
        dialog.fields["title"].setText("New title")
        dialog.save()
        self.assertEqual([d["doi"] for d in saved], ["10.1234/abc"])
        self.assertEqual(self.library.get_document(document["id"])["year"], 2019)

    def test_fields_have_accessible_names(self):
        document, _ = self.add("a.pdf", 3)
        dialog = MetadataDialog(self.library, document)
        self.assertEqual([f.accessibleName() for f in dialog.fields.values()], ["Title", "Tab name", "Authors", "Year", "DOI"])

    def test_tab_name_can_be_edited_and_saved(self):
        document, _ = self.add("a.pdf", 3)
        dialog = MetadataDialog(self.library, document)
        dialog.fields["label"].setText("My Nickname")
        dialog.save()
        self.assertEqual(self.library.get_document(document["id"])["label"], "My Nickname")


class LibraryDialogTests(MetadataCase):
    def setUp(self):
        super().setUp()
        self.dialog = LibraryDialog(self.workspace)
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.typing.stop)

    def titles(self):
        return [self.dialog.table.topLevelItem(i).text(0) for i in range(self.dialog.table.topLevelItemCount())]

    def test_an_empty_library_shows_a_hint_instead_of_an_empty_table(self):
        self.dialog.show()
        self.assertTrue(self.dialog.hint.isVisible())
        self.assertFalse(self.dialog.table.isVisible())
        self.assertFalse(self.dialog.edit_button.isEnabled())
        self.add("a.pdf", 3)
        self.dialog.refresh()
        self.assertFalse(self.dialog.hint.isVisible())
        self.assertTrue(self.dialog.table.isVisible())

    def test_a_search_without_results_is_not_the_empty_library(self):
        self.add("a.pdf", 3)
        self.dialog.show()
        self.dialog.search.setText("zzz")
        self.dialog.refresh()
        self.assertFalse(self.dialog.hint.isVisible())

    def test_search_finds_the_displayed_review_status_and_removes_corrected_papers(self):
        document, _ = self.add("unidentified.pdf", 3)
        self.dialog.show()
        self.assertEqual(self.dialog.table.topLevelItem(0).text(4), "Needs review")
        QTest.keyClicks(self.dialog.search, "Needs review")
        self.pump(lambda: not self.dialog.typing.isActive())
        self.assertEqual(self.titles(), ["unidentified"])
        self.library.update_metadata(document["id"], {"title": "Confirmed title"})
        self.dialog.refresh()
        self.assertEqual(self.titles(), [])
        self.dialog.search.clear()
        self.pump(lambda: not self.dialog.typing.isActive())
        self.assertEqual(self.titles(), ["Confirmed title"])

    def test_editing_the_selected_paper_updates_the_list(self):
        self.add("a.pdf", 3)
        self.dialog.show()
        self.dialog.table.setCurrentItem(self.dialog.table.topLevelItem(0))
        self.dialog.edit_button.click()
        self.dialog.editor.fields["title"].setText("Loops and arcs")
        self.dialog.editor.fields["year"].setText("2021")
        self.dialog.editor.save()
        self.assertEqual(self.titles(), ["Loops and arcs"])
        self.assertEqual(self.dialog.table.topLevelItem(0).text(3), "2021")
        self.assertEqual(self.dialog.table.topLevelItem(0).text(4), "")  # corrected: no longer needs review

    def test_f2_edits_the_selected_paper(self):
        self.add("a.pdf", 3)
        self.dialog.show()
        self.dialog.table.setCurrentItem(self.dialog.table.topLevelItem(0))
        QTest.keyClick(self.dialog.table, Qt.Key_F2)
        self.assertTrue(self.dialog.editor.isVisible())

    def test_controls_have_accessible_names(self):
        self.assertEqual(self.dialog.search.accessibleName(), "Search the library")
        self.assertEqual(self.dialog.table.accessibleName(), "Papers")
        self.assertTrue(all(b.text() for b in (self.dialog.edit_button, self.dialog.import_button, self.dialog.folder_button)))

    def make_files(self):
        folder = self.dir / "incoming"
        (folder / "deep").mkdir(parents=True)
        make_pdf(folder / "one.pdf", pages=3)
        make_pdf(folder / "deep" / "TWO.PDF", pages=4)
        (folder / "notes.txt").write_text("not a paper")
        (folder / "scan.pdf.bak").write_bytes(b"%PDF-1.4 but not named so")
        return folder

    def test_a_folder_selection_reports_pdf_count_and_size_before_importing(self):
        folder = self.make_files()
        expected = sum(p.stat().st_size for p in (folder / "one.pdf", folder / "deep" / "TWO.PDF"))
        self.dialog.show()
        self.dialog.select_import([str(folder)])
        self.assertIn(f"2 PDFs, {size_text(expected)}", self.dialog.estimate.text())
        self.assertIn("extra storage", self.dialog.estimate.text())
        self.assertFalse(self.dialog.import_button.isEnabled())
        self.assertEqual(self.library.list_documents(), [])  # nothing is copied yet
        self.dialog.start_button.click()
        self.pump(lambda: self.dialog.watcher is None)
        self.assertEqual(len(self.library.list_documents()), 2)
        self.assertFalse(self.dialog.estimate.isVisible())
        self.assertTrue(self.dialog.import_button.isEnabled())
        self.assertEqual(self.dialog.message.text(),
                         "2 added, 0 already in the library, 2 need metadata review, 0 failed")

    def test_import_summary_separates_corrected_duplicates_review_files_and_failures(self):
        existing, path = self.add("existing.pdf", 3)
        self.library.update_metadata(existing["id"], {"title": "Confirmed title"})
        fresh = make_pdf(self.dir / "fresh.pdf", pages=4)
        bad = self.dir / "invalid.pdf"
        bad.write_bytes(b"not a PDF")
        self.dialog.show()
        self.dialog.select_import([str(path), str(fresh), str(bad)])
        self.dialog.start_button.click()
        self.pump(lambda: self.dialog.watcher is None)
        message = self.dialog.message.text()
        self.assertIn("1 added, 1 already in the library, 1 need metadata review, 1 failed", message)
        self.assertIn(str(bad), message)
        self.assertEqual(len(self.library.list_documents()), 2)

    def test_an_unreadable_only_selection_shows_its_error_instead_of_no_pdfs(self):
        blocked = self.dir / "blocked"
        blocked.mkdir()
        self.dialog.show()
        with patch("paperassist.library.os.scandir", side_effect=PermissionError("Access denied")):
            self.dialog.select_import([blocked])
        self.assertIn("Could not scan selection", self.dialog.message.text())
        self.assertIn(str(blocked), self.dialog.message.text())
        self.assertIn("Access denied", self.dialog.message.text())
        self.assertFalse(self.dialog.start_button.isVisible())
        self.assertEqual(self.dialog.pending, [])
        self.assertEqual(self.library.list_documents(), [])

    def test_a_partially_unreadable_selection_warns_and_imports_its_readable_files(self):
        folder = self.make_files()
        blocked = folder / "blocked"
        blocked.mkdir()
        scandir = os.scandir

        def scan(path):
            if Path(path) == blocked:
                raise PermissionError("Access denied")
            return scandir(path)

        self.dialog.show()
        with patch("paperassist.library.os.scandir", side_effect=scan):
            self.dialog.select_import([str(folder)])
            self.assertIn(str(blocked), self.dialog.message.text())
            self.assertIn("estimate unavailable", self.dialog.estimate.text())
            self.dialog.start_button.click()
            self.pump(lambda: self.dialog.watcher is None)
        self.assertIn("2 added, 0 already in the library, 2 need metadata review, 1 failed",
                      self.dialog.message.text())
        self.assertIn(str(blocked), self.dialog.message.text())
        self.assertEqual(len(self.library.list_documents()), 2)

    def test_a_selection_can_be_dropped_and_non_pdfs_alone_start_nothing(self):
        folder = self.make_files()
        self.dialog.show()
        self.dialog.select_import([str(folder / "one.pdf"), str(folder / "notes.txt")])
        self.assertIn("1 PDF,", self.dialog.estimate.text())
        self.dialog.discard_button.click()
        self.assertFalse(self.dialog.estimate.isVisible())
        self.assertTrue(self.dialog.import_button.isEnabled())
        self.assertEqual(self.library.list_documents(), [])
        self.dialog.select_import([str(folder / "notes.txt")])
        self.assertFalse(self.dialog.estimate.isVisible())
        self.assertEqual(self.dialog.message.text(), "No PDF files in the selection.")

    def test_symlinks_are_not_followed_like_the_importer(self):
        folder = self.make_files()
        try:
            os.symlink(folder / "one.pdf", folder / "link.pdf")
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available")
        self.assertEqual(sorted(p.name for p in pdf_files([folder])), ["TWO.PDF", "one.pdf"])

    def test_sizes_read_naturally(self):
        self.assertEqual([size_text(n) for n in (12, 2048, 5 * 1024**2, 3 * 1024**3)], ["12 bytes", "2.0 KB", "5.0 MB", "3.0 GB"])

    def test_single_import_can_open_a_new_or_duplicate_pdf_while_import_only_stays_in_library(self):
        path = make_pdf(self.dir / "single.pdf", pages=3)
        opened = []
        self.dialog.open_requested.connect(opened.append)
        self.dialog.show()
        self.dialog.select_import([str(path)])
        self.assertEqual(self.dialog.start_button.text(), "Import only")
        self.assertTrue(self.dialog.import_open_button.isVisible())
        self.dialog.start_button.click()
        self.pump(lambda: self.dialog.watcher is None)
        self.assertEqual(opened, [])
        self.assertTrue(self.dialog.isVisible())
        self.dialog.select_import([str(path)])
        self.dialog.import_open_button.click()
        self.pump(lambda: self.dialog.watcher is None)
        self.assertEqual(opened, [self.library.list_documents()[0]["id"]])
        self.assertTrue(self.dialog.isHidden())
        self.dialog.show()
        second = make_pdf(self.dir / "second.pdf", pages=4)
        self.dialog.select_import([str(second)])
        self.dialog.import_open_button.click()
        self.pump(lambda: self.dialog.watcher is None)
        self.assertEqual(len(opened), 2)
        self.assertNotEqual(*opened)

    def test_batch_and_failed_single_import_do_not_open_papers(self):
        folder = self.make_files()
        opened = []
        self.dialog.open_requested.connect(opened.append)
        self.dialog.show()
        self.dialog.select_import([str(folder)])
        self.assertFalse(self.dialog.import_open_button.isVisible())
        self.dialog.discard_button.click()
        bad = self.dir / "bad.pdf"
        bad.write_bytes(b"not a PDF")
        self.dialog.select_import([str(bad)])
        self.dialog.import_open_button.click()
        self.pump(lambda: self.dialog.watcher is None)
        self.assertEqual(opened, [])
        self.assertTrue(self.dialog.isVisible())
        self.assertIn("1 failed", self.dialog.message.text())

    def test_header_sorting_keeps_selection_and_missing_years_last_after_refresh(self):
        docs = [self.add(f"{name}.pdf", pages)[0] for name, pages in (("Zeta", 3), ("alpha", 4), ("Beta", 5))]
        for doc, year in zip(docs[:2], (2023, 1999)):
            self.library.update_metadata(doc["id"], {"year": year})
        self.dialog.show()
        self.assertEqual(self.titles(), ["alpha", "Beta", "Zeta"])
        self.dialog.table.topLevelItem(0).setSelected(True)
        self.dialog.table.topLevelItem(2).setSelected(True)
        selected = set(self.dialog.selected_ids())
        self.assertEqual(len(selected), 2)
        self.assertFalse(self.dialog.edit_button.isEnabled())
        self.assertFalse(self.dialog.open_button.isEnabled())
        for order, expected in ((Qt.AscendingOrder, ["alpha", "Zeta", "Beta"]),
                                (Qt.DescendingOrder, ["Zeta", "alpha", "Beta"])):
            self.dialog.table.sortItems(3, order)
            self.dialog.refresh()
            self.assertEqual(self.titles(), expected)
            self.assertEqual(set(self.dialog.selected_ids()), selected)

    def test_metadata_menu_refreshes_selected_or_all_even_when_search_is_active(self):
        docs = [self.add(name, pages)[0] for name, pages in (("alpha.pdf", 3), ("Beta.pdf", 4), ("Zeta.pdf", 5))]
        self.dialog.show()
        self.dialog.online_metadata.setChecked(False)
        self.dialog.table.topLevelItem(0).setSelected(True)
        self.dialog.table.topLevelItem(2).setSelected(True)
        original = self.library.refresh_metadata
        with patch.object(self.library, "refresh_metadata", wraps=original) as refresh:
            self.dialog.refresh_selected.trigger()
            self.assertFalse(self.dialog.refresh_button.isEnabled())
            self.pump(lambda: self.dialog.watcher is None)
            self.assertEqual({call.args[0] for call in refresh.call_args_list}, {docs[0]["id"], docs[2]["id"]})
        self.dialog.search.setText("alpha")
        self.dialog.refresh()
        with patch.object(self.library, "refresh_metadata", wraps=original) as refresh:
            self.dialog.refresh_all.trigger()
            self.pump(lambda: self.dialog.watcher is None)
            self.assertEqual({call.args[0] for call in refresh.call_args_list}, {doc["id"] for doc in docs})
        self.assertIn("3 checked", self.dialog.message.text())


class WindowRefreshTests(MetadataCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = RenderClient()

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()

    def test_a_correction_reaches_open_tabs_and_the_references_panel(self):
        document, _ = self.add("a.pdf", 3)
        window = MainWindow(self.client, self.workspace)
        self.addCleanup(window.deleteLater)
        window.show()
        tab = window.open_library_document(document["id"])
        self.addCleanup(lambda: [pane.release() for pane in window.all_panes()])
        window.show_library()
        dialog = window.library_dialog
        dialog.table.setCurrentItem(dialog.table.topLevelItem(0))
        with patch.object(window.details, "reload") as reload:
            dialog.edit_metadata()
            dialog.editor.fields["title"].setText("A much better title")
            dialog.editor.save()
        self.assertIn("A much better title", window.tabs.tabText(0))
        self.assertEqual(tab.main.title, "A much better title")
        reload.assert_called_once_with(document["id"])
        window.hide()


if __name__ == "__main__":
    unittest.main()
