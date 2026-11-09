import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from paperassist import paths
from paperassist.cli import main
from paperassist.workspace import LibraryInUse, library_lock


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
        with redirect_stderr(stderr):
            self.assertEqual(main(['import', str(self.root)]), 1)
        self.assertFalse(library.exists())
        self.assertIn('missing', stderr.getvalue())

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

    def test_default_and_pdf_arguments_launch_the_native_reader(self):
        for arguments in ([], ['one.pdf', 'two.pdf', '--data-dir', str(self.directory)],
                          ['--self-test', 'one.pdf'], ['--self-test-ocr', 'scan.pdf'], ['--help'], ['--version']):
            with self.subTest(arguments=arguments), \
                    patch('paperassist.native.__main__.main', return_value=0) as native:
                self.assertEqual(main(arguments), 0)
                native.assert_called_once_with(arguments)

    def test_desktop_alias_launches_native_without_mutating_arguments(self):
        arguments = ['desktop', '--data-dir', str(self.directory)]
        with patch('paperassist.native.__main__.main', return_value=0) as native:
            self.assertEqual(main(arguments), 0)
        native.assert_called_once_with(arguments[1:])
        self.assertEqual(arguments[0], 'desktop')

    def test_console_entry_uses_process_arguments(self):
        with patch('sys.argv', ['paperassist', 'one.pdf']), \
                patch('paperassist.native.__main__.main', return_value=0) as native:
            self.assertEqual(main(), 0)
        native.assert_called_once_with(['one.pdf'])

    def test_retired_serve_command_explains_the_native_replacement(self):
        output = io.StringIO()
        with redirect_stderr(output), patch('paperassist.native.__main__.main') as native:
            self.assertEqual(main(['serve', '--open']), 1)
        native.assert_not_called()
        self.assertIn('web reader has been retired', output.getvalue())
        self.assertIn('Run paperassist', output.getvalue())

    def test_import_uses_data_dir_option(self):
        with patch('paperassist.library.Library.import_paths', return_value={'imported': 0}) as importer, \
             patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(main(['import', 'a.pdf', '--data-dir', str(self.directory)]), 0)
        importer.assert_called_once_with(['a.pdf'])
        self.assertTrue((self.directory / 'library.sqlite3').exists())

    def test_import_refuses_a_library_owned_by_a_reader(self):
        output = io.StringIO()
        with library_lock(self.directory), redirect_stderr(output):
            self.assertEqual(main(['import', 'a.pdf', '--data-dir', str(self.directory)]), 1)
        self.assertIn('Library is in use', output.getvalue())
        self.assertFalse((self.directory / 'library.sqlite3').exists())

    def test_reimport_deduplicates_and_preserves_source_bytes(self):
        from paperassist.library import Library
        from tests.test_library import pdf
        original = self.directory / 'source.pdf'
        original.write_bytes(pdf())
        library = self.directory / 'library'
        with patch('sys.stdout', new=io.StringIO()):
            for _ in range(2):
                self.assertEqual(main(['import', str(original), '--data-dir', str(library)]), 0)
        self.assertEqual(len(Library(library).list_documents()), 1)
        self.assertEqual(original.read_bytes(), pdf())


class LockTests(unittest.TestCase):
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
        with patch('paperassist.workspace.sys.platform', 'win32'), patch.dict('sys.modules', {'msvcrt': Msvcrt}):
            with library_lock(self.directory), self.assertRaises(LibraryInUse):
                with library_lock(self.directory):
                    pass


if __name__ == '__main__':
    unittest.main()
