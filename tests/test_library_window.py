"""The native window on a real library: opening, saving, positions, shared preferences and side columns."""
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, Fit, NameObject
from test_library import confirm
from test_native_support import make_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QMessageBox, QTreeWidget

    from paperassist import references, settings
    from paperassist.native import app as native_app
    from paperassist.native.engine import Rect
    from paperassist.native.library_dialog import LibraryDialog
    from paperassist.native.library_source import LibrarySource
    from paperassist.native.outline import read_outline
    from paperassist.native.positions import CSS_PER_POINT, PositionKeeper, current_page, to_scale, to_zoom
    from paperassist.native.references_panel import reference_matcher
    from paperassist.native.renderer import RenderClient
    from paperassist.native.source import FileSource
    from paperassist.native.window import MainWindow
    from paperassist.workspace import LibraryInUse, Workspace, library_lock


def cited_pdf(path, pages=3):
    """A paper that cites [1] and [2] and ends with a References heading and two entries."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Courier")}))
    for number in range(pages):
        lines = ([f"Page {number + 1}. Loops cool as shown in [1] and [2]."] if number < pages - 1 else
                 ["References", "[1] Smith, J. 2015, Journal of Loops, 1, 1", "[2] Brown, K. 2018, Journal of Arcs, 2, 2"])
        page = writer.add_blank_page(400, 500)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 11 Tf 20 430 Td 16 TL " + " T* ".join(f"({line}) Tj" for line in lines) + " ET").encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


@needs_qt
class LibraryWindowCase(unittest.TestCase):
    """A disposable library with papers of 3 to 5 pages (different content, so none is a duplicate)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name).resolve()
        config = patch("paperassist.paths.config_file", return_value=self.dir / "config.json")
        config.start()
        self.addCleanup(config.stop)
        self.workspace = Workspace(self.dir / "library")
        self.addCleanup(self.workspace.close)
        papers = [cited_pdf(self.dir / "p3.pdf", pages=3), cited_pdf(self.dir / "p4.pdf", pages=4), make_pdf(self.dir / "p5.pdf", pages=5)]
        self.docs = [self.workspace.library.import_stream(io.BytesIO(path.read_bytes()), path.name) for path in papers]
        self.failures = []

    def make_window(self, workspace=True, size=(1600, 900)):
        window = MainWindow(self.client, self.workspace if workspace else None)
        window.resize(*size)
        window.show()
        window.report_failure = lambda pane: self.failures.append(pane.view.doc.error)
        self.addCleanup(self.close_window, window)
        return window

    def close_window(self, window):
        window.confirm = lambda panes: True  # edits left over from a test are not the point of the cleanup
        window.close()  # finishes the background reads that write into the library folder
        window.deleteLater()

    def pump(self, until=None, seconds=0.2):
        end = time.perf_counter() + (10 if until else seconds)
        while time.perf_counter() < end and not (until and until()):
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(until() if until else True, "timed out")

    def open_document(self, window, document):
        tab = window.open_library_document(document["id"])
        view = tab.main.view
        self.pump(lambda: any(view.has_page(page) for page in view.visible_pages()))
        return tab


class StartUpTests(LibraryWindowCase):
    def test_a_library_open_in_the_browser_gives_a_message_and_a_clean_exit(self):
        shown = []
        with library_lock(self.workspace.data_dir), patch.object(QMessageBox, "warning", lambda *args: shown.append(args[2])):
            self.assertEqual(native_app.run(["--data-dir", str(self.workspace.data_dir)]), 1)
        self.assertIn("browser", shown[0])

    def test_the_lock_is_released_after_the_window_session(self):
        with native_app.open_workspace(self.workspace.data_dir) as workspace:
            self.assertEqual(workspace.library.list_documents()[0]["id"], self.docs[0]["id"])
            with self.assertRaises(LibraryInUse), library_lock(self.workspace.data_dir):
                pass
        with library_lock(self.workspace.data_dir):
            pass

    def test_a_missing_chosen_library_is_reported(self):
        shown = []
        settings.paths.save_library(self.dir / "unplugged")
        with patch.object(QMessageBox, "warning", lambda *args: shown.append(args[2])):
            self.assertEqual(native_app.run([]), 1)
        self.assertIn("missing", shown[0])


