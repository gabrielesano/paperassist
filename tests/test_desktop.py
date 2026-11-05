"""Native lifecycle behavior without requiring a GUI installation."""

import io
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from paperassist.desktop import DesktopBridge, ManagedServer, run
from paperassist.picker import PickerUnavailable
from paperassist.server import LibraryInUse


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for desktop callback')
        time.sleep(.005)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.window = Mock()
        self.window.get_current_url.return_value = 'http://127.0.0.1:12345/'
        self.window.events.loaded.is_set.return_value = True
        self.closed = threading.Event()
        self.window.destroy.side_effect = self.closed.set
        self.callbacks = []
        self.window.evaluate_js.side_effect = lambda script, callback: self.callbacks.append(callback)
        self.bridge = DesktopBridge(self.window, 'http://127.0.0.1:12345')

    def test_unloaded_window_can_close_immediately(self):
        self.window.events.loaded.is_set.return_value = False
        self.assertTrue(self.bridge.closing())
        self.window.evaluate_js.assert_not_called()

    def test_pending_close_is_shared_and_approval_closes(self):
        self.assertFalse(self.bridge.closing())
        wait_for(lambda: self.callbacks)
        self.assertFalse(self.bridge.closing())
        self.assertEqual(len(self.callbacks), 1)
        self.callbacks[0](True)
        self.assertTrue(self.closed.wait(3))
        self.assertTrue(self.bridge.closing())

    def test_cancel_allows_another_close_attempt(self):
        self.assertFalse(self.bridge.closing())
        wait_for(lambda: self.callbacks)
        self.callbacks[0](False)
        wait_for(lambda: not self.bridge.closing() and len(self.callbacks) == 2)
        self.assertFalse(self.closed.is_set())
        self.callbacks[1](True)
        self.assertTrue(self.closed.wait(3))

    def test_foreign_page_closes_without_calling_the_app(self):
        self.window.get_current_url.return_value = 'https://example.com/'
        self.assertFalse(self.bridge.closing())
        self.assertTrue(self.closed.wait(3))
        self.window.evaluate_js.assert_not_called()

    def test_javascript_error_keeps_window_open_and_allows_retry(self):
        self.window.evaluate_js.side_effect = RuntimeError('page unavailable')
        with patch('sys.stderr', new=io.StringIO()):
            self.assertFalse(self.bridge.closing())
            wait_for(lambda: self.window.evaluate_js.call_count == 1)
            wait_for(lambda: not self.bridge.closing() and self.window.evaluate_js.call_count >= 2)
        self.assertFalse(self.closed.is_set())

    def test_native_close_does_not_block_gui_javascript_evaluation(self):
        finish = threading.Event()
        self.window.evaluate_js.side_effect = lambda script, callback: finish.wait(3)
        started = time.monotonic()
        try:
            self.assertFalse(self.bridge.closing())
            self.assertLess(time.monotonic() - started, .2)
        finally:
            finish.set()


class ServerSwitchTests(unittest.TestCase):
    def test_stopping_during_a_switch_also_stops_the_next_listener(self):
        first_stopped, second_stopped = threading.Event(), threading.Event()
        def serving(*args, on_listen, **kwargs):
            on_listen(SimpleNamespace(shutdown=first_stopped.set))
            first_stopped.wait(3)
            on_listen(SimpleNamespace(shutdown=second_stopped.set))
            second_stopped.wait(3)
        with patch('paperassist.desktop.serve', side_effect=serving):
            backend = ManagedServer('/unused', port=12345)
            backend.start()
            stopped = threading.Event()
            thread = threading.Thread(target=lambda: (backend.stop(), stopped.set()))
            thread.start()
            self.assertTrue(stopped.wait(3))
            self.assertTrue(second_stopped.is_set())
            thread.join(3)


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class DesktopTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.window = Mock()
        self.window.events = SimpleNamespace(closing=Event(), before_load=Event(), loaded=Event())
        self.webview = SimpleNamespace(create_window=Mock(return_value=self.window),
                                       start=Mock(), FileDialog=SimpleNamespace(FOLDER=10))
        self.backend = Mock()
        self.backend.start.return_value = 'http://127.0.0.1:12345'
        self.patchers = [patch.dict(sys.modules, {'webview': self.webview}),
                         patch('paperassist.desktop.ManagedServer', return_value=self.backend),
                         patch('paperassist.paths.config_file', return_value=self.root / 'config.json')]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_owned_backend_and_persistent_profile(self):
        self.assertEqual(run(self.root, 'option', 9010), 0)
        self.backend.start.assert_called_once()
        self.backend.stop.assert_called_once()
        self.webview.create_window.assert_called_once()
        self.assertEqual(self.webview.create_window.call_args.args[:2], ('PaperAssist', self.backend.start.return_value))
        self.webview.start.assert_called_once_with(private_mode=False, storage_path=str(self.root / 'webview'),
                                                     **({'gui': 'qt'} if sys.platform.startswith('linux') else {}))
        self.assertTrue((self.root / 'webview').is_dir())
        self.assertEqual(len(self.window.events.closing.handlers), 1)

    def test_gui_and_backend_startup_failure_always_clean_up(self):
        for target in (self.backend.start, self.webview.start):
            target.side_effect = RuntimeError('startup failed')
            with patch('sys.stderr', new=io.StringIO()) as output:
                self.assertEqual(run(self.root), 1)
            self.assertIn('startup failed', output.getvalue())
            target.side_effect = None
        self.assertEqual(self.backend.stop.call_count, 2)

    def test_attached_window_never_stops_the_existing_backend(self):
        self.backend.start.side_effect = LibraryInUse()
        with patch('paperassist.desktop.running_url', return_value='http://127.0.0.1:12345'):
            self.assertEqual(run(self.root), 0)
        self.backend.stop.assert_not_called()
        self.assertEqual(self.webview.create_window.call_args.args[1], 'http://127.0.0.1:12345')

    def test_library_owner_without_address_has_a_clear_error(self):
        self.backend.start.side_effect = LibraryInUse()
        with patch('paperassist.desktop.running_url', return_value=None), patch('sys.stderr', new=io.StringIO()) as output:
            self.assertEqual(run(self.root), 1)
        self.assertIn('desktop address is unavailable', output.getvalue())
        self.backend.stop.assert_not_called()

    def test_sigterm_cleans_up_and_restores_the_signal_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        self.webview.start.side_effect = lambda **kwargs: signal.raise_signal(signal.SIGTERM)
        self.assertEqual(run(self.root), 0)
        self.backend.stop.assert_called_once()
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_optional_dependency_error_has_an_actionable_message(self):
        with patch.dict(sys.modules, {'webview': None}), patch('sys.stderr', new=io.StringIO()) as output:
            self.assertEqual(run(self.root), 1)
        self.assertIn('paperassist[desktop]', output.getvalue())
        self.backend.start.assert_not_called()

    def test_native_folder_selection_cancel_and_failure(self):
        with patch('paperassist.desktop.ManagedServer', return_value=self.backend) as factory:
            run(self.root)
        choose = factory.call_args.args[3]
        self.window.create_file_dialog.return_value = (str(self.root),)
        self.assertEqual(choose(str(self.root)), str(self.root))
        self.window.create_file_dialog.assert_called_with(10, directory=str(self.root))
        self.window.create_file_dialog.return_value = None
        self.assertIsNone(choose(str(self.root)))
        self.window.create_file_dialog.side_effect = RuntimeError('dialog failed')
        with self.assertRaisesRegex(PickerUnavailable, 'dialog failed'):
            choose(str(self.root))


if __name__ == '__main__':
    unittest.main()
