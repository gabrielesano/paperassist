import io
import json
import socket
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
import os
import unittest
from unittest.mock import patch

from paperassist import __version__, paths
from paperassist.library import Library
from paperassist.server import create_app, library_lock, running_url, serve


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = create_app(self.directory.name).test_client()
        self.token = self.client.get('/api/config').json['token']

    def tearDown(self):
        self.directory.cleanup()

    def test_writes_require_token_and_matching_origin(self):
        self.assertEqual(self.client.post('/api/import', json={'paths': []}).status_code, 403)
        headers = {'X-PaperAssist-Token': self.token, 'Origin': 'https://evil.example'}
        self.assertEqual(self.client.post('/api/import', json={'paths': []}, headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/api/config', headers={'Host': 'evil.example'}).status_code, 403)

    def test_config_reports_the_app_version(self):
        self.assertEqual(self.client.get('/api/config').json['version'], __version__)

    def test_token_survives_restart_and_stays_private(self):
        again = create_app(self.directory.name).test_client()
        self.assertEqual(again.get('/api/config').json['token'], self.token)
        if os.name != 'nt':  # Windows has no POSIX permission bits
            mode = (Path(self.directory.name) / '.token').stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.assertNotEqual(create_app(other.name).test_client().get('/api/config').json['token'], self.token)

    def test_job_can_be_polled(self):
        response = self.client.post('/api/import', json={'paths': []}, headers={'X-PaperAssist-Token': self.token})
        self.assertEqual(response.status_code, 202)
        for _ in range(100):
            job = self.client.get('/api/jobs/' + response.json['id']).json
            if job['status'] in ('completed', 'failed'):
                break
            time.sleep(.01)
        self.assertEqual(job['status'], 'completed')

    def test_rejects_invalid_payloads_and_unknown_reference(self):
        from io import BytesIO
        from pypdf import PdfWriter
        from paperassist.library import Library
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        data = BytesIO()
        writer.write(data)
        data.seek(0)
        document = Library(self.directory.name).import_stream(data, 'test.pdf')
        endpoint = f"/api/documents/{document['id']}/references/download"
        headers = {'X-PaperAssist-Token': self.token}
        for payload in ([], {}, {'ids': '1'}, {'ids': []}, {'ids': [True]}, {'ids': ['999']}):
            self.assertEqual(self.client.post(endpoint, json=payload, headers=headers).status_code, 400)
        self.assertEqual(self.client.post('/api/import', json={'paths': [1]}, headers=headers).status_code, 400)

    def test_label_endpoint_stores_a_nickname_behind_the_token(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        data = io.BytesIO()
        writer.write(data)
        data.seek(0)
        document = Library(self.directory.name).import_stream(data, 'test.pdf')
        endpoint = f"/api/documents/{document['id']}/label"
        headers = {'X-PaperAssist-Token': self.token}
        self.assertEqual(self.client.put(endpoint, json={'label': 'x'}).status_code, 403)
        for payload in ([], {}, {'label': None}, {'label': 3}, {'label': 'x' * 81}):
            self.assertEqual(self.client.put(endpoint, json=payload, headers=headers).status_code, 400)
        self.assertEqual(self.client.put('/api/documents/999/label', json={'label': 'x'}, headers=headers).status_code, 404)
        answer = self.client.put(endpoint, json={'label': ' Reale 2016 '}, headers=headers).json
        self.assertEqual((answer['label'], answer['title'], answer['metadata_status']),
                         ('Reale 2016', document['title'], document['metadata_status']))
        self.assertEqual(self.client.get('/api/documents').json[0]['label'], 'Reale 2016')
        self.assertEqual(self.client.put(endpoint, json={'label': ''}, headers=headers).json['label'], '')

    def test_reference_link_reset_requires_explicit_null(self):
        from unittest.mock import patch
        endpoint = '/api/documents/1/references/2'
        headers = {'X-PaperAssist-Token': self.token}
        with patch('paperassist.server.references.link', return_value={'id': '2'}) as link:
            self.assertEqual(self.client.patch(endpoint, json={}, headers=headers).status_code, 400)
            self.assertEqual(self.client.patch(endpoint, json={'local_document_id': True}, headers=headers).status_code, 400)
            self.assertEqual(self.client.patch(endpoint, json={'local_document_id': None}, headers=headers).status_code, 200)
            self.assertIsNone(link.call_args.args[-1])

    def test_repair_downloads_runs_as_a_job(self):
        from unittest.mock import patch
        from paperassist.library import Library
        from tests.test_library import pdf
        from io import BytesIO
        document = Library(self.directory.name).import_stream(BytesIO(pdf()), 'test.pdf')
        endpoint = f"/api/documents/{document['id']}/references/repair-downloads"
        headers = {'X-PaperAssist-Token': self.token}
        self.assertEqual(self.client.post(endpoint).status_code, 403)
        with patch('paperassist.server.references.repair_downloads', return_value=[{'status': 'repaired'}]):
            job = self.client.post(endpoint, headers=headers).json
            for _ in range(100):
                job = self.client.get('/api/jobs/' + job['id']).json
                if job['status'] in ('completed', 'failed'):
                    break
                time.sleep(.01)
        self.assertEqual(job['result'], {'documents': [{'status': 'repaired'}]})

    def test_download_job_reports_and_stores_one_outcome_per_reference(self):
        from unittest.mock import patch
        from paperassist.library import Library
        from tests.test_library import pdf
        from io import BytesIO
        document = Library(self.directory.name).import_stream(BytesIO(pdf()), 'test.pdf')
        endpoint = f"/api/documents/{document['id']}/references/download"
        found = {'version': 2, 'status': 'not_found', 'summary': 'No open-access PDF on arXiv.', 'attempts': []}
        results = [found, RuntimeError('boom')]
        with patch('paperassist.server.references.get', return_value={'references': [{'id': '1'}, {'id': '2'}]}), \
             patch('paperassist.server.retrieval.download', side_effect=results), \
             patch('paperassist.server.references.set_retrieval') as store:
            job = self.client.post(endpoint, json={'ids': ['1', '2']}, headers={'X-PaperAssist-Token': self.token}).json
            for _ in range(200):
                job = self.client.get('/api/jobs/' + job['id']).json
                if job['status'] in ('completed', 'failed'):
                    break
                time.sleep(.01)
        first, second = job['result']['references']
        self.assertEqual((first['status'], first['summary']), ('not_found', 'No open-access PDF on arXiv.'))
        self.assertEqual((second['version'], second['status']), (2, 'failed'))
        self.assertIn('boom', second['summary'])
        self.assertEqual([call.args[3]['status'] for call in store.call_args_list], ['not_found', 'failed'])

    def test_library_lock_blocks_independent_import(self):
        from paperassist.server import library_lock
        from paperassist.cli import main
        with library_lock(self.directory.name):
            from contextlib import redirect_stderr
            from io import StringIO
            message = StringIO()
            with redirect_stderr(message):
                self.assertEqual(main(['import', 'missing.pdf', '--data-dir', self.directory.name]), 1)
            self.assertIn('Library is in use', message.getvalue())


class LibraryLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        # The real per-user config must never be read or written, on any platform.
        patcher = patch('paperassist.paths.config_file', return_value=self.root / 'config' / 'config.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ)  # restores the variable popped below
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('PAPERASSIST_DATA_DIR', None)
        self.current = self.root / 'current'
        Library(self.current)
        (self.current / 'originals').mkdir(exist_ok=True)
        (self.current / 'originals' / 'a.pdf').write_bytes(b'%PDF one')
        self.switched = []
        self.client = create_app(self.current, 'default', self.switched.append).test_client()
        self.headers = {'X-PaperAssist-Token': self.client.get('/api/config').json['token']}

    def post(self, route, **values):
        response = self.client.post(f'/api/library/{route}', json=values, headers=self.headers)
        response.close()  # the switch runs once the response is closed, as it is after sending
        return response

    def test_config_reports_how_the_location_was_set(self):
        self.assertEqual(self.client.get('/api/config').json['library_source'], 'default')
        fixed = create_app(self.current, 'option').test_client()
        self.assertEqual(fixed.get('/api/config').json['library_source'], 'option')

    def test_routes_require_the_token(self):
        for route in ('inspect', 'switch'):
            response = self.client.post(f'/api/library/{route}', json={'path': str(self.root / 'x'), 'action': 'move'})
            self.assertEqual(response.status_code, 403)

    def test_inspect_says_what_would_happen(self):
        answer = self.post('inspect', path=str(self.root / 'new')).json
        self.assertEqual((answer['action'], answer['files']), ('move', 3))  # database, token, one PDF
        self.assertEqual(self.post('inspect', path=str(self.current)).json['action'], 'current')
        self.assertEqual(self.post('inspect', path=str(self.current / 'inside')).json['action'], 'refuse')

    def test_moving_copies_the_library_saves_the_choice_and_then_switches(self):
        target = self.root / 'moved'
        response = self.post('switch', path=str(target), action='move')
        self.assertEqual(response.json['path'], str(target))
        self.assertEqual((target / 'originals' / 'a.pdf').read_bytes(), b'%PDF one')
        self.assertTrue((self.current / 'originals' / 'a.pdf').exists())
        self.assertEqual(paths.saved_library(), target)
        self.assertEqual(self.switched, [target])

    def test_opening_a_library_leaves_the_current_one_alone(self):
        other = self.root / 'other'
        Library(other)
        self.assertEqual(self.post('switch', path=str(other), action='open').status_code, 200)
        self.assertEqual(paths.saved_library(), other)
        self.assertEqual(self.switched, [other])

    def test_refused_and_stale_requests_change_nothing(self):
        other = self.root / 'other'
        Library(other)
        for values in ({'path': str(self.current), 'action': 'open'}, {'path': str(self.current / 'in'), 'action': 'move'},
                       {'path': str(other), 'action': 'move'}, {'path': str(self.root / 'new'), 'action': 'open'}):
            self.assertEqual(self.post('switch', **values).status_code, 409, values)
        self.assertFalse(paths.config_file().exists())
        self.assertFalse((self.root / 'new').exists())
        self.assertEqual(self.switched, [])

    def test_a_location_chosen_at_start_up_cannot_change(self):
        for source in ('option', 'environment'):
            client = create_app(self.current, source, self.switched.append).test_client()
            response = client.post('/api/library/switch', json={'path': str(self.root / 'x'), 'action': 'move'}, headers=self.headers)
            self.assertEqual((response.status_code, response.json['error']), (409, paths.FIXED[source]))
        self.assertFalse((self.root / 'x').exists())

    def test_running_tasks_block_the_switch(self):
        release = threading.Event()
        with patch.object(Library, 'import_paths', lambda *args, **kwargs: release.wait(5)):
            self.client.post('/api/import', json={'paths': ['x']}, headers=self.headers)
            response = self.post('switch', path=str(self.root / 'new'), action='move')
            release.set()
        self.assertEqual((response.status_code, response.json['error']), (409, 'Wait for running tasks to finish.'))
        self.assertFalse((self.root / 'new').exists())

    def test_a_library_open_elsewhere_cannot_be_switched_to(self):
        other = self.root / 'other'
        Library(other)
        with library_lock(other):
            response = self.post('switch', path=str(other), action='open')
        self.assertEqual(response.status_code, 409)
        self.assertIn('another PaperAssist', response.json['error'])
        self.assertFalse(paths.config_file().exists())

    def test_the_folder_chooser_can_be_titled(self):
        with patch('paperassist.server.picker.choose_folder', return_value='/chosen') as chooser:
            self.client.post('/api/choose-folder', json={'start': '/here', 'title': 'Library folder'}, headers=self.headers)
            self.client.post('/api/choose-folder', json={}, headers=self.headers)
        self.assertEqual([call.args for call in chooser.call_args_list], [('/here',), (None,)])
        self.assertEqual(chooser.call_args_list[0].kwargs, {'title': 'Library folder'})
        self.assertEqual(chooser.call_args_list[1].kwargs, {})


class ServeSwitchTests(unittest.TestCase):
    def test_the_running_server_moves_to_the_chosen_library_on_the_same_port(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        first, second = root / 'first', root / 'second'
        Library(second)
        patcher = patch('paperassist.paths.config_file', return_value=root / 'config' / 'config.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        servers = []
        from werkzeug.serving import make_server
        def recording(*args, **kwargs):
            servers.append(make_server(*args, **kwargs))
            return servers[-1]
        def call(url, route, body=None, token=''):
            request = urllib.request.Request(url + route, data=body and json.dumps(body).encode(), method='POST' if body else 'GET',
                                             headers={'Content-Type': 'application/json', 'X-PaperAssist-Token': token})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)
        with patch('paperassist.server.make_server', recording), patch('sys.stdout', new=io.StringIO()):
            with socket.socket() as probe:  # a fixed port, because the new library must reuse it
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            thread = threading.Thread(target=serve, args=('127.0.0.1', port, first, False, 'default'))
            thread.start()
            try:
                while not (first / '.server.json').exists():
                    time.sleep(.01)
                url = running_url(first)
                old = call(url, '/api/config')
                self.assertEqual((old['library_path'], old['library_source']), (str(first), 'default'))
                call(url, '/api/library/switch', {'path': str(second), 'action': 'open'}, old['token'])
                for _ in range(500):
                    try:
                        new = call(url, '/api/config')
                        if new['library_path'] == str(second):
                            break
                    except OSError:
                        pass
                    time.sleep(.01)
                self.assertEqual((new['library_path'], new['library_source']), (str(second), 'settings'))
                self.assertNotEqual(new['token'], old['token'])
                self.assertEqual(running_url(second), url)
                self.assertFalse((first / '.server.json').exists())
                with library_lock(first):  # the old library was released
                    pass
                self.assertEqual(paths.saved_library(), second)
            finally:
                servers[-1].shutdown()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse((second / '.server.json').exists())

    def test_a_library_that_cannot_start_sends_the_server_back_to_the_previous_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            good, broken = root / 'good', root / 'broken'
            with patch('paperassist.paths.config_file', return_value=root / 'config.json'), \
                 patch('paperassist.server._serve_library', side_effect=[broken, RuntimeError('damaged'), None]) as run, \
                 patch('sys.stderr', new=io.StringIO()) as errors:
                serve('127.0.0.1', 0, good, False)
                self.assertEqual([call.args[2] for call in run.call_args_list], [good, broken, good])
                self.assertEqual(paths.saved_library(), good)
            self.assertIn(f'Could not open {broken}: damaged', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
