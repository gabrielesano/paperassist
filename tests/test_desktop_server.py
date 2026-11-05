"""Owned desktop backend integration, using disposable libraries only."""

import json
import subprocess
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from paperassist.library import Library
from paperassist.desktop import ManagedServer
from paperassist.server import LibraryInUse, create_app, library_lock, running_url


def call(url, route='/api/config', body=None, token=''):
    request = urllib.request.Request(url + route, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json', 'X-PaperAssist-Token': token})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


class ManagedServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # The server reports resolved paths; Windows may hand out an 8.3 short temp path.
        self.root = Path(temporary.name).resolve()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        config = patch('paperassist.paths.config_file', return_value=self.root / 'config.json')
        config.start()
        self.addCleanup(config.stop)

    def test_start_is_ready_and_stop_releases_marker_and_lock(self):
        backend = ManagedServer(self.root / 'library', port=self.port)
        try:
            url = backend.start()
            self.assertEqual(call(url)['library_path'], str(self.root / 'library'))
            self.assertEqual(running_url(self.root / 'library'), url)
            with self.assertRaises(LibraryInUse), library_lock(self.root / 'library'):
                pass
        finally:
            backend.stop()
        self.assertFalse(backend.thread.is_alive())
        self.assertIsNone(running_url(self.root / 'library'))
        with library_lock(self.root / 'library'):
            pass

    def test_second_backend_cannot_take_ownership(self):
        first = ManagedServer(self.root / 'library', port=self.port)
        second = ManagedServer(self.root / 'library', port=self.port)
        try:
            first.start()
            with self.assertRaises(LibraryInUse):
                second.start()
            self.assertEqual(call(f'http://127.0.0.1:{self.port}')['library_path'], str(self.root / 'library'))
        finally:
            second.stop()
            first.stop()

    def test_bind_failure_releases_library_and_reports_error(self):
        directory = self.root / 'library'
        with patch('paperassist.server.make_server', side_effect=OSError('in use')):
            backend = ManagedServer(directory, port=self.port)
            with self.assertRaisesRegex(RuntimeError, 'Cannot listen'):
                backend.start()
            backend.stop()
        self.assertIsNone(running_url(directory))
        with library_lock(directory):
            pass

    def test_switch_preserves_origin_and_releases_previous_library(self):
        first, second = self.root / 'first', self.root / 'second'
        Library(second)
        backend = ManagedServer(first, port=self.port)
        try:
            url = backend.start()
            token = call(url)['token']
            call(url, '/api/library/switch', {'path': str(second), 'action': 'open'}, token)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    current = call(url)
                    if current['library_path'] == str(second):
                        break
                except OSError:
                    pass
                time.sleep(.01)
            else:
                self.fail('Library did not switch')
            self.assertEqual(running_url(second), url)
            self.assertIsNone(running_url(first))
            with library_lock(first):
                pass
        finally:
            backend.stop()
        self.assertIsNone(running_url(second))

    def test_shutdown_keeps_lock_until_an_active_request_finishes(self):
        entered, finish = threading.Event(), threading.Event()
        def application(*args):
            app = create_app(*args)
            @app.get('/slow')
            def slow():
                entered.set()
                finish.wait(5)
                return {'finished': True}
            return app
        backend = ManagedServer(self.root / 'library', port=self.port)
        with patch('paperassist.server.create_app', side_effect=application):
            backend.start()
        request = threading.Thread(target=call, args=(f'http://127.0.0.1:{self.port}', '/slow'))
        request.start()
        self.assertTrue(entered.wait(5))
        draining = threading.Event()
        original_close = backend.server.server_close
        def close():
            draining.set()
            original_close()
        backend.server.server_close = close
        stop = threading.Thread(target=backend.stop)
        stop.start()
        try:
            self.assertTrue(draining.wait(5))
            self.assertTrue(stop.is_alive())
            with self.assertRaises(LibraryInUse), library_lock(self.root / 'library'):
                pass
        finally:
            finish.set()
            request.join(5)
            stop.join(5)
        self.assertFalse(stop.is_alive())

    def test_shutdown_cancels_jobs_and_keeps_lock_until_their_writes_finish(self):
        entered, finish = threading.Event(), threading.Event()
        cancel_seen, cancellation = [], []
        def importing(library, paths, progress=None, cancel=None):
            cancellation.append(cancel)
            entered.set()
            finish.wait(5)
            cancel_seen.append(cancel.is_set())
            return {'imported': 0}
        backend = ManagedServer(self.root / 'library', port=self.port)
        with patch.object(Library, 'import_paths', importing):
            try:
                backend.start()
                call(f'http://127.0.0.1:{self.port}', '/api/import', {'paths': ['example.pdf']}, call(f'http://127.0.0.1:{self.port}')['token'])
                self.assertTrue(entered.wait(5))
                stop = threading.Thread(target=backend.stop)
                stop.start()
                self.assertTrue(cancellation[0].wait(5))
                self.assertTrue(stop.is_alive())
                with self.assertRaises(LibraryInUse), library_lock(self.root / 'library'):
                    pass
            finally:
                finish.set()
                backend.stop()
                if 'stop' in locals():
                    stop.join(5)
        self.assertEqual(cancel_seen, [True])

    def test_os_lock_excludes_another_process(self):
        script = ('import sys; from paperassist.server import library_lock, LibraryInUse\n'
                  'try:\n with library_lock(sys.argv[1]): pass\n'
                  'except LibraryInUse: sys.exit(7)\n')
        with library_lock(self.root / 'library'):
            answer = subprocess.run([sys.executable, '-c', script, str(self.root / 'library')], capture_output=True, timeout=10)
            self.assertEqual(answer.returncode, 7, answer.stderr.decode())
        answer = subprocess.run([sys.executable, '-c', script, str(self.root / 'library')], capture_output=True, timeout=10)
        self.assertEqual(answer.returncode, 0, answer.stderr.decode())


class InjectedPickerTests(unittest.TestCase):
    def test_native_picker_uses_existing_protected_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            choose = Mock(return_value='/selected')
            app = create_app(directory, choose_folder=choose)
            client = app.test_client()
            self.assertEqual(client.post('/api/choose-folder', json={}).status_code, 403)
            choose.assert_not_called()
            headers = {'X-PaperAssist-Token': client.get('/api/config').json['token']}
            answer = client.post('/api/choose-folder', json={'start': '/start', 'title': 'Library'}, headers=headers)
            self.assertEqual(answer.json, {'path': '/selected'})
            choose.assert_called_once_with('/start', title='Library')
            choose.return_value = None
            self.assertEqual(client.post('/api/choose-folder', json={}, headers=headers).json, {'path': None})
            app.extensions['paperassist_shutdown']()


if __name__ == '__main__':
    unittest.main()
