import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperassist import location
from paperassist.library import Library

copy2 = shutil.copy2  # patching location.shutil patches the shared module


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.current = self.root / 'current'
        Library(self.current)
        (self.current / 'originals').mkdir(exist_ok=True)
        (self.current / 'originals' / 'a.pdf').write_bytes(b'%PDF one')
        (self.current / 'working').mkdir(exist_ok=True)
        (self.current / '.token').write_text('secret', encoding='utf-8')
        # A running server's markers must not travel with the library.
        (self.current / '.server.lock').write_text('', encoding='utf-8')
        (self.current / '.server.json').write_text('{}', encoding='utf-8')

    def action(self, path):
        return location.classify(self.current, str(path))['action']

    def snapshot(self, folder):
        return sorted((str(path.relative_to(folder)), path.read_bytes() if path.is_file() else None) for path in folder.rglob('*'))

    def test_a_library_is_opened_and_empty_or_missing_folders_receive_a_move(self):
        other = self.root / 'other'
        Library(other)
        self.assertEqual(self.action(other), 'open')
        empty = self.root / 'empty'
        empty.mkdir()
        self.assertEqual(self.action(empty), 'move')
        self.assertEqual(self.action(self.root / 'new' / 'deeper'), 'move')

    def test_move_counts_files_without_the_server_markers(self):
        answer = location.classify(self.current, str(self.root / 'new'))
        self.assertEqual(answer['files'], 3)  # database, one PDF, token
        self.assertEqual(answer['bytes'], sum(path.stat().st_size for path in self.current.rglob('*') if path.is_file() and not path.name.startswith('.server')))
        self.assertIn(f'Move the library to {self.root / "new"}?', answer['message'])
        self.assertIn('copies 3 files', answer['message'])

    def test_unsuitable_choices_are_refused_with_a_reason(self):
        full = self.root / 'full'
        full.mkdir()
        (full / 'notes.txt').write_text('mine', encoding='utf-8')
        file = self.root / 'file.txt'
        file.write_text('x', encoding='utf-8')
        for path, reason in [
            (full, 'not empty'), (file, 'is a file'), (self.current / 'originals', 'outside the current library'),
            (self.current / 'new', 'outside the current library'), (self.root, 'does not contain'), ('relative/folder', 'full path'),
            ('', 'full path'), (None, 'full path'),
        ]:
            answer = location.classify(self.current, str(path) if isinstance(path, Path) else path)
            self.assertEqual(answer['action'], 'refuse', path)
            self.assertIn(reason, answer['message'])

    def test_the_current_library_is_a_no_op_even_through_a_symlink_or_trailing_slash(self):
        self.assertEqual(self.action(self.current), 'current')
        self.assertEqual(self.action(f'{self.current}/'), 'current')
        link = self.root / 'link'
        try:
            link.symlink_to(self.current, target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        self.assertEqual(self.action(link), 'current')

    def test_move_copies_and_verifies_everything_except_the_markers(self):
        target = self.root / 'deep' / 'new'
        location.move(self.current, target)
        self.assertEqual({name for name, _ in self.snapshot(target)}, {'library.sqlite3', 'originals', 'originals/a.pdf', 'working', '.token'})
        self.assertEqual((target / 'originals' / 'a.pdf').read_bytes(), b'%PDF one')
        self.assertTrue((self.current / '.server.lock').exists())

    def test_move_into_an_existing_empty_folder_keeps_the_folder(self):
        target = self.root / 'empty'
        target.mkdir()
        location.move(self.current, target)
        self.assertTrue((target / 'library.sqlite3').is_file())

    def test_a_failed_check_removes_only_what_was_created_and_keeps_the_original(self):
        before = self.snapshot(self.current)
        def corrupting_copy(source, destination):
            copy2(source, destination)
            if Path(source).name == 'a.pdf':
                Path(destination).write_bytes(b'%PDF damaged')
        with patch.object(location.shutil, 'copy2', corrupting_copy):
            target = self.root / 'a' / 'b'
            with self.assertRaisesRegex(ValueError, 'does not match'):
                location.move(self.current, target)
            self.assertFalse((self.root / 'a').exists())
            empty = self.root / 'empty'
            empty.mkdir()
            with self.assertRaises(ValueError):
                location.move(self.current, empty)
            self.assertEqual(list(empty.iterdir()), [])
        self.assertEqual(self.snapshot(self.current), before)

    def test_a_write_error_is_reported_and_cleaned_up(self):
        with patch.object(location.shutil, 'copy2', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaisesRegex(ValueError, 'No space left on device. Nothing was changed'):
                location.move(self.current, self.root / 'full-disk')
        self.assertFalse((self.root / 'full-disk').exists())

    def test_a_change_during_the_copy_is_caught(self):
        def copy_then_change(source, destination):
            copy2(source, destination)
            if Path(source).name == 'a.pdf':
                Path(source).write_bytes(b'%PDF changed in the meantime')
        with patch.object(location.shutil, 'copy2', copy_then_change):
            with self.assertRaises(ValueError):
                location.move(self.current, self.root / 'busy')
        self.assertFalse((self.root / 'busy').exists())


if __name__ == '__main__':
    unittest.main()
