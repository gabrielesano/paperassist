import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from paperassist.library import Library


def pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = Library(Path(self.tmp.name) / 'library')
        self.payload = pdf()

    def test_duplicate_and_corrections(self):
        first = self.library.import_stream(io.BytesIO(self.payload), 'one.pdf')
        self.assertEqual(first['title'], 'one')
        self.assertIsNone(first['doi'])
        self.library.update_metadata(first['id'], {'title': 'Corrected', 'year': 2020})
        second = self.library.import_stream(io.BytesIO(self.payload), 'other.pdf')
        self.assertTrue(second['duplicate'])
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(second['title'], 'Corrected')
        self.assertEqual(len(self.library.list_documents('2020')), 1)

    def test_immutable_original_notes_and_conflict(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'file.pdf')
        original = self.library.get_path(doc['id'], annotated=False)
        self.library.save_notes(doc['id'], [{'page': 1, 'text': 'hello'}])
        self.assertEqual(self.library.get_notes(doc['id'])[0]['text'], 'hello')
        self.assertEqual(self.library.save_pdf(doc['id'], self.payload, 0)['revision'], 1)
        with self.assertRaisesRegex(ValueError, 'Revision conflict'):
            self.library.save_pdf(doc['id'], self.payload, 0)
        self.assertEqual(original.read_bytes(), self.payload)
        self.assertNotEqual(original, self.library.get_path(doc['id']))

    def test_missing_snapshot_and_orphan_do_not_replace_current(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'file.pdf')
        document_id = doc['id']
        self.library.save_pdf(document_id, self.payload, 0)
        orphan = self.library.working / f'{document_id}-2.pdf'
        orphan.write_bytes(b'orphan')
        self.assertEqual(self.library.get_path(document_id).read_bytes(), self.payload)
        self.library.get_path(document_id).unlink()
        with self.assertRaises(FileNotFoundError):
            self.library.get_path(document_id)
        with self.assertRaisesRegex(ValueError, 'Revision conflict'):
            self.library.save_pdf(document_id, self.payload, 0)

    def test_shared_doi_and_repair_missing_original(self):
        first = self.library.import_stream(io.BytesIO(self.payload), 'first.pdf')
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        stream = io.BytesIO()
        writer.write(stream)
        second = self.library.import_stream(io.BytesIO(stream.getvalue()), 'second.pdf')
        a = self.library.update_metadata(first['id'], {'doi': 'https://doi.org/10.1234/ABC'})
        b = self.library.update_metadata(second['id'], {'doi': '10.1234/abc'})
        self.assertEqual(a['work_id'], b['work_id'])
        self.assertEqual(len(self.library.find_by_doi('DOI:10.1234/ABC')), 2)
        with self.library._connect() as db:
            self.assertEqual(db.execute('SELECT preferred_file_id FROM works WHERE id = ?', (a['work_id'],)).fetchone()[0], first['id'])
        self.library.save_notes(first['id'], [{'text': 'first'}])
        self.assertEqual(self.library.get_notes(second['id']), [])
        self.library.get_path(first['id'], False).unlink()
        duplicate = self.library.import_stream(io.BytesIO(self.payload), 'again.pdf')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['work_id'], a['work_id'])
        self.assertEqual(self.library.get_path(first['id'], False).read_bytes(), self.payload)

    def test_reimport_refreshes_old_parser_without_losing_edits(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'old.pdf')
        self.library.update_metadata(doc['id'], {'title': 'My correction'})
        self.library.save_pdf(doc['id'], self.payload, 0)
        with self.library._connect() as db:
            db.execute("UPDATE documents SET extracted = '{}' WHERE id = ?", (doc['id'],))
        result = self.library.import_stream(io.BytesIO(self.payload), 'renamed.pdf')
        self.assertEqual(result['title'], 'My correction')
        self.assertEqual(result['revision'], 1)
        self.assertEqual(result['metadata']['parser_version'], 3)
        self.assertEqual(self.library.get_path(doc['id']).read_bytes(), self.payload)

    def test_progress_and_cancellation(self):
        root = Path(self.tmp.name) / 'inputs'
        root.mkdir()
        (root / 'a.pdf').write_bytes(self.payload)
        (root / 'b.pdf').write_bytes(self.payload)
        import threading
        cancel = threading.Event()
        seen = []
        def progress(done, total):
            seen.append((done, total))
            cancel.set()
        result = self.library.import_paths([root], progress=progress, cancel=cancel)
        self.assertEqual(seen, [(1, 2)])
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['imported'], 1)

    def test_isolated_errors_and_recursive_paths(self):
        root = Path(self.tmp.name) / 'input'
        root.mkdir()
        (root / 'good.pdf').write_bytes(self.payload)
        (root / 'bad.pdf').write_bytes(b'not pdf')
        result = self.library.import_paths([root, root / 'missing'])
        self.assertEqual(result['imported'], 1)
        self.assertEqual(len(result['errors']), 2)
        self.assertEqual(self.library.import_paths([root])['duplicates'], 1)


