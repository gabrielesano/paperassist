"""Regression coverage for callbacks and controllers surviving native tab changes."""
import gc
import sys
import tempfile
import threading
import unittest
import weakref
from pathlib import Path
from unittest.mock import Mock, patch

from test_native_support import make_pdf, needs_qt, pdf_available, PySide6

if pdf_available and PySide6:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QDialog
    from shiboken6 import isValid

    from paperassist.native import shortcuts
    from paperassist.native.acquisition import AcquisitionController
    from paperassist.native.open_files import OpenFiles
    from paperassist.native.positions import PositionKeeper
    from paperassist.native.preferences_dialog import PreferencesDialog
    from paperassist.native.references_panel import ReferenceChoice
    from paperassist.native.renderer import RenderClient
    from paperassist.native.source import FileSource
    from paperassist.native.sources_dialog import CHECKS, SourcesDialog
    from paperassist.native.window import MainWindow
    from paperassist.settings import Settings
    from paperassist.workspace import Workspace


@needs_qt
class AuditLifecycleTests(unittest.TestCase):
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
        self.root = Path(folder.name)
        config = patch("paperassist.paths.config_file", return_value=self.root / "config.json")
        config.start()
        self.addCleanup(config.stop)
        self.window = MainWindow(self.client)
        shortcuts.install(self.window)
        self.window.show()
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        for dialog in self.window.findChildren(QDialog):
            dialog.reject()
        for pane in self.window.all_panes():
            pane.release()
        self.window.hide()
        self.window.deleteLater()
        self.flush_deletes()

    def flush_deletes(self):
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def open_pdf(self, name):
        return self.window.open_source(FileSource(make_pdf(self.root / f"{name}.pdf")))

    def test_sources_completion_after_dialog_deletion_is_disconnected(self):
        workspace = Mock()
        workspace.get_settings.return_value = Settings()
        release = threading.Event()
        workspace.check_settings.side_effect = lambda: release.wait(5) and {}
        dialog = SourcesDialog(workspace)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        errors = []
        with patch.object(sys, "excepthook", side_effect=lambda *args: errors.append(args)):
            dialog.check()
            worker = dialog.check_thread
            try:
                dialog.close()
                self.flush_deletes()
                self.assertFalse(isValid(dialog))
            finally:
                release.set()
                worker.wait(5000)
            self.flush_deletes()
        self.assertEqual(errors, [])
        self.assertNotIn(worker, CHECKS)

    def test_close_reentry_does_not_ask_twice_or_release_panes(self):
        tab = self.open_pdf("close")
        nested = QCloseEvent()

        def confirm(_panes):
            self.window.closeEvent(nested)
            return False

        with patch.object(self.window, "confirm", side_effect=confirm) as ask, \
                patch.object(tab.main, "release") as release:
            event = QCloseEvent()
            self.window.closeEvent(event)
        self.assertEqual(ask.call_count, 1)
        self.assertFalse(nested.isAccepted())
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.window._closing)
        release.assert_not_called()

    def test_closed_views_are_not_retained_by_shortcuts(self):
        tab = self.open_pdf("retained")
        target = weakref.ref(tab.main.view)
        self.assertIn(tab.main.view, self.window.shortcuts.bound)
        self.window.close_tab(0)
        del tab
        self.flush_deletes()
        gc.collect()
        self.assertIsNone(target())
        self.assertEqual(len(self.window.shortcuts.bound), 0)

    def test_page_shortcut_on_welcome_does_not_open_overflow(self):
        with patch.object(self.window.main_bar.overflow, "popup") as popup:
            self.window.shortcuts.focus_page()
        popup.assert_not_called()

    def test_position_restore_queued_before_release_does_nothing(self):
        pane = self.open_pdf("position").main
        pane.view.doc.source.document_id = 1
        workspace = Mock()
        keeper = PositionKeeper(pane, workspace)
        pane.release()
        self.app.processEvents()
        workspace.get_view.assert_not_called()
        self.assertFalse(keeper.active)

    def test_promoted_quick_view_disconnects_old_tab(self):
        tab = self.open_pdf("main")
        self.window.open_quick(FileSource(make_pdf(self.root / "quick.pdf")))
        quick = tab.quick
        with patch.object(tab, "activate", wraps=tab.activate) as activate:
            self.window.promote_quick()
            activate.reset_mock()
            quick.view.activated.emit()
            activate.assert_not_called()
        self.assertIs(self.window.tab().main, quick)

    def test_switch_waits_for_pending_external_open_before_persisting(self):
        workspace = Workspace(self.root / "library")
        self.addCleanup(workspace.close)
        self.window.workspace = workspace
        self.window.file_opener = OpenFiles(self.window)
        self.window.file_opener.pending.append((str(self.root / "incoming.pdf"), False))
        dialog = PreferencesDialog(self.window)
        dialog._plan = {"action": "open"}
        with patch.object(workspace, "prepare_switch") as prepare:
            dialog._confirm_switch()
        prepare.assert_not_called()
        self.assertIsNone(self.window.switch_target)
        self.assertFalse((self.root / "config.json").exists())
        self.assertIn("still opening", dialog.message.text())

    def test_external_open_waits_to_hide_library_until_modal_closes(self):
        library = QDialog(self.window)
        self.window.library_dialog = library
        library.show()
        metadata = QDialog(library)
        metadata.setModal(True)
        metadata.show()
        self.app.processEvents()
        opener = OpenFiles(self.window)
        self.addCleanup(opener.close)
        opener.receive([str(self.root / "incoming.pdf")])
        self.assertTrue(library.isVisible())
        self.assertTrue(metadata.isVisible())
        self.assertTrue(opener.pending)

    def test_automatic_acquisition_offer_checks_active_tab_at_dispatch(self):
        workspace = Mock(data_dir=self.root)
        workspace.get_settings.return_value = Settings()
        self.window.workspace = workspace
        controller = AcquisitionController(self.window)
        self.addCleanup(controller.close)
        tab = self.open_pdf("active")
        tab.main.view.doc.source.document_id = 2
        controller.main_opened(1)
        controller.set_reference_data(1, [{"id": "1"}])
        self.app.processEvents()
        self.assertFalse(controller.dialogs)
        controller.main_opened(2)
        controller.set_reference_data(2, [{"id": "1"}])
        self.window.tabs.setCurrentIndex(-1)
        # Closing the tab before the queued offer must also suppress it.
        self.window.close_tab(0)
        self.app.processEvents()
        self.assertFalse(controller.dialogs)

    def test_citation_cache_invalidation_drops_results_already_in_flight(self):
        view = self.open_pdf("citation").main.view
        view.doc.source.document_id = 9
        panel = self.window.details
        panel.located[view] = {"1": [{"page": 1}]}
        panel.series[view] = ("1", 0, 1)
        panel.stepping.add(view)
        panel.revisions[9] = 0
        panel._invalidate_citations(9)
        panel._located(view, {"id": "1"}, [{"page": 2}], None, 9, 0)
        self.assertNotIn(view, panel.located)
        self.assertNotIn(view, panel.series)
        self.assertNotIn(view, panel.stepping)

    def test_long_citation_choice_fits_narrow_column(self):
        button = ReferenceChoice("Long reference & " * 100)
        self.addCleanup(button.deleteLater)
        button.resize(210, 32)
        button.show()
        self.app.processEvents()
        self.assertLessEqual(button.minimumSizeHint().width(), 210)
        self.assertIn("…", button.text())
        self.assertEqual(button.accessibleName(), button.toolTip())


if __name__ == "__main__":
    unittest.main()