class SourceTests(LibraryWindowCase):
    def test_open_follows_the_newest_revision_and_save_creates_one(self):
        window = self.make_window()
        tab = self.open_document(window, self.docs[0])
        pane = tab.main
        self.assertEqual(pane.key, f"library:{self.docs[0]['id']}")
        window.highlight_action.setChecked(True)
        pane.view.doc.add_highlight(0, [Rect(40, 60, 200, 74)])
        pane.view.edited.emit()
        window.save()
        self.assertEqual(self.workspace.library.get_document(self.docs[0]["id"])["revision"], 1)
        self.assertEqual(pane.view.doc.source.revision, 1)
        saved = PdfReader(self.workspace.library.get_path(self.docs[0]["id"]))
        self.assertEqual(len([a for a in saved.pages[0]["/Annots"] if a.get_object()["/Subtype"] == "/Highlight"]), 1)
        self.assertFalse(pane.modified)
        self.assertTrue(pane.view.doc.source.path.name.endswith("-1.pdf"))

    def test_a_revision_conflict_fails_visibly_and_keeps_the_edits(self):
        window = self.make_window()
        pane = self.open_document(window, self.docs[0]).main
        pane.view.doc.add_highlight(0, [Rect(40, 60, 200, 74)])
        pane.view.edited.emit()
        # Another view of the library saved the paper first.
        self.workspace.library.save_pdf(self.docs[0]["id"], self.workspace.library.get_path(self.docs[0]["id"]).read_bytes(), 0)
        window.save()
        self.assertTrue(pane.modified)
        self.assertEqual(pane.state.value, "Save failed")
        self.assertIn("saved again", self.failures[0])
        self.assertEqual(self.workspace.library.get_document(self.docs[0]["id"])["revision"], 1)

    def test_an_invalid_result_is_a_failed_save_too(self):
        window = self.make_window()
        pane = self.open_document(window, self.docs[0]).main
        pane.view.doc.add_highlight(0, [Rect(40, 60, 200, 74)])
        with patch.object(self.workspace.library, "save_pdf", side_effect=ValueError("Invalid PDF")):
            window.save()
        self.assertTrue(pane.modified)
        self.assertIn("rejected", self.failures[0])

    def test_the_same_paper_in_a_tab_and_a_quick_view_share_one_save_authority(self):
        window = self.make_window()
        tab = self.open_document(window, self.docs[0])
        quick = window.open_library_document_in_quick_view(self.docs[0]["id"])
        self.pump(lambda: quick.view.has_page(0))
        tab.main.view.doc.add_highlight(0, [Rect(40, 60, 200, 74)])
        tab.main.view.edited.emit()
        self.assertTrue(quick.view.locked)
        window.activate = None
        tab.activate(tab.main)
        window.save()
        self.assertFalse(quick.view.locked)
        self.assertEqual(quick.view.doc.source.revision, 1)  # reloaded, so it could save on top of the new revision
        self.assertEqual(len(window.all_panes()), 2)

    def test_a_paper_already_in_a_tab_is_selected_instead_of_opened_twice(self):
        window = self.make_window()
        first = self.open_document(window, self.docs[0])
        self.open_document(window, self.docs[1])
        self.assertIs(window.open_library_document(self.docs[0]["id"]), first)
        self.assertEqual(window.tabs.count(), 2)