if __name__ == '__main__':
    unittest.main()


class VerifiedMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = Library(Path(self.tmp.name) / 'library')
        self.payload = pdf()
        self.record = {'provider': 'crossref', 'title': 'Nanoflares in a twisted loop', 'authors': 'A. F. Rappazzo',
                       'year': 2013, 'doi': '10.1088/0004-637x/771/2/76', 'publication': ['APJ', '771', '76', '2013']}

    def test_fills_download_placeholder_without_marking_it_corrected(self):
        document = self.library.import_stream(io.BytesIO(self.payload), 'download.pdf')
        self.assertEqual(document['title'], 'download')
        document = self.library.apply_verified(document['id'], self.record, 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertEqual((document['filename'], document['title'], document['year'], document['doi']),
                         ('Rappazzo_2013_ApJ_771_76.pdf', 'Nanoflares in a twisted loop', 2013, self.record['doi']))
        self.assertEqual(document['metadata_status'], 'verified')
        self.assertEqual(document['metadata']['sources']['title'], 'crossref')
        self.assertEqual(self.library.find_by_doi(self.record['doi'])[0]['id'], document['id'])

    def test_duplicate_download_keeps_user_corrections(self):
        first = self.library.import_stream(io.BytesIO(self.payload), 'mine.pdf')
        self.library.update_metadata(first['id'], {'title': 'My title', 'year': 2020})
        duplicate = self.library.import_stream(io.BytesIO(self.payload), 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertTrue(duplicate['duplicate'])
        document = self.library.apply_verified(duplicate['id'], self.record, 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertEqual((document['title'], document['year'], document['doi'], document['filename'], document['metadata_status']),
                         ('My title', 2020, None, 'mine.pdf', 'corrected'))

    def test_verified_record_replaces_extracted_pdf_properties(self):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.add_metadata({'/Title': 'Loop heating model', '/Author': 'Unknown author'})
        stream = io.BytesIO()
        writer.write(stream)
        document = self.library.import_stream(io.BytesIO(stream.getvalue()), 'download.pdf')
        self.assertEqual(document['metadata']['sources']['title'], 'pdf_info')
        document = self.library.apply_verified(document['id'], self.record, 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertEqual((document['title'], document['authors']), (self.record['title'], self.record['authors']))

    def test_file_names_are_not_titles(self):
        from paperassist.metadata import useful
        self.assertFalse(useful('rdbpf_6.eps'))
        self.assertTrue(useful('Loop heating model'))

    def test_user_chosen_filenames_are_kept(self):
        first = self.library.import_stream(io.BytesIO(self.payload), 'paper.pdf')
        document = self.library.apply_verified(first['id'], self.record, 'New_name.pdf')
        self.assertEqual((document['filename'], document['doi']), ('paper.pdf', self.record['doi']))
