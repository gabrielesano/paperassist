"""Switch startup recovery, SIGTERM, and widget lifecycle through the native close guard."""
import gc
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import weakref
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfReader
from support import QtTestCase, make_pdf, needs_qt, pdf_available, qt_app

if pdf_available:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QDialog
    from shiboken6 import isValid

    from paperassist import paths
    from paperassist.native import app as native_app
    from paperassist.native.acquisition import AcquisitionController
    from paperassist.native.open_files import OpenFiles
    from paperassist.native.positions import PositionKeeper
    from paperassist.native.preferences_dialog import PreferencesDialog
    from paperassist.native.references_panel import ReferenceChoice
    from paperassist.native.source import FileSource
    from paperassist.native.sources_dialog import CHECKS, SourcesDialog
    from paperassist.native.window import MainWindow
    from paperassist.settings import Settings
    from paperassist.workspace import LibraryInUse, Workspace, library_lock


@needs_qt
class SwitchRecoveryTests(unittest.TestCase):
    def test_launch_files_are_not_reimported_when_switching_libraries(self):
        app = qt_app()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            old, target = root / 'old', root / 'target'
            Workspace(old).close()
            Workspace(target).close()
            seen = []

            def show(_app, workspace, pdfs):
                seen.append(list(pdfs))
                if len(seen) == 1:
                    return 0, workspace.prepare_switch(str(target), 'open')
                return 0, None

            with patch('paperassist.paths.config_file', return_value=root / 'config.json'), \
                    patch.object(native_app, 'show', side_effect=show):
                paths.save_library(old)
                self.assertEqual(native_app.run([str(root / 'source.pdf')]), 0)
            self.assertEqual(seen, [[str(root / 'source.pdf')], []])

    def test_startup_race_retries_delivery_and_reports_a_closing_owner(self):
        app = qt_app()
        with tempfile.TemporaryDirectory() as directory:
            Workspace(directory).close()
            with library_lock(directory), patch.object(native_app.QMessageBox, 'warning') as warning:
                with patch.object(native_app, 'forward', side_effect=[False, True]) as deliver:
                    self.assertEqual(native_app.run(['--data-dir', directory]), 0)
                    self.assertEqual(deliver.call_count, 2)
                    warning.assert_not_called()
                with patch.object(native_app, 'forward', side_effect=[False, RuntimeError('Window is closing')]):
                    self.assertEqual(native_app.run(['--data-dir', directory]), 1)
                    self.assertIn('Window is closing', warning.call_args.args[2])

    def test_failed_target_restores_the_previous_choice_and_reopens_its_lock(self):
        app = qt_app()
        for failure in ("missing", "locked", "corrupt"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory).resolve()
                stack.enter_context(patch("paperassist.paths.config_file", return_value=root / "config.json"))
                old, target = root / "old", root / "target"
                Workspace(old).close()
                Workspace(target).close()
                paths.save_library(old)
                seen, messages = [], []

                def show(_app, workspace, _pdfs):
                    seen.append(workspace)
                    if len(seen) == 1:
                        chosen = workspace.prepare_switch(str(target), "open")
                        if failure == "missing":
                            target.rename(root / "disconnected")
                        elif failure == "locked":
                            stack.enter_context(library_lock(target))
                        else:
                            (target / "library.sqlite3").write_bytes(b"not a database")
                        return 0, chosen
                    self.assertEqual(workspace.data_dir, old)
                    self.assertEqual(workspace.source, "settings")
                    with self.assertRaises(LibraryInUse), library_lock(old):
                        pass
                    return 0, None

                stack.enter_context(patch.object(native_app, "show", side_effect=show))
                stack.enter_context(patch.object(native_app.QMessageBox, "warning", side_effect=lambda *args: messages.append(args[2])))
                self.assertEqual(native_app.run([]), 0)
                self.assertEqual(len(seen), 2)
                self.assertEqual(paths.saved_library(), old)
                self.assertIn("Reopening the previous library", messages[0])
                with self.assertRaises(RuntimeError):
                    seen[0].executor.submit(lambda: None)
                with library_lock(old):
                    pass

    def test_signal_handler_is_restored_after_an_exception(self):
        app = qt_app()
        previous = signal.getsignal(signal.SIGTERM)
        probe_reader, probe_writer = socket.socketpair()
        probe_writer.setblocking(False)
        try:
            previous_wakeup = signal.set_wakeup_fd(probe_writer.fileno(), warn_on_full_buffer=False)
            try:
                with self.assertRaises(ValueError), native_app.termination_requests(app):
                    self.assertEqual([t for t in app.findChildren(QTimer) if t.isActive() and not t.isSingleShot()], [])
                    raise ValueError("startup failed")
                current_wakeup = signal.set_wakeup_fd(previous_wakeup)
                self.assertEqual(current_wakeup, probe_writer.fileno())
            finally:
                signal.set_wakeup_fd(previous_wakeup)
        finally:
            probe_reader.close()
            probe_writer.close()
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertEqual([t for t in app.findChildren(QTimer) if t.isActive() and not t.isSingleShot()], [])

    def test_unavailable_previous_library_reports_failure_without_creating_an_empty_one(self):
        app = qt_app()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch("paperassist.paths.config_file", return_value=root / "config.json"):
                old, target = root / "old", root / "target"
                Workspace(old).close()
                Workspace(target).close()
                paths.save_library(old)

                def disconnect(_app, workspace, _pdfs):
                    chosen = workspace.prepare_switch(str(target), "open")
                    target.rename(root / "disconnected-target")
                    (old / "library.sqlite3").rename(root / "disconnected-old.sqlite3")
                    return 0, chosen

                with patch.object(native_app, "show", side_effect=disconnect) as shown, \
                        patch.object(native_app.QMessageBox, "warning") as warning:
                    self.assertEqual(native_app.run([]), 1)
                self.assertEqual(shown.call_count, 1)
                self.assertEqual(warning.call_count, 2)
                self.assertFalse((old / "library.sqlite3").exists())
                self.assertFalse(target.exists())