class LibraryDialogTests(LibraryWindowCase):
    def setUp(self):
        super().setUp()
        self.workspace.library.update_metadata(self.docs[1]["id"], {"title": "Loops and nanoflares", "authors": "Ada Lovelace", "year": 2019})
        self.dialog = LibraryDialog(self.workspace)
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.typing.stop)  # a pending search must not run once the library is gone

    def titles(self):
        return [self.dialog.table.topLevelItem(i).text(0) for i in range(self.dialog.table.topLevelItemCount())]

    def test_search_covers_title_authors_year_and_doi_and_flags_papers_to_review(self):
        self.assertEqual(len(self.titles()), 3)
        for query in ("nanoflares", "lovelace", "2019", "LOVELACE 2019"):
            self.dialog.search.setText(query)
            self.dialog.refresh()
            self.assertEqual(self.titles(), ["Loops and nanoflares"], query)
        self.dialog.search.setText("nothing like this")
        self.dialog.refresh()
        self.assertEqual(self.dialog.message.text(), "No paper matches this search.")
        self.dialog.search.setText("")
        self.dialog.refresh()
        flags = {self.dialog.table.topLevelItem(i).text(0): self.dialog.table.topLevelItem(i).text(4)
                 for i in range(self.dialog.table.topLevelItemCount())}
        self.assertEqual(flags["Loops and nanoflares"], "")
        self.assertEqual(sorted(flags.values()).count("Needs review"), 2)

    def items(self):
        table = self.dialog.table
        return [table.topLevelItem(i) for i in range(table.topLevelItemCount())]

    def test_columns_show_the_title_even_when_a_tab_name_exists(self):
        header = self.dialog.table.headerItem()
        self.assertEqual([header.text(i) for i in range(5)], ["Title", "Tab name", "Authors", "Year", "Metadata"])
        self.workspace.library.set_label(self.docs[1]["id"], "lovelace2019")
        self.dialog.refresh()
        row = next(item for item in self.items() if item.data(0, Qt.UserRole) == self.docs[1]["id"])
        self.assertEqual([row.text(i) for i in range(5)],
                         ["Loops and nanoflares", "lovelace2019", "Ada Lovelace", "2019", ""])
        self.assertEqual(sum(bool(item.text(1)) for item in self.items()), 1)
        self.dialog.search.setText("lovelace2019")  # a paper can be found by its tab name
        self.dialog.refresh()
        self.assertEqual(self.titles(), ["Loops and nanoflares"])

    def test_year_sorts_numerically_and_the_review_colour_sits_on_the_metadata_column(self):
        self.workspace.library.update_metadata(self.docs[0]["id"], {"year": 987})
        self.dialog.refresh()
        self.dialog.table.sortItems(3, Qt.AscendingOrder)
        # 987 < 2019 only as numbers; as text "2019" would come first. Papers without a year come last.
        self.assertEqual([item.text(3) for item in self.items()], ["987", "2019", ""])
        self.dialog.table.sortItems(3, Qt.DescendingOrder)
        self.assertEqual([item.text(3) for item in self.items()], ["2019", "987", ""])
        for item in self.items():
            review = item.text(4) == "Needs review"
            self.assertEqual(item.foreground(4).color().name() == "#a15c00", review)
            self.assertNotEqual(item.foreground(0).color().name(), "#a15c00")

    def test_enter_and_the_buttons_ask_for_a_tab_or_the_quick_view(self):
        tabs, quick = [], []
        self.dialog.open_requested.connect(tabs.append)
        self.dialog.quick_requested.connect(quick.append)
        self.dialog.show()
        self.dialog.table.setCurrentItem(self.dialog.table.topLevelItem(0))
        wanted = self.dialog.selected_id()
        QTest.keyClick(self.dialog.table, Qt.Key_Return)
        self.assertEqual(tabs, [wanted])
        self.dialog.show()
        self.dialog.quick_button.click()
        self.assertEqual(quick, [wanted])

    def test_import_runs_as_a_job_with_progress_and_updates_the_list(self):
        new = [make_pdf(self.dir / f"new{n}.pdf", pages=n) for n in (6, 7)]
        self.dialog.start_import([str(path) for path in new] + [str(new[0])])
        self.assertFalse(self.dialog.import_button.isEnabled())
        self.pump(lambda: self.dialog.watcher is None)
        self.assertEqual(len(self.titles()), 5)
        self.assertIn("2 added, 1 already in the library", self.dialog.message.text())
        self.assertTrue(self.dialog.import_button.isEnabled())

    def test_a_running_import_can_be_cancelled(self):
        def slow_import(paths, progress=None, cancel=None):
            cancel.wait(5)
            return {"documents": [], "imported": 0, "duplicates": 0, "errors": [], "cancelled": cancel.is_set()}
        with patch.object(self.workspace.library, "import_paths", slow_import):
            self.dialog.start_import(["x.pdf"])
            self.pump(seconds=0.3)
            self.assertTrue(self.dialog.cancel_button.isVisibleTo(self.dialog))
            self.dialog.cancel_button.click()
            self.pump(lambda: self.dialog.watcher is None)
        self.assertIn("cancelled", self.dialog.message.text())

    def test_a_failed_import_is_reported(self):
        with patch.object(self.workspace, "import_paths", side_effect=ValueError("paths must be a list")):
            self.dialog.start_import(["x.pdf"])
        self.assertIn("Could not start the import", self.dialog.message.text())

    def test_the_window_opens_the_chosen_paper(self):
        window = self.make_window()
        window.show_library()
        window.library_dialog.table.setCurrentItem(window.library_dialog.table.topLevelItem(0))
        wanted = window.library_dialog.selected_id()
        QTest.keyClick(window.library_dialog.table, Qt.Key_Return)
        self.assertEqual(window.tab().main.key, f"library:{wanted}")
        self.assertTrue(window.library_dialog.isHidden())

    def test_opening_papers_and_closing_the_library_leave_metadata_refresh_running(self):
        window = self.make_window()
        window.show_library()
        dialog = window.library_dialog
        document_id = self.docs[0]["id"]
        item = next(dialog.table.topLevelItem(i) for i in range(dialog.table.topLevelItemCount())
                    if dialog.table.topLevelItem(i).data(0, Qt.UserRole) == document_id)
        dialog.table.setCurrentItem(item)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        from paperassist.metadata import extract_metadata

        def slow_extract(path, filename):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Test did not release metadata extraction")
            extracted = extract_metadata(path, filename)
            if filename == self.docs[0]["filename"]:
                extracted["title"] = "Refreshed paper title"
            return extracted

        with patch("paperassist.library.extract_metadata", side_effect=slow_extract):
            dialog.online_metadata.setChecked(False)
            dialog.refresh_all.trigger()
            self.pump(entered.is_set)
            watcher = dialog.watcher
            dialog.open_button.click()
            self.assertTrue(dialog.isHidden())
            tab = window.tab()
            view = tab.main.view
            self.assertEqual(tab.main.key, f"library:{document_id}")
            self.pump(lambda: view.has_page(0))
            view.doc.add_highlight(0, [Rect(20, 50, 120, 65)])
            view.edited.emit()
            view.goto(1)
            self.pump()
            document = view.doc
            self.assertTrue(watcher.timer.isActive())
            self.assertEqual(self.workspace.job_status(watcher.job_id)["status"], "running")

            window.show_library()
            self.assertIs(dialog.watcher, watcher)
            self.assertTrue(dialog.progress.isVisible())
            dialog.quick_button.click()
            self.assertTrue(dialog.isHidden())
            self.assertIsNotNone(tab.quick)
            window.show_library()
            dialog.close()
            self.assertTrue(dialog.isHidden())
            self.assertFalse(self.workspace.cancellations[watcher.job_id].is_set())
            self.pump()
            position = view.verticalScrollBar().value()
            release.set()
            self.pump(lambda: dialog.watcher is None)

        job = self.workspace.job_status(watcher.job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(len(job["result"]["documents"]), len(self.docs))
        self.assertTrue(dialog.isHidden())
        self.assertEqual(tab.main.title, "Refreshed paper title")
        self.assertEqual(tab.quick.title, "Refreshed paper title")
        self.assertIs(view.doc, document)
        self.assertTrue(view.doc.modified)
        self.assertEqual(view.verticalScrollBar().value(), position)


class OcrControlTests(LibraryWindowCase):
    """The refresh menu's OCR choices, against a workspace whose job the test controls."""

    def setUp(self):
        super().setUp()
        self.dialog = LibraryDialog(self.workspace)
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.typing.stop)
        self.set_statuses("needs_review", "verified", "incomplete")

    def set_statuses(self, *statuses):
        for document, status in zip(self.docs, statuses):
            confirm(self.workspace.library, document["id"], status, title=f"Paper {document['id']}")
        self.dialog.refresh()
        self.review = [document["id"] for document, status in zip(self.docs, statuses) if status in ("needs_review", "incomplete")]

    def start_fake_job(self, trigger):
        self.record = {"id": "fake", "status": "running"}
        self.calls = []
        self.workspace.refresh_metadata = lambda ids=None, **options: self.calls.append((ids, options)) or self.record
        self.workspace.job_status = lambda job_id: self.record
        self.workspace.cancel_job = lambda job_id: self.record.update(status="cancelled")
        trigger()
        self.dialog.watcher.timer.stop()  # the test drives the polling itself

    def finish(self, **result):
        self.record.update(status="completed", result={"documents": [], **result})
        self.dialog.watcher.finished.emit(self.record)

    def test_ocr_choices_map_to_the_refresh_mode(self):
        dialog = self.dialog
        self.assertTrue(dialog.ocr_metadata.isChecked())
        self.assertEqual(dialog.ocr_metadata.text(), "Read scanned PDFs with offline OCR")
        dialog.table.selectAll()
        count = len(self.docs)
        self.assertEqual(dialog.recover_selected.text(), f"Recover selected papers with OCR ({count})")
        for action, ocr_checked, ids, expected in (
                (dialog.refresh_selected, True, dialog.selected_ids(), "auto"),
                (dialog.refresh_review, True, self.review, "auto"),
                (dialog.refresh_all, True, None, "auto"),
                (dialog.refresh_selected, False, dialog.selected_ids(), "off"),
                (dialog.refresh_review, False, self.review, "off"),
                (dialog.refresh_all, False, None, "off"),
                (dialog.recover_selected, False, dialog.selected_ids(), "force")):
            dialog.ocr_metadata.setChecked(ocr_checked)
            self.start_fake_job(action.trigger)
            self.assertEqual(self.calls, [(ids, {"online": True, "ocr": expected})])
            self.finish()

    def test_review_refresh_sends_only_papers_that_need_review(self):
        dialog = self.dialog
        self.set_statuses("needs_review", "verified", "corrected")
        self.assertEqual(dialog.refresh_review.text(), "Refresh papers that need review (1)")
        self.assertEqual(dialog.refresh_all.text(), f"Force refresh entire library ({len(self.docs)})")
        self.start_fake_job(dialog.refresh_review.trigger)
        self.assertEqual(self.calls, [([self.docs[0]["id"]], {"online": True, "ocr": "auto"})])

    def test_review_count_ignores_the_search_filter(self):
        dialog = self.dialog
        self.assertEqual(len(self.review), 2)
        dialog.search.setText("no paper has this text")
        dialog.refresh()
        self.assertEqual(dialog.table.topLevelItemCount(), 0)
        self.assertEqual(dialog.refresh_review.text(), "Refresh papers that need review (2)")
        self.assertTrue(dialog.refresh_review.isEnabled())
        self.start_fake_job(dialog.refresh_review.trigger)
        self.assertEqual(self.calls[0][0], self.review)

    def test_review_refresh_is_disabled_when_nothing_needs_review(self):
        self.set_statuses("verified", "corrected", "verified")
        self.assertFalse(self.dialog.refresh_review.isEnabled())
        self.assertTrue(self.dialog.refresh_all.isEnabled())
        self.assertTrue(self.dialog.refresh_button.isEnabled())

    def test_ocr_actions_follow_the_selection_and_are_disabled_while_a_job_runs(self):
        dialog = self.dialog
        dialog.table.clearSelection()
        self.assertFalse(dialog.recover_selected.isEnabled())
        self.assertTrue(dialog.refresh_all.isEnabled())
        dialog.table.selectAll()
        self.assertTrue(dialog.recover_selected.isEnabled())
        self.start_fake_job(dialog.recover_selected.trigger)
        for action in (dialog.refresh_selected, dialog.refresh_review, dialog.refresh_all, dialog.recover_selected):
            self.assertFalse(action.isEnabled())
        self.assertFalse(dialog.refresh_button.isEnabled())
        dialog.start_metadata_refresh(dialog.selected_ids(), ocr="force")
        self.assertEqual(len(self.calls), 1)
        self.finish()
        self.assertTrue(dialog.recover_selected.isEnabled())

    def test_tooltips_say_that_ocr_is_local_and_provisional(self):
        for action in (self.dialog.ocr_metadata, self.dialog.recover_selected):
            for phrase in ("locally", "minute", "never modifies the PDF", "provisional"):
                self.assertIn(phrase, action.toolTip())

    def test_progress_names_the_ocr_stage_and_keeps_the_online_provider(self):
        self.dialog.table.selectAll()
        self.start_fake_job(self.dialog.refresh_selected.trigger)
        for progress, text in (({"completed": 2, "total": 5, "stage": "ocr", "page": 1}, "Refreshing metadata 2 of 5… (offline OCR, page 1)"),
                               ({"completed": 2, "total": 5, "stage": "ocr"}, "Refreshing metadata 2 of 5… (offline OCR)"),
                               ({"completed": 3, "total": 5, "provider": "arxiv"}, "Refreshing metadata 3 of 5… (arXiv)"),
                               ({"completed": 4, "total": 5, "provider": "crossref"}, "Refreshing metadata 4 of 5… (Crossref)")):
            self.dialog._progress({"progress": progress})
            self.assertEqual(self.dialog.message.text(), text)
        self.dialog.cancel_button.click()  # the existing button stops OCR through the job's cancel event
        self.assertEqual(self.record["status"], "cancelled")

    def test_summary_counts_papers_recovered_with_ocr_only_when_there_are_some(self):
        self.start_fake_job(self.dialog.refresh_all.trigger)
        self.finish(local=1, ocr=2)
        self.assertIn("1 improved locally", self.dialog.message.text())
        self.assertTrue(self.dialog.message.text().endswith(", 2 recovered with OCR"), self.dialog.message.text())
        self.start_fake_job(self.dialog.refresh_all.trigger)
        self.finish(local=1, ocr=0)
        self.assertNotIn("OCR", self.dialog.message.text())

    def test_a_corrected_paper_explains_that_refresh_keeps_only_the_corrected_fields(self):
        document = self.docs[0]
        self.workspace.library.update_metadata(document["id"], {"title": "My own title"})
        self.dialog.refresh()
        item = next(self.dialog.table.topLevelItem(i) for i in range(self.dialog.table.topLevelItemCount())
                    if self.dialog.table.topLevelItem(i).data(0, Qt.UserRole) == document["id"])
        self.assertIn("refresh keeps those fields and can still fill the others", item.toolTip(4))
        self.assertIn("Source: Corrected by you", item.toolTip(0))
        self.assertNotIn("Corrected by you", item.toolTip(2))
        self.assertIn("verified or corrected by you", self.dialog.refresh_review.toolTip())
        self.assertIn("Refresh selected papers", self.dialog.refresh_review.toolTip())

    def test_ocr_sources_are_labelled_as_provisional(self):
        listing = self.workspace.library.list_documents

        def with_sources(*args, **kwargs):
            documents = listing(*args, **kwargs)
            for document in documents:
                document["metadata"]["sources"] = {"title": "ocr_title", "authors": "ocr_authors", "year": "ocr_year"}
            return documents

        with patch.object(self.workspace.library, "list_documents", with_sources):
            self.dialog.refresh()
        item = self.dialog.table.topLevelItem(0)
        for column, label in ((0, "Offline OCR of the first page (provisional)"),
                              (2, "Offline OCR author lines (provisional)"), (3, "Offline OCR (provisional)")):
            self.assertIn(f"Source: {label}", item.toolTip(column))
        self.assertEqual(item.toolTip(1), "")


