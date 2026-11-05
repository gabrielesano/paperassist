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
from paperassist.cli import desktop_main, main
from paperassist.server import LibraryInUse, library_lock, running_url, serve


class DataDirectoryTests(unittest.TestCase):
    def locate(self, platform, env, home='/home/ada', option=None):
        with patch.object(paths.sys, 'platform', platform), patch.dict(os.environ, env, clear=True), \
             patch.object(Path, 'home', return_value=Path(home)):
            return paths.locate(option)

    @unittest.skipIf(os.name == 'nt', 'POSIX-style absolute paths')
    def test_linux_uses_xdg_then_home(self):
        self.assertEqual(self.locate('linux', {'XDG_DATA_HOME': '/xdg'}), (Path('/xdg/paperassist'), 'default'))
        self.assertEqual(self.locate('linux', {})[0], Path('/home/ada/.local/share/paperassist'))
        self.assertEqual(self.locate('linux', {'XDG_DATA_HOME': 'relative'})[0], Path('/home/ada/.local/share/paperassist'))

    @unittest.skipIf(os.name == 'nt', 'POSIX-style absolute paths')
    def test_macos_uses_application_support(self):
        self.assertEqual(self.locate('darwin', {})[0], Path('/home/ada/Library/Application Support/PaperAssist'))

    def test_windows_uses_appdata(self):
        self.assertEqual(self.locate('win32', {'APPDATA': '/appdata'})[0], Path('/appdata/PaperAssist'))
        self.assertEqual(self.locate('win32', {})[0], Path('/home/ada/AppData/Roaming/PaperAssist'))

    def test_environment_overrides_platform_default(self):
        for platform in ('linux', 'darwin', 'win32'):
            self.assertEqual(self.locate(platform, {'PAPERASSIST_DATA_DIR': '/library'}), (Path('/library'), 'environment'))

    def test_option_overrides_environment(self):
        self.assertEqual(self.locate('linux', {'PAPERASSIST_DATA_DIR': '/library'}, option='/explicit'), (Path('/explicit'), 'option'))

    @unittest.skipIf(os.name == 'nt', 'POSIX-style absolute paths')
    def test_config_file_is_per_user_and_outside_the_library(self):
        self.assertEqual(self.config('linux', {'XDG_CONFIG_HOME': '/xdg'}), Path('/xdg/paperassist/config.json'))
        self.assertEqual(self.config('linux', {'XDG_CONFIG_HOME': 'relative'}), Path('/home/ada/.config/paperassist/config.json'))
        self.assertEqual(self.config('linux', {}), Path('/home/ada/.config/paperassist/config.json'))
        self.assertEqual(self.config('darwin', {}), Path('/home/ada/Library/Preferences/PaperAssist/config.json'))
        self.assertEqual(self.config('win32', {'LOCALAPPDATA': '/local'}), Path('/local/PaperAssist/config.json'))
        self.assertEqual(self.config('win32', {}), Path('/home/ada/AppData/Local/PaperAssist/config.json'))

    def config(self, platform, env):
        with patch.object(paths.sys, 'platform', platform), patch.dict(os.environ, env, clear=True), \
             patch.object(Path, 'home', return_value=Path('/home/ada')):
            return paths.config_file()


class SavedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config'), 'XDG_DATA_HOME': str(self.root / 'data')})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(paths.sys, 'platform', 'linux')
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('PAPERASSIST_DATA_DIR', None)

    def test_saved_library_beats_the_default_but_not_the_user_decision(self):
        library = self.root / 'mine'
        paths.save_library(library)
        self.assertEqual(paths.locate(), (library, 'settings'))
        with patch.dict(os.environ, {'PAPERASSIST_DATA_DIR': '/env'}):
            self.assertEqual(paths.locate(), (Path('/env'), 'environment'))
            self.assertEqual(paths.locate('/opt'), (Path('/opt'), 'option'))

    def test_save_replaces_the_file_and_leaves_no_temporary_files(self):
        paths.save_library(self.root / 'one')
        paths.save_library(self.root / 'two')
        folder = paths.config_file().parent
        self.assertEqual([entry.name for entry in folder.iterdir()], ['config.json'])
        self.assertEqual(json.loads(paths.config_file().read_text(encoding='utf-8')), {'library': str(self.root / 'two')})

    def test_a_missing_chosen_library_stops_instead_of_starting_empty(self):
        library = self.root / 'unplugged'
        paths.save_library(library)
        stderr = io.StringIO()
        with redirect_stderr(stderr), patch('paperassist.cli._serve') as serve_library:
            self.assertEqual(main(['serve']), 1)
            self.assertEqual(main(['import', str(self.root)]), 1)
        serve_library.assert_not_called()
        self.assertFalse(library.exists())
        self.assertIn('missing', stderr.getvalue())

    def test_desktop_also_refuses_a_missing_chosen_library(self):
        paths.save_library(self.root / 'unplugged')
        with redirect_stderr(io.StringIO()), patch('paperassist.cli._desktop') as desktop:
            self.assertEqual(main(['desktop']), 1)
        desktop.assert_not_called()

    def test_unreadable_or_relative_choices_are_ignored(self):
        self.assertEqual(paths.locate()[1], 'default')
        paths.config_file().parent.mkdir(parents=True)
        for content in ('not json', '[]', '{"library": 3}', '{"library": "relative/path"}'):
            paths.config_file().write_text(content, encoding='utf-8')
            self.assertEqual(paths.locate()[1], 'default', content)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def test_no_command_serves_default_library_and_opens_browser(self):
        with patch('paperassist.cli.locate', return_value=(self.directory, 'default')), \
             patch('paperassist.server.serve') as serve_mock:
            self.assertEqual(main([]), 0)
        serve_mock.assert_called_once_with('127.0.0.1', 8765, self.directory, True, 'default')

    def test_serve_keeps_flags_and_does_not_open_browser_by_default(self):
        with patch('paperassist.server.serve') as serve_mock:
            main(['serve', '--port', '9000', '--data-dir', str(self.directory)])
            main(['serve', '--open', '--data-dir', str(self.directory)])
        self.assertEqual(serve_mock.call_args_list[0].args, ('127.0.0.1', 9000, self.directory, False, 'option'))
        self.assertEqual(serve_mock.call_args_list[1].args, ('127.0.0.1', 8765, self.directory, True, 'option'))

    def test_import_uses_data_dir_option(self):
        with patch('paperassist.library.Library.import_paths', return_value={'imported': 0}) as importer, \
             patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(main(['import', 'a.pdf', '--data-dir', str(self.directory)]), 0)
        importer.assert_called_once_with(['a.pdf'])
        self.assertTrue((self.directory / 'library.sqlite3').exists())

    def test_desktop_uses_the_resolved_library_and_explicit_port(self):
        with patch('paperassist.cli._desktop', return_value=0) as desktop:
            self.assertEqual(main(['desktop', '--data-dir', str(self.directory), '--port', '9000']), 0)
        desktop.assert_called_once_with(self.directory, 'option', 9000)

    def test_desktop_entry_point_uses_the_same_command_path(self):
        with patch('paperassist.cli._desktop', return_value=0) as desktop, \
             patch('paperassist.cli.locate', return_value=(self.directory, 'default')):
            self.assertEqual(desktop_main([]), 0)
        desktop.assert_called_once_with(self.directory, 'default', 8765)

    def test_desktop_returns_the_runtime_exit_code(self):
        with patch('paperassist.desktop.run', return_value=1):
            self.assertEqual(main(['desktop', '--data-dir', str(self.directory)]), 1)

    def test_running_server_is_reported_and_reused(self):
        (self.directory / '.server.json').write_text(json.dumps({'url': 'http://127.0.0.1:8765'}), encoding='utf-8')
        message = io.StringIO()
        with library_lock(self.directory), redirect_stderr(message), patch('paperassist.cli.webbrowser.open') as opened:
            self.assertEqual(main(['serve', '--data-dir', str(self.directory)]), 1)
            opened.assert_not_called()
            with patch('paperassist.cli.locate', return_value=(self.directory, 'default')):
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
