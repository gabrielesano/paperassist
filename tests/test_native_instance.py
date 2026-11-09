"""Local file delivery: framing, path identity, owner errors and dirty-pane reuse."""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from test_native_support import make_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QCoreApplication, QEvent, QUrl
    from PySide6.QtGui import QFileOpenEvent
    from PySide6.QtWidgets import QApplication
    from paperassist.native.instance import InstanceServer, absolute_paths, forward, server_name
    from paperassist.native.open_files import FileOpenEvents, OpenFiles
    from paperassist.native.renderer import RenderClient
    from paperassist.native.window import MainWindow
    from paperassist.workspace import Workspace, library_lock
    from paperassist.native.engine import Rect


@needs_qt
class InstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        patcher = patch('paperassist.native.instance.config_file', return_value=self.root / 'config.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.directory = self.root / 'library'
        self.directory.mkdir()

    def pump(self, until):
        deadline = time.monotonic() + 8
        while not until() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(until(), 'Timed out waiting for local file delivery')

    def request(self, paths, raw=False):
        script = '''
import json, sys
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from paperassist.native.instance import forward, server_name
from PySide6.QtNetwork import QLocalSocket
app = QCoreApplication([])
with patch('paperassist.native.instance.config_file', return_value=Path(sys.argv[1])):
    try:
        if sys.argv[4] == 'raw':
            socket = QLocalSocket()
            socket.connectToServer(server_name(sys.argv[2]))
            assert socket.waitForConnected(1000)
            assert socket.waitForReadyRead(1000)
            socket.readAll()
            socket.write(Path(sys.argv[3]).read_bytes())
            socket.waitForBytesWritten(1000)
            if not socket.canReadLine():
                socket.waitForReadyRead(1000)
            answer = bytes(socket.readAll())
            print(json.dumps(json.loads(answer) if answer else {'error': 'Disconnected'}))
        else:
            print(json.dumps({'ok': forward(sys.argv[2], json.loads(sys.argv[3]))}))
    except Exception as error:
        print(json.dumps({'error': str(error)}))
'''
        if raw:
            payload = self.root / 'request.txt'
            payload.write_text(paths)
        process = subprocess.Popen([sys.executable, '-c', script, str(self.root / 'config.json'),
                                    str(self.directory), str(payload) if raw else json.dumps(paths),
                                    'raw' if raw else 'open'], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.pump(lambda: process.poll() is not None)
            output, errors = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 0, errors)
            response = json.loads(output)
            return RuntimeError(response['error']) if 'error' in response else response['ok']
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_local_delivery_and_empty_activation_reach_the_owner(self):
        received = []
        with library_lock(self.directory):
            server = InstanceServer(self.directory, received.append)
            try:
                path = self.root / 'Paper è.pdf'
                self.assertIs(self.request([str(path)]), True)
                self.assertIs(self.request([]), True)
                self.assertEqual(received, [[str(path)], []])
                if os.name != 'nt':
                    self.assertEqual(Path(server_name(self.directory)).parent.stat().st_mode & 0o777, 0o700)
            finally:
                server.close()
        self.assertIs(forward(self.directory, []), False)

    def test_rejected_request_is_reported_without_starting_another_owner(self):
        def reject(paths):
            raise RuntimeError('Window is closing')
        with library_lock(self.directory):
            server = InstanceServer(self.directory, reject)
            try:
                error = self.request([])
                self.assertIsInstance(error, RuntimeError)
                self.assertIn('Window is closing', str(error))
            finally:
                server.close()

    def test_malformed_and_oversized_requests_cannot_reach_the_window(self):
        received = []
        with library_lock(self.directory):
            server = InstanceServer(self.directory, received.append)
            try:
                for payload in ('not JSON\n', '{"version":true,"paths":[]}\n',
                                '{"version":1,"paths":[null]}\n',
                                '{"version":1,"paths":[]}\n{}\n', 'x' * (256 * 1024 + 1)):
                    with self.subTest(payload=payload[:80]):
                        self.assertIsInstance(self.request(payload, raw=True), RuntimeError)
                self.assertEqual(received, [])
                self.assertIs(self.request([]), True)
            finally:
                server.close()

    def test_aliases_share_the_endpoint_and_different_libraries_do_not(self):
        other = self.root / 'another'
        other.mkdir()
        self.assertNotEqual(server_name(self.directory), server_name(other))
        alias = self.root / 'alias'
        try:
            alias.symlink_to(self.directory, target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlinks are unavailable')
        self.assertEqual(server_name(self.directory), server_name(alias))

    def test_local_urls_and_relative_paths_are_normalized_before_forwarding(self):
        path = self.root / 'Spaces è % in name.pdf'
        self.assertEqual(absolute_paths([str(path), QUrl.fromLocalFile(str(path)).toString()]), [str(path)])
        local = QUrl.fromLocalFile(str(path))
        local.setHost('localhost')
        self.assertEqual(absolute_paths([local.toString()]), [str(path)])
        self.assertTrue(Path(absolute_paths(['relative.pdf'])[0]).is_absolute())
        for bad in ([None], [''], ['\0'], ['file://another-host/private.pdf'], 'not a list'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                absolute_paths(bad)

    def test_finder_events_wait_until_a_receiver_is_ready(self):
        events = FileOpenEvents(self.app)
        try:
            path = str(self.root / 'Paper è.pdf')
            self.assertTrue(self.app.sendEvent(self.app, QFileOpenEvent(path)))
            self.assertEqual(events.pending, [path])
            received = []
            events.receiver = received.append
            events.flush()
            self.assertEqual(received, [[path]])
            self.assertEqual(events.pending, [])
        finally:
            self.app.removeEventFilter(events)
            events.deleteLater()


@needs_qt
class ManagedWindowOpeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        patcher = patch('paperassist.paths.config_file', return_value=self.root / 'config.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = Workspace(self.root / 'library')
        self.addCleanup(self.workspace.close)
        self.client = RenderClient()
        self.addCleanup(self.client.shutdown)
        self.window = MainWindow(self.client, self.workspace)
        self.window.acquisition.main_opened = lambda document_id: None
        self.window.file_opener = OpenFiles(self.window)
        self.window.show()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.file_opener.close()
        self.window.confirm = lambda panes: True
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def pump(self, until):
        deadline = time.monotonic() + 10
        while not until() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(until(), 'PDF opening did not finish')

    def test_reopening_selects_the_dirty_pane_and_retains_a_comment_draft(self):
        path = make_pdf(self.root / 'Original è.pdf', pages=2)
        original = path.read_bytes()
        opener = self.window.file_opener
        opener.receive([str(path)])
        self.pump(lambda: not opener.busy)
        pane = self.window.pane()
        self.pump(lambda: pane.view.has_page(0))
        pane.view.doc.add_highlight(0, [Rect(40, 60, 120, 74)])
        pane.view.edited.emit()
        self.window.comments.compose()
        self.window.comments.panel.text.setPlainText('Unfinished comment')
        doc = pane.view.doc
        opener.receive([str(path)])
        self.pump(lambda: not opener.busy)
        self.assertIs(self.window.pane(), pane)
        self.assertIs(pane.view.doc, doc)
        self.assertTrue(pane.modified)
        self.assertTrue(self.window.comments.has_draft(pane))
        self.assertEqual(self.window.tabs.count(), 1)
        self.assertEqual(len(self.workspace.library.list_documents()), 1)
        self.assertEqual(path.read_bytes(), original)
        document_id = pane.view.doc.source.document_id
        self.assertEqual(self.workspace.library.get_document(document_id)['revision'], 0)
        self.assertTrue(pane.save())
        self.assertEqual(self.workspace.library.get_document(document_id)['revision'], 1)
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_file_does_not_replace_the_open_reader(self):
        path = make_pdf(self.root / 'valid.pdf')
        opener = self.window.file_opener
        opener.receive([str(path)])
        self.pump(lambda: not opener.busy)
        pane = self.window.pane()
        opener.receive([str(self.root / 'absent.pdf')])
        self.pump(lambda: not opener.busy)
        self.assertIs(self.window.pane(), pane)
        self.assertIn('Could not open PDF', self.window.statusBar().currentMessage())

    def test_unreadable_pdf_error_is_not_replaced_with_a_success_message(self):
        path = self.root / 'broken.pdf'
        path.write_bytes(b'%PDF-1.7\nnot a readable PDF\n')
        self.window.file_opener.receive([str(path)])
        self.pump(lambda: not self.window.file_opener.busy)
        self.assertEqual(self.window.tabs.count(), 0)
        self.assertIn('Could not open PDF', self.window.statusBar().currentMessage())