class PositionTests(LibraryWindowCase):
    def test_pdfjs_scales_map_both_ways(self):
        width, page = 1.2, 0.9
        self.assertEqual(to_zoom("page-width", width, page), (width, True))
        self.assertEqual(to_zoom("page-fit", width, page), (page, False))
        self.assertEqual(to_zoom("page-actual", width, page), (CSS_PER_POINT, False))
        self.assertEqual(to_zoom("auto", width, page), (width, False))
        self.assertEqual(to_zoom("auto", 3, page)[0], 1.25 * CSS_PER_POINT)
        self.assertAlmostEqual(to_zoom("1.5", width, page)[0], 2.0)  # PDF.js 150% is 2 logical pixels per point
        self.assertEqual(to_scale(2.0, False), "1.5")
        self.assertEqual(to_scale(0.9, True), "page-width")

    def test_a_stored_position_is_restored_when_the_paper_opens(self):
        self.workspace.save_view(self.docs[1]["id"], {"page": 3, "scale": "1.5"})
        view = self.open_document(self.make_window(), self.docs[1]).main.view
        self.pump(lambda: current_page(view) == 2 and not view.fit)
        self.assertAlmostEqual(view.zoom, 2.0, places=3)
        self.assertEqual(self.workspace.get_view(self.docs[1]["id"]), {"page": 3, "scale": "1.5"})  # restoring wrote nothing

    def test_every_pdfjs_mode_is_restored_and_a_closed_untouched_paper_keeps_its_value(self):
        for scale in ("page-width", "page-fit", "auto", "page-actual", 1.25):
            self.workspace.save_view(self.docs[0]["id"], {"page": 2, "scale": scale})
            window = self.make_window()
            pane = self.open_document(window, self.docs[0]).main
            view = pane.view
            self.pump(lambda: current_page(view) == 1)
            self.assertEqual(view.fit, scale == "page-width", scale)
            if scale == "page-actual":
                self.assertAlmostEqual(view.zoom, CSS_PER_POINT)
            window.close_tab(0)
            self.assertEqual(self.workspace.get_view(self.docs[0]["id"]), {"page": 2, "scale": scale}, scale)

    def test_scrolling_and_zooming_are_saved_after_a_pause_and_on_close(self):
        window = self.make_window()
        pane = self.open_document(window, self.docs[2]).main
        view = pane.view
        self.pump(seconds=0.1)
        view.zoom_by(1.25)
        view.goto(3)
        self.pump(lambda: self.workspace.get_view(self.docs[2]["id"]) is not None)
        saved = self.workspace.get_view(self.docs[2]["id"])
        self.assertEqual(saved["page"], 4)
        self.assertAlmostEqual(float(saved["scale"]), view.zoom / CSS_PER_POINT, places=3)
        view.goto(1)
        window.close_tab(0)  # the pause has not elapsed: closing stores it
        self.assertEqual(self.workspace.get_view(self.docs[2]["id"])["page"], 2)

    def test_the_position_belongs_to_each_exact_paper(self):
        window = self.make_window()
        for document, page in ((self.docs[0], 2), (self.docs[1], 3)):
            self.workspace.save_view(document["id"], {"page": page, "scale": "1"})
        first = self.open_document(window, self.docs[0]).main.view
        second = self.open_document(window, self.docs[1]).main.view
        self.pump(lambda: current_page(first) == 1 and current_page(second) == 2)

    def test_loose_files_have_no_position_store(self):
        window = self.make_window(workspace=False)
        pane = window.open_source(FileSource(make_pdf(self.dir / "loose.pdf"))).main
        self.pump(lambda: pane.view.has_page(0))
        self.assertEqual(pane.findChildren(PositionKeeper), [])


