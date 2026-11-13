"""Native acquisition presents existing backend jobs without making network requests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from test_native_support import needs_qt, pdf_available, PySide6

if pdf_available and PySide6:
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton
    from paperassist.native.acquisition import (AcquisitionController, AcquisitionDialog, SelectionStore,
                                               current_retrieval, provider_summary, proxy_url,
                                               reference_status, retrieval_details, search_links, web_url)
    from paperassist.settings import Settings


@needs_qt
class AcquisitionPresentationTests(unittest.TestCase):
    def test_old_results_remain_not_searched(self):
        ref = {"retrieval": {"status": "not_found", "summary": "Old provider set"}}
        self.assertEqual(current_retrieval(ref), {})
        self.assertEqual(reference_status(ref), "Not searched")
        self.assertIn("Earlier search", retrieval_details(ref))

    def test_distinct_results_and_link_warnings_stay_visible(self):
        statuses = {reference_status({"retrieval": {"version": 2, "status": value}})
                    for value in ("downloaded", "local", "not_identified", "ambiguous", "blocked", "not_found", "failed")}
        self.assertEqual(len(statuses), 7)
        self.assertIn("source paper", reference_status({"local_invalid": True}))
        self.assertIn("verification", reference_status({"local_document_id": 9, "local_conflict": "Different DOI"}))

    def test_attempts_show_outcomes_without_treating_missing_configuration_as_failure(self):
        text = retrieval_details({"retrieval": {"version": 2, "summary": "Try again", "attempts": [
            {"provider": "crossref", "status": "failed", "message": "Timed out"},
            {"provider": "ads", "status": "not_configured"}, {"provider": "core", "status": "skipped"}]}})
        self.assertEqual(text, "Try again\nCrossref: Timed out")

    def test_search_queries_preserve_punctuation_and_have_separate_choices(self):
        links = dict(search_links({"title": "A & B: λ", "authors": "Müller", "year": "2026", "doi": "10.1/a+b"}))
        self.assertEqual(parse_qs(urlsplit(links["Search title"]).query)["q"], ["A & B: λ"])
        self.assertEqual(parse_qs(urlsplit(links["Search DOI"]).query)["q"], ["10.1/a+b"])
        self.assertIn("filetype:pdf", parse_qs(urlsplit(links["Search PDF"]).query)["q"][0])
        self.assertEqual(len(links), 4)

    def test_external_urls_reject_local_and_script_schemes(self):
        for value in (None, "file:///tmp/a.pdf", "javascript:alert(1)", "http://[bad"):
            self.assertIsNone(web_url(value))
        self.assertEqual(web_url("https://example.org/a.pdf"), "https://example.org/a.pdf")

    def test_proxy_matches_browser_encoding_and_prefix_rules(self):
        target = "https://example.org/a?b=1&c=2"
        self.assertEqual(proxy_url("https://proxy.org/?url={url}", target),
                         "https://proxy.org/?url=https%3A%2F%2Fexample.org%2Fa%3Fb%3D1%26c%3D2")
        self.assertEqual(proxy_url("https://proxy.org/login?url=", target), "https://proxy.org/login?url=" + target)
        self.assertIsNone(proxy_url("https://proxy.org/?url={url}", "file:///tmp/a"))

    def test_provider_status_uses_saved_configuration_without_exposing_keys(self):
        text = provider_summary(Settings({"keys": {"ads": "secret-never-shown"}, "enabled": {"core": False}}))
        self.assertIn("ADS: ready", text)
        self.assertIn("CORE: disabled", text)
        self.assertNotIn("secret", text)

    def test_selection_sidecar_preserves_other_sources_and_ignores_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelectionStore(directory)
            store.save(1, ["4", "2"])
            store.save(2, ["8"])
            self.assertEqual(store.load(1), {"2", "4"})
            self.assertEqual(store.load(2), {"8"})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            store.path.write_text("broken", encoding="utf-8")
            self.assertEqual(store.load(1), set())


@needs_qt
class AcquisitionWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = QMainWindow()
        self.workspace = self.window.workspace = Mock(data_dir=Path(self.directory.name))
        self.workspace.get_settings.return_value = Settings()
        self.controller = AcquisitionController(self.window)
        self.refs = [{"id": "1", "title": "Missing paper", "doi": "10.1/test"},
                     {"id": "2", "title": "Local paper", "local_document_id": 7},
                     {"id": "3", "title": "Previous reference", "legacy": True}]
        self.controller.set_reference_data(11, self.refs)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.controller.close()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def test_offer_is_modeless_and_never_starts_a_search(self):
        self.controller.offer(11)
        dialog = self.controller.dialogs[11]
        self.assertFalse(dialog.isModal())
        self.workspace.download_references.assert_not_called()
        self.assertEqual(dialog.count.text(), "0 selected")
        self.assertFalse(self.controller.selections.path.exists())  # opening the offer changes nothing on disk
        dialog.boxes[0].setChecked(True)
        self.assertEqual(self.controller.selections.load(11), {"1"})

    def test_loose_file_window_has_no_acquisition_or_selection_sidecar(self):
        window = QMainWindow()
        window.workspace = None
        controller = AcquisitionController(window)
        controller.main_opened(None)
        controller.offer(None)
        self.assertIsNone(controller.selections)
        self.assertFalse(controller.dialogs)
        controller.close()
        window.deleteLater()

    def test_selection_excludes_local_legacy_and_stale_ids_and_survives_reopen(self):
        self.controller.selections.save(11, ["1", "2", "3", "999"])
        dialog = AcquisitionDialog(self.controller, 11, self.refs)
        self.assertEqual(dialog.selected(), ["1"])
        self.assertFalse(dialog.boxes[1].isEnabled())
        self.assertFalse(dialog.boxes[2].isEnabled())
        dialog.select_all(False)
        self.assertEqual(dialog.count.text(), "0 selected")
        self.assertFalse(dialog.selected_button.isEnabled())
        dialog.select_all(True)
        self.assertEqual(self.controller.selections.load(11), {"1"})
        dialog.close()

    def test_ampersand_in_reference_title_escapes_mnemonic_in_checkbox(self):
        title = "Fang, X., Xia, C., & Keppens — A&A"
        refs = [{"id": "4", "title": title}]
        dialog = AcquisitionDialog(self.controller, 11, refs)
        self.addCleanup(dialog.close)
        box = dialog.boxes[0]
        self.assertIn("&&", box.text())
        self.assertTrue(QKeySequence.mnemonic(box.text()).isEmpty())
        self.assertEqual(box.text(), "Fang, X., Xia, C., && Keppens — A&&A")
        self.assertEqual(box.toolTip().strip(), title)
        self.assertNotIn("&&", box.toolTip())

    def test_long_reference_titles_wrap_and_remain_clickable_and_accessible(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QAccessible
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QScrollArea
        title = ('A long study of Müller & 李: λ-dependent transport 🧪 ' * 8
                 + 'https://example.org/' + 'unbroken' * 30)
        dialog = AcquisitionDialog(self.controller, 11, [{'id': '4', 'title': title}])
        self.addCleanup(dialog.close)
        dialog.resize(680, 560)
        dialog.show()
        self.app.processEvents()
        box = dialog.boxes[0]
        self.assertGreater(box.height(), box.fontMetrics().height() * 3)
        self.assertGreater(box.heightForWidth(300), box.heightForWidth(500))
        self.assertEqual(dialog.findChild(QScrollArea).horizontalScrollBar().maximum(), 0)
        self.assertEqual(QAccessible.queryAccessibleInterface(box).text(QAccessible.Name), title)
        box.setFocus()
        QTest.keyClick(box, Qt.Key_Space)
        self.assertEqual(dialog.selected(), ['4'])
        QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(box.width() // 2, box.height() - 2))
        self.assertEqual(dialog.selected(), [])
        original_height = box.height()
        font = box.font()
        font.setPointSizeF(font.pointSizeF() * 2)
        box.setFont(font)
        QTest.qWait(30)  # nested scroll/layout requests need more than one event-loop pass
        self.assertGreater(box.height(), original_height)
        self.assertEqual(dialog.findChild(QScrollArea).horizontalScrollBar().maximum(), 0)
        self.workspace.download_references.assert_not_called()

    def test_new_main_open_waits_for_references_and_offers_once(self):
        self.controller.main_opened(22)
        self.assertIn(22, self.controller.pending)
        self.assertNotIn(22, self.controller.dialogs)
        self.controller.set_reference_data(22, self.refs)
        self.app.processEvents()
        first = self.controller.dialogs[22]
        self.controller.set_reference_data(22, self.refs)
        self.app.processEvents()
        self.assertIs(self.controller.dialogs[22], first)
        self.workspace.download_references.assert_not_called()

    def test_background_local_resolution_updates_an_already_open_offer(self):
        self.controller.offer(11)
        dialog = self.controller.dialogs[11]
        dialog.select_all(True)
        self.controller.set_reference_data(11, [{**self.refs[0], "local_document_id": 8}, *self.refs[1:]])
        self.assertFalse(dialog.boxes[0].isEnabled())
        self.assertEqual(dialog.selected(), [])
        self.assertEqual(dialog.count.text(), "0 selected")
        self.assertFalse(dialog.selected_button.isEnabled())

    def test_reanalysis_rebuilds_offer_rows_and_preserves_current_selection(self):
        self.controller.offer(11)
        dialog = self.controller.dialogs[11]
        dialog.boxes[0].setChecked(True)
        refs = [{**self.refs[0], "title": "Updated title"}, {"id": "4", "title": "New reference"}]
        self.controller.set_reference_data(11, refs)
        self.assertEqual(list(dialog.rows), ["1", "4"])
        self.assertEqual(dialog.selected(), ["1"])
        self.assertIn("Updated title", dialog.boxes[0].text())
        dialog.select_all(True)
        self.assertEqual(dialog.selected(), ["1", "4"])

    def test_other_reference_tasks_wait_for_the_current_job(self):
        self.controller.jobs["running"] = Mock()
        start, done = Mock(), Mock()
        self.controller._task(11, start, done)
        start.assert_not_called()
        done.assert_not_called()
        self.assertIn("Wait for the current", self.controller.status.text())

    def test_deselected_current_link_candidate_cannot_be_accepted(self):
        from PySide6.QtWidgets import QDialogButtonBox, QTreeWidget
        self.workspace.library.list_documents.return_value = [{"id": 4, "title": "Candidate"}]
        self.controller.choose_link(11, self.refs[0])
        dialog = self.controller._link_dialog
        table = dialog.findChild(QTreeWidget)
        table.setCurrentItem(table.topLevelItem(0))
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertTrue(buttons.button(QDialogButtonBox.Ok).isEnabled())
        table.clearSelection()
        self.assertIsNotNone(table.currentItem())
        self.assertFalse(buttons.button(QDialogButtonBox.Ok).isEnabled())
        with patch.object(self.controller, "link") as link:
            buttons.accepted.emit()
        link.assert_not_called()
        dialog.close()

    def test_deselected_current_folder_candidate_cannot_be_accepted(self):
        from PySide6.QtWidgets import QDialogButtonBox, QTreeWidget
        self.controller._folder_result(11, self.refs[0], {"candidates": [{"name": "Candidate.pdf"}]})
        dialog = self.controller._folder_dialog
        table = dialog.findChild(QTreeWidget)
        table.setCurrentItem(table.topLevelItem(0))
        table.clearSelection()
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertFalse(buttons.button(QDialogButtonBox.Ok).isEnabled())
        with patch.object(self.controller, "link_folder_file") as link:
            buttons.accepted.emit()
        link.assert_not_called()
        dialog.close()

    def test_closing_drops_a_queued_acquisition_offer(self):
        self.controller.main_opened(11)
        self.controller.close()
        self.app.processEvents()
        self.assertFalse(self.controller.dialogs)

    def test_single_acquisition_opens_success_and_reports_unsearched_on_cancel(self):
        self.workspace.download_references.return_value = {"id": "job1", "status": "pending"}
        self.assertTrue(self.controller.acquire(11, ["1", "2", "3", "999"], open_id="1"))
        self.workspace.download_references.assert_called_once_with(11, ["1"])
        opened = []
        self.controller.open_requested.connect(opened.append)
        self.workspace.job_status.return_value = {"id": "job1", "status": "completed", "result": {
            "references": [{"id": "1", "status": "downloaded", "document_id": 8}]}}
        self.controller.jobs["job1"]._poll()
        self.assertEqual(opened, [8])
        self.assertIn("1 downloaded", self.controller.status.text())
        self.workspace.download_references.return_value = {"id": "job2"}
        self.controller.acquire(11, ["1"])
        self.workspace.job_status.return_value = {"id": "job2", "status": "cancelled", "result": {"references": []}}
        self.controller.jobs["job2"]._poll()
        self.assertIn("1 not searched", self.controller.status.text())

    def test_progress_and_cancel_use_workspace_job(self):
        self.workspace.download_references.return_value = {"id": "job"}
        self.controller.acquire(11, ["1"])
        self.controller._progress(11, {"progress": {"provider": "arxiv", "completed": 0, "total": 1}})
        self.assertIn("arXiv", self.controller.status.text())
        self.controller.cancel()
        self.workspace.cancel_job.assert_called_once_with("job")

    def test_completed_search_does_not_replace_a_different_tabs_quick_view(self):
        from types import SimpleNamespace
        source = SimpleNamespace(document_id=22)
        self.window.tab = lambda: SimpleNamespace(main=SimpleNamespace(view=SimpleNamespace(doc=SimpleNamespace(source=source))))
        opened = []
        self.controller.open_requested.connect(opened.append)
        self.controller._open_for_source(11, 8)
        self.assertEqual(opened, [])
        source.document_id = 11
        self.controller._open_for_source(11, 8)
        self.assertEqual(opened, [8])

    def test_missing_folder_explains_configuration_and_exposes_preferences_hook(self):
        requested = []
        self.controller.preferences_requested.connect(requested.append)
        self.controller.check_folder(11, self.refs[0])
        self.assertEqual(requested, ["inbox"])
        self.assertIn("No downloads folder", self.controller.status.text())
        self.workspace.check_folder.assert_not_called()

    def test_ambiguous_folder_requires_an_explicit_candidate(self):
        self.controller._folder_result(11, self.refs[0], {"status": "ambiguous", "summary": "Several PDFs match; choose one.",
            "candidates": [{"name": "a.pdf", "size": 1024}, {"name": "b.pdf", "size": 2048}]})
        dialog = self.controller._folder_dialog
        button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Link selected PDF")
        self.assertFalse(button.isEnabled())
        self.workspace.link_file.assert_not_called()
        dialog.close()

    def test_self_link_is_rejected_before_backend_and_invalid_link_remains_visible(self):
        self.controller.link(11, self.refs[0], 11)
        self.workspace.submit.assert_not_called()
        card = self.controller.card_actions(11, {"id": "1", "local_invalid": True, "manual_link": True})
        self.assertIn("Reset PDF link", [button.text() for button in card.findChildren(QPushButton)])
        card.deleteLater()

    def test_browser_links_only_open_after_explicit_button_click(self):
        with patch("paperassist.native.acquisition.QDesktopServices.openUrl") as open_url:
            card = self.controller.card_actions(11, self.refs[0])
            open_url.assert_not_called()
            button = next(button for button in card.findChildren(QPushButton) if button.text() == "Publisher / DOI")
            button.click()
            self.assertEqual(open_url.call_count, 1)
            card.deleteLater()

    def test_repair_downloads_runs_workspace_job_and_emits_changed(self):
        changed = []
        self.controller.changed.connect(changed.append)
        self.workspace.repair_downloads.return_value = {"id": "repair-1", "status": "pending"}
        self.controller.repair(11)
        self.workspace.repair_downloads.assert_called_once_with(11)
        self.assertIn("Checking earlier downloads", self.controller.status.text())
        self.workspace.job_status.return_value = {
            "id": "repair-1", "status": "completed", "result": {"documents": [{"id": 8}, {"id": 9}]},
        }
        self.controller.jobs["repair-1"]._poll()
        self.assertIn("2 results", self.controller.status.text())
        self.assertEqual(changed, [11])
