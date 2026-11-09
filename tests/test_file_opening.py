"""External PDF opening preserves source bytes and the library's exact document identity."""
import io
from pathlib import Path
import tempfile
import unittest

from paperassist.library import Library
from paperassist.workspace import Workspace
from tests.test_library import pdf


class ManagedOpeningTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.library = Library(self.root / 'library')
        self.path = self.root / 'Paper with spaces è.pdf'
        self.path.write_bytes(pdf())

    def test_external_copy_duplicate_and_saved_revision_keep_one_document(self):
        original = self.path.read_bytes()
        first = self.library.open_file(self.path)
        self.library.update_metadata(first['id'], {'title': 'My corrected title'})
        self.library.save_pdf(first['id'], original + b'\n%saved revision', 0)
        copy = self.root / 'same paper.pdf'
        copy.write_bytes(original)
        again = self.library.open_file(copy)
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(again['title'], 'My corrected title')
        self.assertEqual(again['revision'], 1)
        self.assertEqual(len(self.library.list_documents()), 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(copy.read_bytes(), original)
        self.assertEqual(self.library.get_path(first['id'], False).read_bytes(), original)

    def test_managed_original_and_current_revision_reuse_the_owner(self):
        first = self.library.open_file(self.path)
        self.library.save_pdf(first['id'], pdf() + b'\n%revision1', 0)
        older = self.library.get_path(first['id'])
        self.assertEqual(self.library.open_file(older)['id'], first['id'])
        self.library.save_pdf(first['id'], pdf() + b'\n%revision2', 1)
        self.assertEqual(self.library.open_file(self.library.get_path(first['id'], False))['revision'], 2)
        with self.assertRaisesRegex(ValueError, 'older revision'):
            self.library.open_file(older)
        self.assertEqual(len(self.library.list_documents()), 1)

    def test_another_librarys_managed_files_are_not_imported(self):
        other = Library(self.root / 'other')
        document = other.import_stream(io.BytesIO(pdf()), 'owned.pdf')
        for path in (other.get_path(document['id']),):
            with self.assertRaisesRegex(ValueError, 'another PaperAssist library'):
                self.library.open_file(path)
        self.assertEqual(self.library.list_documents(), [])
        self.assertEqual(other.get_document(document['id'])['revision'], 0)

    def test_changed_managed_original_is_not_reused_under_the_old_identity(self):
        document = self.library.open_file(self.path)
        original = self.library.get_path(document['id'], False)
        original.write_bytes(pdf() + b'\n%changed bytes')
        with self.assertRaisesRegex(ValueError, 'original has changed'):
            self.library.open_file(original)
        self.assertEqual(len(self.library.list_documents()), 1)

    def test_opening_rejects_folders_missing_and_non_pdf_files(self):
        for path in (self.root, self.root / 'missing.pdf', self.root / 'note.txt'):
            if path.suffix == '.txt':
                path.write_text('notes')
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                self.library.open_file(path)
        self.assertEqual(self.library.list_documents(), [])

    def test_background_open_reports_each_file_without_losing_successes(self):
        import threading
        import time
        workspace = Workspace(self.root / 'library')
        self.addCleanup(workspace.close)
        job = workspace.open_files([str(self.path), str(self.root / 'missing.pdf')])
        deadline = time.monotonic() + 5
        while (result := workspace.job_status(job['id']))['status'] in ('pending', 'running') and time.monotonic() < deadline:
            threading.Event().wait(.01)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(result['result']['documents']), 1)
        self.assertEqual(len(result['result']['errors']), 1)
        self.assertEqual(result['progress'], {'completed': 2, 'total': 2})
