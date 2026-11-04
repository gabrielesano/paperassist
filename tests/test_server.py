import tempfile
import time
from pathlib import Path
import os
import unittest

from paperassist.server import create_app


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