class ColumnTests(LibraryWindowCase):
    def test_plus_buttons_restore_only_the_corresponding_column(self):
        settings.update_preferences({"outline_expanded": False, "details_expanded": False})
        window = self.make_window()
        left, right = window.outline_show_button, window.details_show_button
        self.assertTrue(left.isVisible() and right.isVisible())
        self.assertEqual(left.accessibleName(), "Show Outline")
        left.click()
        self.assertTrue(window.outline_column.isVisible())
        self.assertFalse(window.details_column.isVisible())
        self.assertFalse(left.isVisible())
        self.assertTrue(right.isVisible())
        self.assertEqual((settings.preferences()["outline_expanded"], settings.preferences()["details_expanded"]), (True, False))
        right.click()
        self.assertTrue(window.details_column.isVisible())
        self.assertFalse(right.isVisible())
        window.outline_column.hide_requested.emit()
        self.assertTrue(left.isVisible())
        self.assertFalse(right.isVisible())
        self.assertTrue(window.details_column.isVisible())

    def test_preferences_are_shared_through_the_user_config(self):
        settings.update_preferences({"quick_view_layout": "side", "outline_expanded": False, "details_expanded": True})
        window = self.make_window()
        self.assertTrue(window.side_by_side)
        self.assertFalse(window.outline_column.isVisibleTo(window))
        self.assertTrue(window.details_column.isVisibleTo(window))
        tab = self.open_document(window, self.docs[0])
        self.assertTrue(tab.side_by_side)
        quick = window.open_library_document_in_quick_view(self.docs[1]["id"])
        quick.layout_button.click()
        self.assertEqual(settings.preferences()["quick_view_layout"], "stacked")
        window.details_column.hide_requested.emit()
        self.assertEqual(settings.preferences()["details_expanded"], False)

    def test_each_column_collapses_and_one_button_hides_or_restores_both(self):
        window = self.make_window()
        outline, details = window.outline_column, window.details_column
        self.assertTrue(outline.isVisibleTo(window) and details.isVisibleTo(window))
        window.toggle_columns()
        self.assertFalse(outline.isVisibleTo(window) or details.isVisibleTo(window))
        self.assertEqual(settings.preferences()["outline_expanded"], False)
        window.toggle_columns()
        self.assertTrue(outline.isVisibleTo(window) and details.isVisibleTo(window))
        outline.hide_requested.emit()  # its own "−" button
        self.assertFalse(outline.isVisibleTo(window))
        window.toggle_columns()  # one column is hidden: this brings both back
        self.assertTrue(outline.isVisibleTo(window) and details.isVisibleTo(window))
        self.assertEqual((settings.preferences()["outline_expanded"], settings.preferences()["details_expanded"]), (True, True))

    def test_the_outline_follows_the_bookmarks_and_navigates_the_main_paper(self):
        path = self.dir / "marked.pdf"
        writer = PdfWriter(clone_from=make_pdf(self.dir / "base.pdf", pages=6))
        chapter = writer.add_outline_item("Introduction", 0)
        writer.add_outline_item("Methods", 2, parent=chapter)
        writer.add_outline_item("Results", 4, fit=Fit.xyz(left=0, top=300, zoom=0))
        with path.open("wb") as out:
            writer.write(out)
        entries = read_outline(path.read_bytes())
        self.assertEqual([(title, level, page) for title, level, page, _ in entries],
                         [("Introduction", 0, 0), ("Methods", 1, 2), ("Results", 0, 4)])
        self.assertEqual(entries[2][3], 200)  # the page is 500 points high and the bookmark sits 300 above its bottom
        window = self.make_window()
        document = self.workspace.library.import_stream(io.BytesIO(path.read_bytes()), "marked.pdf")
        tab = self.open_document(window, document)
        outline = window.outline
        self.assertEqual(outline.topLevelItemCount(), 2)
        self.assertEqual(outline.topLevelItem(0).child(0).text(0), "Methods")
        outline.itemClicked.emit(outline.topLevelItem(1), 0)
        self.pump(lambda: current_page(tab.main.view) == 4)

    def test_a_paper_without_bookmarks_lists_its_pages(self):
        window = self.make_window()
        tab = self.open_document(window, self.docs[0])
        self.assertEqual([window.outline.topLevelItem(i).text(0) for i in range(window.outline.topLevelItemCount())],
                         ["Page 1", "Page 2", "Page 3"])
        window.outline.itemClicked.emit(window.outline.topLevelItem(2), 0)
        self.pump(lambda: current_page(tab.main.view) == 2)
        self.open_document(window, self.docs[2])  # the outline belongs to the main paper of the current tab
        self.assertEqual(window.outline.topLevelItemCount(), 5)


