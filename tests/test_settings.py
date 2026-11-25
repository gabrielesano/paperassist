import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperassist import settings
from paperassist.workspace import Workspace

SECRET = 'abcd-secret-token-wxyz'


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name) / 'data'
        self.workspace = Workspace(self.data)
        self.addCleanup(self.workspace.close)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def view(self):
        return self.workspace.get_settings().view()

    def put(self, body):
        return self.workspace.update_settings(body).view()

    def test_defaults_enable_every_source_and_hide_keys(self):
        view = self.view()
        self.assertEqual(view['enabled'], dict.fromkeys(settings.SOURCES, True))
        self.assertEqual(view['keys']['ads'], {'configured': False, 'hint': ''})
        self.assertEqual((view['email'], view['proxy'], view['inbox']), ('', '', ''))

    def test_round_trip_masks_keys_and_stores_the_file_privately(self):
        response = self.put({'email': 'reader@example.org', 'proxy': 'https://login.proxy.example.edu/login?url=',
                             'keys': {'ads': SECRET}, 'enabled': {'core': False}})
        for view in (response, self.view()):
            self.assertEqual(view['keys']['ads'], {'configured': True, 'hint': 'wxyz'})
            self.assertEqual((view['email'], view['enabled']['core'], view['enabled']['ads']), ('reader@example.org', False, True))
            self.assertNotIn(SECRET, json.dumps(view))
        file = self.data / 'settings.json'
        if os.name != 'nt':  # Windows has no POSIX permission bits
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(file.read_text())['keys']['ads'], SECRET)
        self.assertNotIn(SECRET, json.dumps(self.workspace.get_settings().providers()))

    def test_omitted_fields_stay_and_null_or_empty_clears(self):
        self.put({'email': 'reader@example.org', 'keys': {'ads': SECRET, 'core': SECRET}})
        view = self.put({'keys': {'core': None}})
        self.assertEqual((view['email'], view['keys']['ads']['configured'], view['keys']['core']['configured']), ('reader@example.org', True, False))
        view = self.put({'email': ''})
        self.assertEqual((view['email'], view['keys']['ads']['configured']), ('', True))

    def test_environment_is_only_a_fallback(self):
        with patch.dict(os.environ, {'PAPERASSIST_EMAIL': 'env@example.org', 'OPENALEX_API_KEY': 'environment-key'}):
            self.assertEqual(settings.load(self.data).email, 'env@example.org')
            self.assertEqual(self.view()['keys']['openalex'], {'configured': True, 'hint': '-key'})
            self.assertTrue(self.workspace.get_settings().providers()['unpaywall']['configured'])
            self.put({'email': 'stored@example.org', 'keys': {'openalex': 'stored-openalex-key'}})
            self.assertEqual(settings.load(self.data).email, 'stored@example.org')
            self.assertEqual(settings.load(self.data).key('openalex'), 'stored-openalex-key')

    def test_invalid_input_never_echoes_the_key_or_writes_settings(self):
        bad = [{'email': 'not-an-address'}, {'proxy': 'http://proxy.example.edu/?url='}, {'proxy': 'ftp://x.example/{url}'},
               {'proxy': 'https://'}, {'keys': {'ads': 'short'}}, {'keys': {'ads': 'has a space in it'}}, {'keys': {'ads': 'x' * 300}},
               {'keys': {'nope': SECRET}}, {'keys': {'ads': 12345678}}, {'enabled': {'nope': True}}, {'enabled': {'ads': 'yes'}},
               {'surprise': 1}, *({name: value} for name in ('enabled', 'keys')
                                    for value in ('bad', [1], 7, False))]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError) as raised:
                self.put(body)
            self.assertTrue(str(raised.exception))
            self.assertNotIn(SECRET, str(raised.exception))
        self.assertFalse((self.data / 'settings.json').exists())

    def test_proxy_accepts_both_template_styles(self):
        for proxy in ('https://login.proxy.example.edu/login?url=', 'https://proxy.example.edu/go/{url}'):
            self.assertEqual(self.put({'proxy': proxy})['proxy'], proxy)
            self.assertEqual(self.workspace.get_settings().proxy, proxy)

    def test_providers_describe_what_each_source_needs(self):
        providers = self.workspace.get_settings().providers()
        self.assertEqual(set(providers), set(settings.SOURCES))
        self.assertEqual(providers['ads'], {'configured': False, 'enabled': True, 'needs': 'token'})
        self.assertEqual(providers['core'], {'configured': False, 'enabled': True, 'needs': 'key'})
        self.assertEqual(providers['unpaywall'], {'configured': False, 'enabled': True, 'needs': 'email'})
        self.assertEqual(providers['arxiv'], {'configured': True, 'enabled': True, 'needs': None})
        self.put({'keys': {'ads': SECRET}, 'enabled': {'ads': False}})
        self.assertEqual(self.workspace.get_settings().providers()['ads'], {'configured': True, 'enabled': False, 'needs': 'token'})

    def test_check_uses_stored_settings(self):
        self.put({'keys': {'ads': SECRET}})
        with patch('paperassist.workspace.retrieval.check', return_value={'ads': {'ok': True, 'message': 'Accepted.'}}) as check:
            response = self.workspace.check_settings()
        self.assertEqual(response, {'ads': {'ok': True, 'message': 'Accepted.'}})
        self.assertEqual(check.call_args.args[0].key('ads'), SECRET)

    def test_corrupt_file_falls_back_to_defaults(self):
        (self.data / 'settings.json').write_text('{broken')
        self.assertEqual(self.view()['email'], '')

    def test_inbox_must_be_an_existing_folder_outside_the_library(self):
        folder = Path(self.directory.name) / 'downloads'
        folder.mkdir()
        (self.data / 'inside').mkdir()
        for value in (str(folder / 'missing'), str(folder / '..' / 'nowhere'), 'relative/path', str(self.data), str(self.data / 'inside')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.put({'inbox': value})
        (folder / 'file.pdf').write_bytes(b'x')
        with self.assertRaises(ValueError):
            self.put({'inbox': str(folder / 'file.pdf')})
        self.assertEqual(self.put({'inbox': str(folder)})['inbox'], str(folder.resolve()))
        self.assertEqual(self.workspace.get_settings().inbox, str(folder.resolve()))
        # Windows resolves "~" from USERPROFILE, POSIX systems from HOME.
        with patch.dict(os.environ, {'HOME': str(folder), 'USERPROFILE': str(folder)}):
            (folder / 'sub').mkdir()
            self.assertEqual(self.put({'inbox': '~/sub'})['inbox'], str((folder / 'sub').resolve()))
        self.assertEqual(self.put({'inbox': None})['inbox'], '')

    def test_shared_constants_and_atomic_json_helpers(self):
        from paperassist import paths
        self.assertEqual(settings.SOURCES, tuple(settings.PROVIDER_NAMES))
        self.assertEqual(settings.ZOOM_CHOICES, (0.8, 0.9, 1, 1.1, 1.25, 1.5))
        self.assertEqual(paths.format_size(512), '512 bytes')
        self.assertEqual(paths.format_size(2048), '2.0 KB')
        target = self.data / 'nested' / 'sample.json'
        paths.write_json_atomic(target, {'ok': True}, mode=0o600)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), {'ok': True})
        if os.name != 'nt':
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(TypeError):
            paths.write_json_atomic(target, {'bad': object()})
        # Failed atomic writes clean up temp files and keep the previous target intact.
        self.assertEqual(list(target.parent.iterdir()), [target])
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), {'ok': True})

    def test_dpapi_round_trip_legacy_migration_and_corrupt_blob_resilience(self):
        def fake_protect(raw: bytes) -> bytes:
            return b'ENC:' + raw[::-1]

        def fake_unprotect(blob: bytes) -> bytes:
            if not blob.startswith(b'ENC:'):
                raise OSError('bad blob')
            return blob[4:][::-1]

        with patch('paperassist.settings._use_dpapi', return_value=True), \
             patch('paperassist.settings._dpapi_protect', side_effect=fake_protect), \
             patch('paperassist.settings._dpapi_unprotect', side_effect=fake_unprotect):
            view = self.put({'keys': {'ads': SECRET}})
            self.assertEqual(view['keys']['ads'], {'configured': True, 'hint': 'wxyz'})
            raw_stored = json.loads((self.data / 'settings.json').read_text(encoding='utf-8'))
            self.assertTrue(raw_stored['keys']['ads'].startswith('dpapi:'))
            self.assertNotIn(SECRET, raw_stored['keys']['ads'])
            self.assertEqual(self.workspace.get_settings().key('ads'), SECRET)

            # Legacy plaintext key loads cleanly and upgrades to dpapi: on the next update().
            legacy = {'email': 'old@example.org', 'keys': {'core': 'legacy-core-key'}}
            (self.data / 'settings.json').write_text(json.dumps(legacy), encoding='utf-8')
            self.assertEqual(self.workspace.get_settings().key('core'), 'legacy-core-key')
            self.put({'email': 'new@example.org'})
            upgraded = json.loads((self.data / 'settings.json').read_text(encoding='utf-8'))
            self.assertTrue(upgraded['keys']['core'].startswith('dpapi:'))
            self.assertEqual(self.workspace.get_settings().key('core'), 'legacy-core-key')

            # Corrupt dpapi blob never crashes load(); falls back to empty key.
            (self.data / 'settings.json').write_text(json.dumps({'keys': {'ads': 'dpapi:not-valid-b64!'}}), encoding='utf-8')
            loaded = self.workspace.get_settings()
            self.assertEqual(loaded.key('ads'), '')
            self.assertFalse(loaded.view()['keys']['ads']['configured'])

    def test_windows_acl_applies_owner_only_protected_dacl_for_mode_600(self):
        from unittest.mock import MagicMock
        from paperassist import paths

        def fake_get_token_info(_token, _cls, buf, _size, needed_ptr):
            if buf is None:
                needed_ptr._obj.value = 16
                return False
            return True

        kernel32, advapi32 = MagicMock(), MagicMock()
        kernel32.GetCurrentProcess.return_value = 123
        advapi32.OpenProcessToken.side_effect = lambda proc, access, ptr: True
        advapi32.GetTokenInformation.side_effect = fake_get_token_info
        advapi32.ConvertSidToStringSidW.return_value = True
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.return_value = True
        advapi32.SetFileSecurityW.return_value = True

        target = self.data / 'win-settings.json'
        with patch('paperassist.paths._is_windows', return_value=True), \
             patch('paperassist.paths._win32_libs', return_value=(kernel32, advapi32)), \
             patch('paperassist.paths.ctypes.wstring_at', return_value='S-1-5-21-1000'):
            paths.write_json_atomic(target, {'keys': {}}, mode=0o600)
        sddl = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.call_args.args[0]
        self.assertEqual(sddl, 'D:P(A;;FA;;;S-1-5-21-1000)')
        flags = advapi32.SetFileSecurityW.call_args.args[1]
        self.assertEqual(flags, 0x00000004 | 0x80000000)

    def test_restore_session_preference_is_boolean_only(self):
        with patch('paperassist.paths.config_file', return_value=self.data / 'config.json'):
            self.assertEqual(settings.preferences(), {})
            updated = settings.update_preferences({'restore_session': True})
            self.assertIs(updated['restore_session'], True)
            for bad in ('yes', 1, None, []):
                with self.assertRaises(ValueError):
                    settings.update_preferences({'restore_session': bad})
