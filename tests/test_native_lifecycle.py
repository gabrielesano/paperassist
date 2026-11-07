"""Switch startup recovery and SIGTERM through the real native close guard."""
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from test_native_support import needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from paperassist import paths
    from paperassist.native import app as native_app
    from paperassist.workspace import LibraryInUse, Workspace, library_lock


@needs_qt
class SwitchRecoveryTests(unittest.TestCase):
    def test_failed_target_restores_the_previous_choice_and_reopens_its_lock(self):
        app = QApplication.instance() or QApplication([])
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
        app = QApplication.instance() or QApplication([])
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
        app = QApplication.instance() or QApplication([])
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
        self.process = subprocess.Popen([sys.executable, str(driver), mode, str(self.root)],
                                        stdout=subprocess.PIPE, stderr=self.errors, text=True)
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
        deadline = time.monotonic() + 15
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