class ReferencesTests(LibraryWindowCase):
    def test_link_picker_preserves_candidate_rank_after_filtering_and_requires_explicit_link(self):
        library = self.workspace.library
        source_id = self.docs[2]["id"]
        lower = library.update_metadata(self.docs[0]["id"], {
            "title": "Alpha candidate", "authors": "Peres G., Guarrasi M., Reale F.", "year": 2014})
        higher = library.update_metadata(self.docs[1]["id"], {
            "title": "Zulu candidate", "authors": "Guarrasi M., Reale F.", "year": 2014})
        other_ids = []
        for pages, title in ((6, "Aardvark unrelated"), (7, "Beta unrelated")):
            path = make_pdf(self.dir / f"other{pages}.pdf", pages=pages)
            document = library.import_stream(io.BytesIO(path.read_bytes()), path.name)
            library.update_metadata(document["id"], {"title": title})
            other_ids.append(document["id"])
        ref = {"id": "1", "raw": "Guarrasi, M., Reale, F. 2014, A&A, 564, A48",
               "authors": "Guarrasi", "year": "2014"}
        references._save(library, source_id, {"references": [ref], "status": "partial",
                         "extraction_version": references.EXTRACTION_VERSION,
                         "destination_version": references.DESTINATION_VERSION})
        ref = references.get(library, source_id)["references"][0]
        ranked_ids = [higher["id"], lower["id"]]
        self.assertEqual([candidate["id"] for candidate in ref["local_candidates"]], ranked_ids)
        window = self.make_window()
        window.acquisition.choose_link(source_id, ref)
        dialog = window.acquisition._link_dialog
        table, search = dialog.findChild(QTreeWidget), dialog.findChild(QLineEdit)
        button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)

        def listed():
            return [table.topLevelItem(i).data(0, Qt.UserRole) for i in range(table.topLevelItemCount())]

        self.assertEqual(listed(), ranked_ids + other_ids)
        self.assertFalse(button.isEnabled())
        QTest.keyClicks(search, "candidate")
        self.pump(lambda: table.topLevelItemCount() == 2)
        self.assertEqual(listed(), ranked_ids)
        self.assertTrue(all(table.topLevelItem(i).text(3) == "Possible match — verify" for i in range(2)))
        self.assertFalse(references.get(library, source_id)["references"][0].get("manual_link"))
        table.setCurrentItem(table.topLevelItem(0))
        self.assertTrue(button.isEnabled())
        button.click()
        self.pump(lambda: not window.acquisition.jobs)
        linked = references.get(library, source_id)["references"][0]
        self.assertTrue(linked["manual_link"])
        self.assertEqual(linked["manual_target_id"], higher["id"])

    def test_the_filter_ignores_case_accents_and_word_order(self):
        matches = reference_matcher("antolin 2012")
        self.assertTrue(matches({"authors": "Antolín, J.", "year": "2012", "raw": "..."}))
        self.assertFalse(matches({"authors": "Antolín, J.", "year": "2013"}))
        self.assertTrue(reference_matcher("2012 ANTOLIN")({"label": "[3]", "raw": "Antolín 2012"}))
        self.assertTrue(reference_matcher("")({}))

    def panel_texts(self, window):
        return [window.details.list.item(i).text() for i in range(window.details.list.count())]

    def test_an_unanalyzed_paper_is_analyzed_as_a_job_and_a_local_reference_opens_in_the_quick_view(self):
        window = self.make_window()
        source = self.docs[0]
        self.assertEqual(references.get(self.workspace.library, source["id"])["status"], "not_analyzed")
        tab = window.open_library_document(source["id"])
        self.assertIn(source["id"], window.details.analyzing)  # busy, not blocked
        self.assertTrue(window.details.progress.isVisibleTo(window))
        self.pump(lambda: window.details.list.count() > 0)
        self.assertIn("Smith", self.panel_texts(window)[0])
        self.assertTrue(self.panel_texts(window)[0].endswith("Not searched"))
        window.details._open(window.details.list.item(0))
        self.assertIsNone(tab.quick)  # not in the library: nothing to open

        ref = references.get(self.workspace.library, source["id"])["references"][0]
        references.link(self.workspace.library, source["id"], ref["id"], self.docs[1]["id"])
        window.details.show_for(source["id"])
        self.pump(lambda: self.panel_texts(window)[0].endswith("In library"))
        self.assertTrue(self.panel_texts(window)[0].endswith("In library"))
        window.details._open(window.details.list.item(0))
        self.assertEqual(tab.quick.key, f"library:{self.docs[1]['id']}")
        window.details._open(window.details.list.item(0))
        self.assertEqual(len(window.all_panes()), 2)  # a second click on the same paper changes nothing

    def test_the_filter_box_narrows_the_list_and_is_remembered_per_paper(self):
        window = self.make_window()
        self.open_document(window, self.docs[0])
        self.pump(lambda: window.details.list.count() == 2)
        window.details.filter.setText("zzz")
        self.assertEqual(window.details.list.count(), 0)
        self.assertEqual(window.details.status.text(), "No matching references.")
        self.open_document(window, self.docs[1])
        self.pump(lambda: window.details.list.count() == 2)
        self.assertEqual(window.details.filter.text(), "")
        window.tabs.setCurrentIndex(0)
        self.assertEqual(window.details.filter.text(), "zzz")
        window.details.filter.setText("smith")
        self.assertEqual(window.details.list.count(), 1)
        self.assertEqual(window.details.status.text(), "1 of 2 references")

    def test_an_analysis_error_is_shown_with_a_way_to_retry(self):
        window = self.make_window()
        with patch.object(references, "analyze", side_effect=RuntimeError("the PDF has no text")):
            self.open_document(window, self.docs[0])
            self.pump(lambda: "no text" in window.details.status.text())
        self.assertTrue(window.details.retry.isVisibleTo(window))
        window.details.retry.click()
        self.pump(lambda: window.details.list.count() == 2)

    def test_loose_files_explain_that_references_need_the_library(self):
        window = self.make_window(workspace=False)
        window.open_source(FileSource(make_pdf(self.dir / "loose.pdf")))
        self.assertIn("papers in the library", window.details.status.text())


