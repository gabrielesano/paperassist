"""Shared library operations, persistence and background jobs."""

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from paperassist import paths
from paperassist.workspace import JobsFull, LibraryInUse, Workspace, WorkspaceConflict, library_lock
from tests.test_library import pdf


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Windows may hand out an 8.3 short name; the workspace resolves its paths.
        self.root = Path(tmp.name).resolve()
        config = patch('paperassist.paths.config_file', return_value=self.root / 'config.json')
        config.start()
        self.addCleanup(config.stop)
        self.workspace = self.open_workspace(self.root / 'library')
        self.doc = self.workspace.library.import_stream(io.BytesIO(pdf()), 'sample.pdf')

    def open_workspace(self, directory, source='default'):
        workspace = Workspace(directory, source)
        self.addCleanup(workspace.close)
        return workspace

    def finish(self, job):
        for _ in range(200):
            result = self.workspace.job_status(job['id'])
            if result['status'] in ('completed', 'failed', 'cancelled'):
                return result
            time.sleep(.005)
        self.fail('Job did not finish')

    def test_import_progress_failures_deduplication_and_staging_cleanup(self):
        job = self.workspace.import_files([('same.pdf', io.BytesIO(pdf())), ('bad.pdf', io.BytesIO(b'not a PDF'))])
        done = self.finish(job)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['progress'], {'completed': 2, 'total': 2})
        self.assertTrue(done['result']['files'][0]['document']['duplicate'])
        self.assertIn('error', done['result']['files'][1])
        self.assertFalse(list(self.workspace.data_dir.glob('paperassist-upload-*')))
        source = self.root / 'other.pdf'
        source.write_bytes(pdf())
        done = self.finish(self.workspace.import_paths([str(source)]))
        self.assertEqual(done['progress'], {'completed': 1, 'total': 1})
        self.assertEqual(source.read_bytes(), pdf())

    def test_cancel_between_imported_files(self):
        entered, release = threading.Event(), threading.Event()
        original = self.workspace.library.import_stream
        def first(*args):
            entered.set()
            release.wait(2)
            return original(*args)
        with patch.object(self.workspace.library, 'import_stream', side_effect=first):
            job = self.workspace.import_files([('one.pdf', io.BytesIO(pdf())), ('two.pdf', io.BytesIO(pdf()))])
            self.assertTrue(entered.wait(2))
            self.workspace.cancel_job(job['id'])
            release.set()
            done = self.finish(job)
        self.assertEqual(done['status'], 'cancelled')
        self.assertEqual(len(done['result']['files']), 1)
        self.assertFalse(list(self.workspace.data_dir.glob('paperassist-upload-*')))

    def test_job_limits_failure_and_independent_snapshots(self):
        release = threading.Event()
        try:
            jobs = [self.workspace.submit(lambda job, cancel: release.wait(2)) for _ in range(8)]
            with self.assertRaises(JobsFull):
                self.workspace.import_files([('one.pdf', io.BytesIO(pdf()))])
            self.assertFalse(list(self.workspace.data_dir.glob('paperassist-upload-*')))
            with self.assertRaisesRegex(WorkspaceConflict, 'running tasks'):
                self.workspace.prepare_switch(str(self.root / 'next'), 'move')
        finally:
            release.set()
        for job in jobs:
            self.finish(job)
        def fail(job, cancel):
            raise ValueError('synthetic failure')
        result = self.finish(self.workspace.submit(fail))
        self.assertEqual((result['status'], result['error']), ('failed', 'synthetic failure'))
        result['status'] = 'changed'
        self.assertEqual(self.workspace.job_status(result['id'])['status'], 'failed')
        with self.assertRaises(KeyError):
            self.workspace.cancel_job('missing')

    def test_shutdown_cleans_queued_uploads_without_importing_them(self):
        entered = threading.Barrier(3)
        def wait(job, cancel):
            entered.wait(2)
            cancel.wait(2)
        self.workspace.submit(wait)
        self.workspace.submit(wait)
        entered.wait(2)
        with patch.object(self.workspace.library, 'import_stream') as import_stream:
            job = self.workspace.import_files([('queued.pdf', io.BytesIO(pdf()))])
            self.workspace.close()
            import_stream.assert_not_called()
        self.assertEqual(self.workspace.job_status(job['id'])['status'], 'cancelled')
        self.assertFalse(list(self.workspace.data_dir.glob('paperassist-upload-*')))

    def test_references_download_refreshes_local_state_and_records_outcomes(self):
        refs = {'references': [{'id': '1'}, {'id': '2'}]}
        def download(library, ref, progress, settings):
            progress('arxiv')
            if ref['id'] == '2':
                raise RuntimeError('offline')
            return {'status': 'local', 'document_id': self.doc['id']}
        with patch('paperassist.workspace.references.get', return_value=refs) as get, \
             patch('paperassist.workspace.retrieval.download', side_effect=download), \
             patch('paperassist.workspace.references.link') as link, \
             patch('paperassist.workspace.references.set_retrieval') as store:
            result = self.finish(self.workspace.download_references(self.doc['id'], ['1', '2']))
        self.assertEqual([r['status'] for r in result['result']['references']], ['local', 'failed'])
        self.assertEqual(store.call_count, 2)
        self.assertEqual(link.call_count, 1)
        self.assertEqual(get.call_count, 3)

    def test_analysis_folder_link_and_repair_use_the_open_library_and_settings(self):
        self.workspace.update_settings({'inbox': str(self.root)})
        for method, target, value in (
            ('analyze_references', 'references.analyze', {'references': []}),
            ('repair_downloads', 'references.repair_downloads', []),
        ):
            with patch('paperassist.workspace.' + target, return_value=value) as call:
                self.assertEqual(self.finish(getattr(self.workspace, method)(self.doc['id']))['status'], 'completed')
                self.assertIs(call.call_args.args[0], self.workspace.library)
        with patch('paperassist.workspace.inbox.check_folder', return_value={'status': 'no_match'}) as check:
            result = self.finish(self.workspace.check_folder(self.doc['id'], '1'))
            self.assertEqual(result['result']['status'], 'no_match')
            self.assertEqual(check.call_args.args[3], str(self.root))
        with patch('paperassist.workspace.inbox.link_file', return_value={'status': 'linked'}) as link:
            self.assertEqual(self.workspace.link_file(self.doc['id'], '1', 'x.pdf')['status'], 'linked')
            self.assertEqual(link.call_args.args[-1], 'x.pdf')

    def test_view_is_file_specific_survives_metadata_revision_and_restart(self):
        document_id = self.doc['id']
        self.assertIsNone(self.workspace.get_view(document_id))
        view = {'page': 1, 'scale': 1.25}
        self.workspace.save_view(document_id, view)
        self.workspace.library.update_metadata(document_id, {'doi': '10.1234/synthetic'})
        self.workspace.library.save_pdf(document_id, pdf(), 0)
        reopened = self.open_workspace(self.workspace.data_dir)
        self.assertEqual(reopened.get_view(document_id), view)
        self.assertEqual(reopened.save_view(document_id, {'page': 1, 'scale': 'page-fit'}, True), view)
        # Two different byte contents with the same DOI remain independent files.
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        data = io.BytesIO()
        writer.write(data)
        data.seek(0)
        other = reopened.library.import_stream(data, 'variant.pdf')
        reopened.library.update_metadata(other['id'], {'doi': '10.1234/synthetic'})
        self.assertIsNone(reopened.get_view(other['id']))

    def test_invalid_views_never_replace_a_saved_position(self):
        view = {'page': 1, 'scale': 'page-width'}
        self.workspace.save_view(self.doc['id'], view)
        for bad in (None, [], {}, {'page': True, 'scale': 1}, {'page': 0, 'scale': 1},
                    {'page': 2, 'scale': 1}, {'page': 1, 'scale': float('nan')},
                    {'page': 1, 'scale': True}, {'page': 1, 'scale': 'broken'}):
            with self.assertRaises(ValueError):
                self.workspace.save_view(self.doc['id'], bad)
        self.assertEqual(self.workspace.get_view(self.doc['id']), view)
        with self.assertRaises(KeyError):
            self.workspace.get_view(999)

    def test_preferences_are_user_scoped_and_keep_the_library_choice(self):
        paths.save_library(self.workspace.data_dir)
        saved = {'ui_zoom': 1.25, 'quick_view_layout': 'side', 'outline_expanded': False, 'details_expanded': True}
        self.assertEqual(self.workspace.get_preferences(), {})
        self.workspace.save_preferences(saved)
        other = self.open_workspace(self.root / 'other')
        self.assertEqual(other.get_preferences(), saved)
        self.assertEqual(other.save_preferences({'ui_zoom': .8}, True), saved)
        paths.save_library(other.data_dir)
        self.assertEqual(self.workspace.get_preferences(), saved)
        self.assertEqual(paths.saved_library(), other.data_dir)
        for bad in ([], {'ui_zoom': True}, {'ui_zoom': 4}, {'quick_view_layout': 'up'}, {'outline_expanded': 1}, {'unknown': False}):
            with self.assertRaises(ValueError):
                self.workspace.save_preferences(bad)
        self.assertEqual(json.loads(paths.config_file().read_text())['preferences'], saved)

    def test_switch_rechecks_target_lock_and_preserves_preferences(self):
        self.workspace.save_preferences({'ui_zoom': 1.1})
        target = self.root / 'other'
        self.open_workspace(target)
        with library_lock(target):
            with self.assertRaises(LibraryInUse):
                with library_lock(target):
                    pass
            with self.assertRaisesRegex(WorkspaceConflict, 'another PaperAssist'):
                self.workspace.prepare_switch(str(target), 'open')
        self.assertEqual(self.workspace.prepare_switch(str(target), 'open'), target)
        self.assertEqual(paths.saved_library(), target)
        self.assertEqual(self.workspace.get_preferences(), {'ui_zoom': 1.1})
        with self.assertRaises(WorkspaceConflict):
            self.open_workspace(target, 'option').prepare_switch(str(self.root / 'new'), 'move')

    def test_partial_preference_migration_preserves_existing_values(self):
        self.workspace.save_preferences({'ui_zoom': 1.25})
        migrated = self.workspace.save_preferences({'ui_zoom': .8, 'details_expanded': False}, True)
        self.assertEqual(migrated, {'ui_zoom': 1.25, 'details_expanded': False})
        self.workspace.save_preferences({'quick_view_layout': 'side'})
        self.assertEqual(self.workspace.get_preferences(), {**migrated, 'quick_view_layout': 'side'})
