import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperassist.server import create_app
from tests.test_library import pdf


class ViewApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = patch('paperassist.paths.config_file', return_value=self.root / 'config.json')
        config.start()
        self.addCleanup(config.stop)
        self.app = create_app(self.root / 'library')
        self.addCleanup(self.app.extensions['paperassist_shutdown'])
        self.client = self.app.test_client()
        self.workspace = self.app.extensions['paperassist_workspace']
        self.doc = self.workspace.library.import_stream(io.BytesIO(pdf()), 'sample.pdf')
        self.endpoint = f"/api/documents/{self.doc['id']}/view"
        self.headers = {'X-PaperAssist-Token': self.client.get('/api/config').json['token']}

    def test_reading_state_round_trip_missing_only_migration_and_restart(self):
        self.assertIsNone(self.client.get(self.endpoint).json)
        view = {'page': 1, 'scale': '1.4'}
        self.assertEqual(self.client.put(self.endpoint, json=view, headers=self.headers).json, view)
        self.assertEqual(self.client.put(self.endpoint + '?if_missing=1', json={'page': 1, 'scale': .8}, headers=self.headers).json, view)
        restarted = create_app(self.root / 'library')
        self.addCleanup(restarted.extensions['paperassist_shutdown'])
        self.assertEqual(restarted.test_client().get(self.endpoint).json, view)
        self.assertEqual(self.workspace.library.get_document(self.doc['id'])['revision'], 0)

    def test_state_writes_require_token_and_invalid_values_fail_without_writing(self):
        for endpoint, value in ((self.endpoint, {'page': 1, 'scale': 1}), ('/api/preferences', {'ui_zoom': 1.25})):
            self.assertEqual(self.client.put(endpoint, json=value).status_code, 403)
            self.assertEqual(self.client.put(endpoint, json=[], headers=self.headers).status_code, 400)
        self.assertEqual(self.client.put(self.endpoint, json={'page': 99, 'scale': 1}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get('/api/documents/999/view').status_code, 404)
        self.assertEqual(self.client.put('/api/documents/999/view', json={'page': 1, 'scale': 1}, headers=self.headers).status_code, 404)
        self.assertIsNone(self.client.get(self.endpoint).json)
        self.assertFalse((self.root / 'config.json').exists())

    def test_partial_preference_migration_never_overwrites_another_clients_values(self):
        self.assertEqual(self.client.get('/api/preferences').json, {})
        self.client.put('/api/preferences', json={'ui_zoom': 1.25}, headers=self.headers)
        result = self.client.put('/api/preferences?if_missing=1', json={'ui_zoom': .8, 'details_expanded': False}, headers=self.headers)
        self.assertEqual(result.json, {'ui_zoom': 1.25, 'details_expanded': False})
        self.client.put('/api/preferences', json={'quick_view_layout': 'side'}, headers=self.headers)
        self.assertEqual(self.client.get('/api/preferences').json, {**result.json, 'quick_view_layout': 'side'})

    def test_existing_missing_job_and_reference_responses_stay_unchanged(self):
        from werkzeug.exceptions import NotFound
        for response in (
            self.client.get('/api/jobs/missing'),
            self.client.post('/api/jobs/missing/cancel', headers=self.headers),
            self.client.post(f"/api/documents/{self.doc['id']}/references/999/resolve", headers=self.headers),
        ):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json, {'error': NotFound.description})
