import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from paperassist import picker
from paperassist.server import create_app


def finished(code=0, out='', err=''):
    return subprocess.CompletedProcess([], code, out, err)


def choose(platform, result, tools=(), start=None):
    """Run choose_folder with a fake platform, installed tools and process result."""
    with mock.patch('sys.platform', platform), \
            mock.patch('shutil.which', lambda name: f'/usr/bin/{name}' if name in tools else None), \
            mock.patch('subprocess.run', return_value=result) as run:
        try:
            return picker.choose_folder(start), run
        except picker.PickerUnavailable as error:
            return error, run


class PickerTests(unittest.TestCase):
    def test_linux_prefers_zenity_and_starts_in_given_folder(self):
        with tempfile.TemporaryDirectory() as start:
            path, run = choose('linux', finished(out=f'{start}/papers\n'), ['zenity', 'kdialog'], start)
        command = run.call_args.args[0]
        self.assertEqual(path, f'{start}/papers')
        self.assertEqual(command[0], 'zenity')
        self.assertIn(f'--filename={start}/', command)
        self.assertFalse(run.call_args.kwargs.get('shell'))

    def test_missing_or_invalid_start_falls_back_to_home(self):
        _, run = choose('linux', finished(out='/x\n'), ['zenity'], '/no/such/folder')
        self.assertIn(f'--filename={Path.home()}/', run.call_args.args[0])

    def test_kdialog_is_used_when_zenity_is_missing(self):
        path, run = choose('linux', finished(out='/data\n'), ['kdialog'])
        self.assertEqual((path, run.call_args.args[0][:2]), ('/data', ['kdialog', '--getexistingdirectory']))

    def test_tk_is_the_last_resort_and_runs_in_a_subprocess(self):
        path, run = choose('linux', finished(out='/data\n'))
        self.assertEqual(path, '/data')
        self.assertEqual(run.call_args.args[0][:2], [sys.executable, '-c'])
        self.assertIn('-topmost', run.call_args.args[0][2])

    def test_windows_uses_tk_even_if_zenity_exists(self):
        _, run = choose('win32', finished(out='C:/Papers\n'), ['zenity'])
        self.assertEqual(run.call_args.args[0][0], sys.executable)

    def test_macos_uses_osascript_and_strips_trailing_slash(self):
        path, run = choose('darwin', finished(out='/Users/me/Papers/\n'))
        command = run.call_args.args[0]
        self.assertEqual((path, command[:2]), ('/Users/me/Papers', ['osascript', '-e']))
        self.assertIn('choose folder', command[2])

    def test_macos_escapes_quotes_in_the_start_folder(self):
        with mock.patch('pathlib.Path.is_dir', return_value=True):  # quotes are not valid in Windows names
            _, run = choose('darwin', finished(out='/x/\n'), start='/a"b')
        self.assertIn('a\\"b', run.call_args.args[0][2])

    def test_cancel_returns_none(self):
        self.assertIsNone(choose('linux', finished(1), ['zenity'])[0])
        self.assertIsNone(choose('linux', finished(1), ['kdialog'])[0])
        self.assertIsNone(choose('linux', finished(0, '\n'))[0])
        self.assertIsNone(choose('darwin', finished(1, err='execution error: User canceled. (-128)'))[0])

    def test_failures_raise_with_a_short_reason(self):
        for platform, tools, result in (
            ('linux', ['zenity'], finished(5, err='Gtk-WARNING: no display\nlast line')),
            ('linux', [], finished(1, err='ModuleNotFoundError: No module named tkinter')),
            ('darwin', [], finished(1, err='some other error')),
        ):
            error = choose(platform, result, tools)[0]
            self.assertIsInstance(error, picker.PickerUnavailable, platform)
        self.assertIn('last line', str(choose('linux', finished(5, err='a\nlast line'), ['zenity'])[0]))

    def test_unlaunchable_tool_raises(self):
        with mock.patch('sys.platform', 'linux'), mock.patch('shutil.which', return_value='/usr/bin/zenity'), \
                mock.patch('subprocess.run', side_effect=FileNotFoundError('zenity')):
            with self.assertRaises(picker.PickerUnavailable):
                picker.choose_folder()


class ChooseFolderEndpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = create_app(self.directory.name).test_client()
        self.headers = {'X-PaperAssist-Token': self.client.get('/api/config').json['token']}

    def post(self, **kwargs):
        return self.client.post('/api/choose-folder', json={'start': '/tmp'}, headers=kwargs.get('headers', self.headers))

    def test_requires_token_and_matching_origin(self):
        with mock.patch('paperassist.picker.choose_folder') as chooser:
            self.assertEqual(self.post(headers={}).status_code, 403)
            self.assertEqual(self.post(headers={**self.headers, 'Origin': 'https://evil.example'}).status_code, 403)
        chooser.assert_not_called()

    def test_returns_the_path_or_null_and_passes_the_start(self):
        with mock.patch('paperassist.picker.choose_folder', return_value='/data/papers') as chooser:
            response = self.post()
        self.assertEqual((response.status_code, response.json), (200, {'path': '/data/papers'}))
        chooser.assert_called_once_with('/tmp')
        with mock.patch('paperassist.picker.choose_folder', return_value=None):
            self.assertEqual(self.post().json, {'path': None})

    def test_rejects_a_non_text_start(self):
        response = self.client.post('/api/choose-folder', json={'start': 3}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_unavailable_picker_is_503(self):
        with mock.patch('paperassist.picker.choose_folder', side_effect=picker.PickerUnavailable('No window')):
            response = self.post()
        self.assertEqual((response.status_code, response.json), (503, {'error': 'No window'}))

    def test_second_request_while_open_is_409(self):
        opened, release = threading.Event(), threading.Event()
        def slow(start):
            opened.set()
            release.wait(5)
            return '/data'
        with mock.patch('paperassist.picker.choose_folder', side_effect=slow):
            first = threading.Thread(target=self.post)
            first.start()
            self.assertTrue(opened.wait(5))
            second = self.post()
            release.set()
            first.join()
        self.assertEqual((second.status_code, second.json), (409, {'error': 'A folder window is already open.'}))
        with mock.patch('paperassist.picker.choose_folder', return_value='/data'):
            self.assertEqual(self.post().status_code, 200)


if __name__ == '__main__':
    unittest.main()