class ReferenceCardTests(LibraryWindowCase):
    """Cards are painted text; only the card in use owns buttons, and the keyboard reaches all of them."""

    def setUp(self):
        super().setUp()
        self.window = self.make_window()
        self.window.acquisition.main_opened = lambda document_id: None
        self.panel = self.window.details
        self.open_document(self.window, self.docs[0])
        self.pump(lambda: self.panel.list.count() == 2 and not self.panel.analyzing)

    def widgets(self):
        items = [self.panel.list.item(i) for i in range(self.panel.list.count())]
        return [widget for item in items if (widget := self.panel.list.itemWidget(item))]

    def test_only_the_expanded_card_owns_widgets(self):
        self.assertEqual(self.widgets(), [])
        first, second = self.panel.list.item(0), self.panel.list.item(1)
        self.panel._expand(first)
        self.assertEqual(self.widgets(), [self.panel.list.itemWidget(first)])
        self.panel._expand(second)
        self.assertEqual(self.widgets(), [self.panel.list.itemWidget(second)])
        self.panel._expand(first)
        self.panel.filter.setText("smith")  # a refill keeps the card in use only while it is still listed
        self.assertEqual(len(self.widgets()), 1)
        self.panel.filter.setText("zzz")
        self.assertEqual(self.widgets(), [])

    def test_cards_stay_reachable_openable_and_steppable_from_the_keyboard(self):
        refs = references.get(self.workspace.library, self.docs[0]["id"])["references"]
        references.link(self.workspace.library, self.docs[0]["id"], refs[0]["id"], self.docs[1]["id"])
        self.panel.reload(self.docs[0]["id"])
        self.pump(lambda: self.panel.list.count() == 2 and not self.panel.analyzing)
        self.window.activateWindow()
        list_, tab = self.panel.list, self.window.tab()
        list_.setFocus()
        list_.setCurrentRow(0)
        QTest.keyClick(list_, Qt.Key_Return)  # Enter expands and moves focus into the card
        (card,) = self.widgets()
        self.pump(seconds=0.1)
        self.assertGreaterEqual(card.height(), card.minimumSizeHint().height())  # nothing of the card is clipped
        self.assertEqual(card.count.accessibleName(), "Go to the next citation in the text")
        self.assertEqual(card.title.toolTip(), list_.item(0).toolTip())
        card.title.linkActivated.emit("open")  # what Enter does on the focused title link
        self.assertEqual(tab.quick.key, f"library:{self.docs[1]['id']}")
        found = [{"page": 1, "rects": [Rect(20, 70, 60, 80)]}, {"page": 2, "rects": [Rect(20, 70, 60, 80)]}]
        with patch.object(self.panel.view.client, "locate", create=True,
                          side_effect=lambda doc, ref, callback: callback(found, "")):
            QTest.keyClick(card.count, Qt.Key_Space)
            self.pump(lambda: card.count.text() == "1 of 2")
        self.assertIs(self.widgets()[0], card)  # stepping updates text in place, so focus stays on the button
        self.assertIn("1 of 2", list_.item(0).text())
        QTest.keyClick(card.count, Qt.Key_Escape)
        self.assertEqual(self.widgets(), [])
        self.pump(seconds=0.1)
        self.assertGreater(list_.visualItemRect(list_.item(0)).height(), 30)  # back to the painted size
        self.assertTrue(list_.hasFocus() or not self.window.isActiveWindow())

    def test_cards_show_status_tones_tooltips_and_the_partial_note(self):
        item = self.panel.list.item(0)
        self.assertEqual(item.text().count("\n"), 2)  # title, count, status
        self.assertIn("Smith", item.text())
        ref = self.panel._ref(item.data(Qt.UserRole + 1))
        self.assertEqual(item.toolTip(), ref["raw"])
        self.assertEqual(self.panel.list.accessibleName(), "References")
        ref["local_invalid"] = True
        self.panel._fill()
        self.assertEqual(self.panel.list.item(0).data(Qt.UserRole + 2), "#7a3216")
        self.assertEqual(self.panel.list.item(0).data(Qt.UserRole + 3)[0], "#b0482a")
        self.panel.payload_status = "partial"
        self.panel._fill()
        self.assertTrue(self.panel.analysis_note.isVisibleTo(self.window))
        self.panel.payload_status = "complete"
        self.panel._fill()
        self.assertFalse(self.panel.analysis_note.isVisibleTo(self.window))


if __name__ == "__main__":
    unittest.main()
