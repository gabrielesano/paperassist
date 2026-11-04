import _thread
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from paperassist import paths
from paperassist.cli import main
from paperassist.server import LibraryInUse, library_lock, running_url, serve


class DataDirectoryTests(unittest.TestCase):
    def resolve(self, platform, env, home='/home/ada'):
        with patch.object(paths.sys, 'platform', platform), patch.dict(os.environ, env, clear=True), \
             patch.object(Path, 'home', return_value=Path(home)):
            return paths.default_data_dir()

    @unittest.skipIf(os.name == 'nt', 'POSIX-style absolute paths')
    def test_linux_uses_xdg_then_home(self):
        self.assertEqual(self.resolve('linux', {'XDG_DATA_HOME': '/xdg'}), Path('/xdg/paperassist'))
        self.assertEqual(self.resolve('linux', {}), Path('/home/ada/.local/share/paperassist'))
        self.assertEqual(self.resolve('linux', {'XDG_DATA_HOME': 'relative'}), Path('/home/ada/.local/share/paperassist'))

    @unittest.skipIf(os.name == 'nt', 'POSIX-style absolute paths')
    def test_macos_uses_application_support(self):
        self.assertEqual(self.resolve('darwin', {}), Path('/home/ada/Library/Application Support/PaperAssist'))

    def test_windows_uses_appdata(self):
        self.assertEqual(self.resolve('win32', {'APPDATA': '/appdata'}), Path('/appdata/PaperAssist'))
        self.assertEqual(self.resolve('win32', {}), Path('/home/ada/AppData/Roaming/PaperAssist'))

    def test_environment_overrides_platform_default(self):
        for platform in ('linux', 'darwin', 'win32'):
            self.assertEqual(self.resolve(platform, {'PAPERASSIST_DATA_DIR': '/library'}), Path('/library'))

    def test_option_overrides_environment(self):
        with patch.dict(os.environ, {'PAPERASSIST_DATA_DIR': '/library'}):
            self.assertEqual(paths.resolve_data_dir('/explicit'), Path('/explicit'))
            self.assertEqual(paths.resolve_data_dir(None), Path('/library'))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def test_no_command_serves_default_library_and_opens_browser(self):
        with patch('paperassist.cli.resolve_data_dir', return_value=self.directory), \
             patch('paperassist.server.serve') as serve_mock:
            self.assertEqual(main([]), 0)
        serve_mock.assert_called_once_with('127.0.0.1', 8765, self.directory, True)

    def test_serve_keeps_flags_and_does_not_open_browser_by_default(self):
        with patch('paperassist.server.serve') as serve_mock:
            main(['serve', '--port', '9000', '--data-dir', str(self.directory)])
            main(['serve', '--open', '--data-dir', str(self.directory)])
        self.assertEqual(serve_mock.call_args_list[0].args, ('127.0.0.1', 9000, self.directory, False))
        self.assertEqual(serve_mock.call_args_list[1].args, ('127.0.0.1', 8765, self.directory, True))

    def test_import_uses_data_dir_option(self):
        with patch('paperassist.library.Library.import_paths', return_value={'imported': 0}) as importer, \
             patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(main(['import', 'a.pdf', '--data-dir', str(self.directory)]), 0)
        importer.assert_called_once_with(['a.pdf'])
        self.assertTrue((self.directory / 'library.sqlite3').exists())

    def test_running_server_is_reported_and_reused(self):
        (self.directory / '.server.json').write_text(json.dumps({'url': 'http://127.0.0.1:8765'}), encoding='utf-8')
        message = io.StringIO()
        with library_lock(self.directory), redirect_stderr(message), patch('paperassist.cli.webbrowser.open') as opened:
            self.assertEqual(main(['serve', '--data-dir', str(self.directory)]), 1)
            opened.assert_not_called()
            with patch('paperassist.cli.resolve_data_dir', return_value=self.directory):
                self.assertEqual(main([]), 0)
            opened.assert_called_once_with('http://127.0.0.1:8765')
        self.assertIn('already open in a running PaperAssist server', message.getvalue())
        self.assertIn('http://127.0.0.1:8765', message.getvalue())


class LockAndServeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def test_lock_conflicts_while_held_and_is_reusable_after(self):
        with library_lock(self.directory):
            with self.assertRaisesRegex(LibraryInUse, 'Library is in use'):
                with library_lock(self.directory):
                    pass
        with library_lock(self.directory):
            pass

    def test_windows_lock_path_reports_conflict(self):
        class Msvcrt:
            LK_NBLCK = 2
            locked = False
            @classmethod
            def locking(cls, fd, mode, size):
                if cls.locked:
                    raise OSError('locked')
                cls.locked = True
        with patch('paperassist.server.sys.platform', 'win32'), patch.dict('sys.modules', {'msvcrt': Msvcrt}):
            with library_lock(self.directory), self.assertRaises(LibraryInUse):
                with library_lock(self.directory):
                    pass

    def test_missing_marker_gives_no_url(self):
        self.assertIsNone(running_url(self.directory))

    def test_serve_opens_browser_once_listening_and_cleans_up(self):
        opened = []
        def fake_open(url):
            opened.append(url)
            def visit_then_stop():
                # The page must already be reachable when the browser is asked to open it.
                urllib.request.urlopen(url + '/api/config').close()
                _thread.interrupt_main()
            threading.Thread(target=visit_then_stop, daemon=True).start()
        with patch('paperassist.server.webbrowser.open', fake_open), patch('sys.stdout', new=io.StringIO()) as out:
            # Port 0 asks the OS for a free port.
            serve('127.0.0.1', 0, self.directory, open_browser=True)
        self.assertRegex(opened[0], r'^http://127\.0\.0\.1:\d+$')
        self.assertIn(str(self.directory.resolve()), out.getvalue())
        self.assertFalse((self.directory / '.server.json').exists())

    def test_port_in_use_is_explained(self):
        with patch('sys.stdout', new=io.StringIO()):
            import socket
            with socket.socket() as busy:
                busy.bind(('127.0.0.1', 0))
                busy.listen()
                with self.assertRaisesRegex(RuntimeError, '--port'):
                    serve('127.0.0.1', busy.getsockname()[1], self.directory)

    def test_non_loopback_host_is_refused(self):
        with self.assertRaises(ValueError):
            serve('0.0.0.0', 0, self.directory)


if __name__ == '__main__':
    unittest.main()
