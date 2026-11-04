import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperassist import settings
from paperassist.server import create_app

SECRET = 'abcd-secret-token-wxyz'


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name) / 'data'
        self.client = create_app(self.data).test_client()
        self.headers = {'X-PaperAssist-Token': self.client.get('/api/config').json['token']}
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def put(self, body):
        return self.client.put('/api/settings', json=body, headers=self.headers)

    def test_defaults_enable_every_source_and_hide_keys(self):
        view = self.client.get('/api/settings').json
        self.assertEqual(view['enabled'], dict.fromkeys(settings.SOURCES, True))
        self.assertEqual(view['keys']['ads'], {'configured': False, 'hint': ''})
        self.assertEqual((view['email'], view['proxy'], view['inbox']), ('', '', ''))

    def test_round_trip_masks_keys_and_stores_the_file_privately(self):
        response = self.put({'email': 'reader@example.org', 'proxy': 'https://login.proxy.example.edu/login?url=',
                             'keys': {'ads': SECRET}, 'enabled': {'core': False}})
        self.assertEqual(response.status_code, 200)
        for view in (response.json, self.client.get('/api/settings').json):
            self.assertEqual(view['keys']['ads'], {'configured': True, 'hint': 'wxyz'})
            self.assertEqual((view['email'], view['enabled']['core'], view['enabled']['ads']), ('reader@example.org', False, True))
            self.assertNotIn(SECRET, json.dumps(view))
        file = self.data / 'settings.json'
        if os.name != 'nt':  # Windows has no POSIX permission bits
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(file.read_text())['keys']['ads'], SECRET)
        self.assertNotIn(SECRET, self.client.get('/api/config').get_data(as_text=True))

    def test_omitted_fields_stay_and_null_or_empty_clears(self):
        self.put({'email': 'reader@example.org', 'keys': {'ads': SECRET, 'core': SECRET}})
        view = self.put({'keys': {'core': None}}).json
        self.assertEqual((view['email'], view['keys']['ads']['configured'], view['keys']['core']['configured']), ('reader@example.org', True, False))
        view = self.put({'email': ''}).json
        self.assertEqual((view['email'], view['keys']['ads']['configured']), ('', True))

    def test_environment_is_only_a_fallback(self):
        with patch.dict(os.environ, {'PAPERASSIST_EMAIL': 'env@example.org', 'OPENALEX_API_KEY': 'environment-key'}):
            self.assertEqual(settings.load(self.data).email, 'env@example.org')
            view = self.client.get('/api/settings').json
            self.assertEqual(view['keys']['openalex'], {'configured': True, 'hint': '-key'})
            self.assertTrue(self.client.get('/api/config').json['providers']['unpaywall']['configured'])
            self.put({'email': 'stored@example.org', 'keys': {'openalex': 'stored-openalex-key'}})
            self.assertEqual(settings.load(self.data).email, 'stored@example.org')
            self.assertEqual(settings.load(self.data).key('openalex'), 'stored-openalex-key')

    def test_invalid_input_is_a_clear_400_that_never_echoes_the_key(self):
        bad = [{'email': 'not-an-address'}, {'proxy': 'http://proxy.example.edu/?url='}, {'proxy': 'ftp://x.example/{url}'},
               {'proxy': 'https://'}, {'keys': {'ads': 'short'}}, {'keys': {'ads': 'has a space in it'}}, {'keys': {'ads': 'x' * 300}},
               {'keys': {'nope': SECRET}}, {'keys': {'ads': 12345678}}, {'enabled': {'nope': True}}, {'enabled': {'ads': 'yes'}},
               {'surprise': 1}]
        for body in bad:
            response = self.put(body)
            self.assertEqual(response.status_code, 400, body)
            self.assertTrue(response.json['error'])
            self.assertNotIn(SECRET, response.get_data(as_text=True))
        self.assertEqual(self.client.put('/api/settings', json=[], headers=self.headers).status_code, 400)
        self.assertFalse((self.data / 'settings.json').exists())

    def test_proxy_accepts_both_template_styles(self):
        for proxy in ('https://login.proxy.example.edu/login?url=', 'https://proxy.example.edu/go/{url}'):
            self.assertEqual(self.put({'proxy': proxy}).json['proxy'], proxy)
            self.assertEqual(self.client.get('/api/config').json['proxy'], proxy)

    def test_writes_need_the_token(self):
        self.assertEqual(self.client.put('/api/settings', json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/settings/check').status_code, 403)

    def test_config_providers_describe_what_each_source_needs(self):
        providers = self.client.get('/api/config').json['providers']
        self.assertEqual(set(providers), set(settings.SOURCES))
        self.assertEqual(providers['ads'], {'configured': False, 'enabled': True, 'needs': 'token'})
        self.assertEqual(providers['core'], {'configured': False, 'enabled': True, 'needs': 'key'})
        self.assertEqual(providers['unpaywall'], {'configured': False, 'enabled': True, 'needs': 'email'})
        self.assertEqual(providers['arxiv'], {'configured': True, 'enabled': True, 'needs': None})
        self.put({'keys': {'ads': SECRET}, 'enabled': {'ads': False}})
        self.assertEqual(self.client.get('/api/config').json['providers']['ads'], {'configured': True, 'enabled': False, 'needs': 'token'})

    def test_check_uses_stored_settings(self):
        self.put({'keys': {'ads': SECRET}})
        with patch('paperassist.server.retrieval.check', return_value={'ads': {'ok': True, 'message': 'Accepted.'}}) as check:
            response = self.client.post('/api/settings/check', headers=self.headers)
        self.assertEqual(response.json, {'ads': {'ok': True, 'message': 'Accepted.'}})
        self.assertEqual(check.call_args.args[0].key('ads'), SECRET)

    def test_corrupt_file_falls_back_to_defaults(self):
        (self.data / 'settings.json').write_text('{broken')
        self.assertEqual(self.client.get('/api/settings').json['email'], '')

    def test_inbox_must_be_an_existing_folder_outside_the_library(self):
        folder = Path(self.directory.name) / 'downloads'
        folder.mkdir()
        (self.data / 'inside').mkdir()
        for value in (str(folder / 'missing'), str(folder / '..' / 'nowhere'), 'relative/path', str(self.data), str(self.data / 'inside')):
            self.assertEqual(self.put({'inbox': value}).status_code, 400, value)
        (folder / 'file.pdf').write_bytes(b'x')
        self.assertEqual(self.put({'inbox': str(folder / 'file.pdf')}).status_code, 400)
        self.assertEqual(self.put({'inbox': str(folder)}).json['inbox'], str(folder.resolve()))
        self.assertEqual(self.client.get('/api/config').json['inbox'], str(folder.resolve()))
        # Windows resolves "~" from USERPROFILE, POSIX systems from HOME.
        with patch.dict(os.environ, {'HOME': str(folder), 'USERPROFILE': str(folder)}):
            (folder / 'sub').mkdir()
            self.assertEqual(self.put({'inbox': '~/sub'}).json['inbox'], str((folder / 'sub').resolve()))
        self.assertEqual(self.put({'inbox': None}).json['inbox'], '')