@needs_qt
@unittest.skipUnless(os.name == "posix", "Requires POSIX SIGTERM delivery")
class NativeProcessTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.events = []

    def start(self, mode):
        self.errors = (self.root / "stderr.log").open("w+")
        self.addCleanup(self.errors.close)
        driver = Path(__file__).with_name("native_lifecycle_driver.py")
        # Cocoa sends process arguments to the application as file-open events.
        env = {**os.environ, "PAPERASSIST_TEST_MODE": mode, "PAPERASSIST_TEST_ROOT": str(self.root)}
        self.process = subprocess.Popen([sys.executable, str(driver)], env=env, stdout=subprocess.PIPE,
                                        stderr=self.errors, text=True)
        self.addCleanup(self.stop)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.addCleanup(self.selector.close)

    def stop(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.process.stdout.close()

    def event(self, kind):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self.selector.select(timeout=0.1):
                line = self.process.stdout.readline()
                if not line:
                    break
                value = json.loads(line)
                self.events.append(value)
                if value["type"] == kind:
                    return value
        self.errors.seek(0)
        self.fail(f"Missing {kind}: {self.events}\n{self.errors.read()}")

    def finish(self):
        finished = self.event("finished")
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(finished["code"], 0)
        self.assertTrue(finished["restored"])
        with library_lock(self.root / "library"):
            pass

    def test_sigterm_cancels_and_drains_jobs_before_releasing_the_library(self):
        self.start("job")
        ready = self.event("ready")
        self.process.send_signal(signal.SIGTERM)
        self.event("draining")
        with self.assertRaises(LibraryInUse), library_lock(self.root / "library"):
            pass
        self.finish()
        self.assertEqual(json.loads((self.root / "job-result.json").read_text()), {"cancelled": True, "locked": True})
        with self.assertRaises(ProcessLookupError):
            os.kill(ready["worker"], 0)

    def test_sigterm_cancel_keeps_edits_and_draft_then_discard_writes_nothing(self):
        self.start("cancel")
        self.event("ready")
        self.process.send_signal(signal.SIGTERM)
        self.event("prompt")
        self.process.send_signal(signal.SIGTERM)
        self.process.send_signal(signal.SIGTERM)
        cancelled = self.event("cancelled")
        self.assertEqual(cancelled, {"type": "cancelled", "modified": True, "draft": True, "prompts": 1})
        self.assertIsNone(self.process.poll())
        self.process.send_signal(signal.SIGTERM)
        self.finish()
        workspace = Workspace(self.root / "library")
        self.addCleanup(workspace.close)
        document = workspace.library.list_documents()[0]
        self.assertEqual(document["revision"], 0)
        self.assertEqual(workspace.library.get_notes(document["id"]), [])
        self.assertEqual(workspace.library.get_path(document["id"]).read_bytes(), (self.root / "source.pdf").read_bytes())

    def test_sigterm_save_persists_the_pdf_and_comment_only_after_the_choice(self):
        self.start("save")
        self.event("ready")
        self.process.send_signal(signal.SIGTERM)
        self.finish()
        workspace = Workspace(self.root / "library")
        self.addCleanup(workspace.close)
        document = workspace.library.list_documents()[0]
        self.assertEqual(document["revision"], 1)
        self.assertEqual(workspace.library.get_notes(document["id"])[0]["text"], "Pending lifecycle comment")
        self.assertEqual(workspace.library.get_path(document["id"], annotated=False).read_bytes(),
                         (self.root / "source.pdf").read_bytes())
        annotations = PdfReader(workspace.library.get_path(document["id"])).pages[0]["/Annots"]
        self.assertEqual(sum(item.get_object()["/Subtype"] == "/Ink" for item in annotations), 1)

    def test_failed_switch_recovers_in_the_real_window(self):
        self.start("fallback")
        recovered = self.event("recovered")
        self.assertEqual(recovered["directory"], str(self.root / "library"))
        self.assertEqual(recovered["source"], "settings")
        self.finish()
        self.assertEqual(len([event for event in self.events if event["type"] == "prompt"]), 1)


@needs_qt
class WidgetLifecycleTests(QtTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.dir
        self.window = MainWindow(self.client)
        self.window.show()
        self.addCleanup(lambda: self.cleanup_window(self.window))

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

    def test_closed_views_are_not_retained_after_tab_close(self):
        tab = self.open_pdf("retained")
        target = weakref.ref(tab.main.view)
        self.window.close_tab(0)
        del tab
        self.flush_deletes()
        gc.collect()
        self.assertIsNone(target())

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

    def test_queued_acquisition_offers_are_dropped_when_the_window_is_deleted(self):
        workspace = Workspace(self.root / "offer-library")
        self.addCleanup(workspace.close)
        window = MainWindow(self.client, workspace)
        controller = window.acquisition
        self.app.processEvents()
        with patch.object(AcquisitionController, "_offer_if_active") as offer:
            controller.main_opened(1)
            controller.set_reference_data(1, [])
            controller.main_opened(1)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
            self.assertFalse(isValid(controller))
            self.app.processEvents()
        offer.assert_not_called()

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
