import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from pypdf import PdfWriter

from paperassist.library import Library, safe_filename
from paperassist.metadata import PARSER_VERSION


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

    def test_search_matches_displayed_review_labels_and_preserves_raw_status_queries(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'unidentified.pdf')
        for status in ('needs_review', 'incomplete', 'corrected', 'verified'):
            self.library.update_metadata(doc['id'], {'title': 'Unidentified paper'}, status=status)
            with self.subTest(status=status):
                for query in ('Needs review', 'Metadata needs review', '  NEEDS REVIEW  '):
                    expected = [doc['id']] if status in ('needs_review', 'incomplete') else []
                    self.assertEqual([item['id'] for item in self.library.list_documents(query)], expected)
                self.assertEqual([item['id'] for item in self.library.list_documents(status)], [doc['id']])
                self.assertEqual([item['id'] for item in self.library.list_documents()], [doc['id']])

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
        self.assertEqual(result['metadata']['parser_version'], PARSER_VERSION)
        self.assertEqual(self.library.get_path(doc['id']).read_bytes(), self.payload)

    def test_label_is_a_nickname_that_leaves_the_catalogue_alone(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'one.pdf')
        self.assertEqual(doc['label'], '')
        labelled = self.library.set_label(doc['id'], '  Reale\n 2016  ')
        self.assertEqual(labelled['label'], 'Reale 2016')
        for key in ('title', 'authors', 'year', 'metadata_status', 'stored_name'):
            self.assertEqual(labelled[key], doc[key])
        self.assertEqual(self.library.list_documents()[0]['label'], 'Reale 2016')
        # Re-reading the PDF with a newer parser rewrites the extracted data, not the nickname.
        with self.library._connect() as db:
            db.execute("UPDATE documents SET extracted = '{}' WHERE id = ?", (doc['id'],))
        again = self.library.import_stream(io.BytesIO(self.payload), 'other.pdf')
        self.assertEqual(again['label'], 'Reale 2016')
        self.assertEqual(self.library.set_label(doc['id'], '   ')['label'], '')

    def test_label_does_not_rename_the_stored_file(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'one.pdf')
        before = sorted(path.name for path in self.library.originals.iterdir())
        self.library.set_label(doc['id'], 'Reale 2016')
        self.assertEqual(sorted(path.name for path in self.library.originals.iterdir()), before)

    def test_label_validates_type_and_length(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'one.pdf')
        for bad in (None, 5, ['x'], {'a': 1}):
            with self.assertRaisesRegex(ValueError, 'string'):
                self.library.set_label(doc['id'], bad)
        self.assertEqual(len(self.library.set_label(doc['id'], 'x' * 80)['label']), 80)
        with self.assertRaisesRegex(ValueError, 'longer'):
            self.library.set_label(doc['id'], 'x' * 81)
        with self.assertRaises(KeyError):
            self.library.set_label(999, 'x')

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

    def test_parser_upgrade_keeps_verified_metadata_and_provenance(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'download.pdf')
        doc = self.library.apply_verified(doc['id'], self.record)
        metadata = {**doc['metadata'], 'parser_version': 0}
        with self.library._connect() as db:
            db.execute('UPDATE documents SET extracted = ? WHERE id = ?', (json.dumps(metadata), doc['id']))
        duplicate = self.library.import_stream(io.BytesIO(self.payload), 'again.pdf')
        for key in ('title', 'authors', 'year', 'doi', 'metadata_status', 'work_id'):
            self.assertEqual(duplicate[key], doc[key], key)
        self.assertEqual(duplicate['metadata']['verified'], doc['metadata']['verified'])
        self.assertEqual(duplicate['metadata']['sources'], doc['metadata']['sources'])

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


class ReadableNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'library'
        self.library = Library(self.root)
        self.payload = pdf()
        self.record = {'provider': 'crossref', 'title': 'Nanoflares in a twisted loop', 'authors': 'A. F. Rappazzo, M. Velli',
                       'year': 2013, 'doi': '10.1088/0004-637x/771/2/76', 'publication': ['APJ', '771', '76', '2013'],
                       'first_author': 'Rappazzo'}

    def variant(self, width):
        writer = PdfWriter()
        writer.add_blank_page(width=width, height=72)
        stream = io.BytesIO()
        writer.write(stream)
        return stream.getvalue()

    def names(self):
        return sorted(path.name for path in self.library.originals.glob('*.pdf'))

    def legacy(self, document_id):
        """Turn a document back into what libraries looked like before readable names."""
        doc = self.library.get_document(document_id)
        self.library.get_path(document_id, False).rename(self.library.originals / f'{doc["sha256"]}.pdf')
        with self.library._connect() as db:
            db.execute('UPDATE documents SET stored_name = NULL WHERE id = ?', (document_id,))

    def test_safe_filename_rules(self):
        self.assertEqual(safe_filename('Müller – Sun 2020.PDF'), 'Müller_–_Sun_2020.pdf')
        self.assertEqual(safe_filename('a/b\\c:d*e?f"g<h>i|j\tk.pdf'), 'a_b_c_d_e_f_g_h_i_j_k.pdf')
        self.assertEqual(safe_filename('  ..hidden. . '), 'hidden.pdf')
        self.assertEqual(safe_filename('CON.pdf'), '_CON.pdf')
        self.assertEqual(safe_filename('lpt3.pdf'), '_lpt3.pdf')
        self.assertEqual(safe_filename('Console.pdf'), 'Console.pdf')
        self.assertEqual(safe_filename('x' * 300 + '.pdf'), 'x' * 120 + '.pdf')
        self.assertLessEqual(len(safe_filename('é' * 300).encode()), 204)
        for generic in ('download.pdf', 'Document (1).pdf', '12345.pdf', 'a' * 64 + '.pdf', '', '...', 'file_2.pdf'):
            self.assertEqual(safe_filename(generic), '', generic)

    def test_import_keeps_the_given_name_and_the_bytes(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'My paper 2020.pdf')
        self.assertEqual(doc['stored_name'], 'My_paper_2020.pdf')
        path = self.library.get_path(doc['id'], False)
        self.assertEqual(path.name, 'My_paper_2020.pdf')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), doc['sha256'])
        self.assertTrue(self.library.import_stream(io.BytesIO(self.payload), 'other.pdf')['duplicate'])
        self.assertEqual(self.names(), ['My_paper_2020.pdf'])

    def test_generic_names_become_untitled_and_collisions_are_case_insensitive(self):
        first = self.library.import_stream(io.BytesIO(self.payload), 'download.pdf')
        self.assertEqual(first['stored_name'], f'Untitled_{first["id"]}.pdf')
        a = self.library.import_stream(io.BytesIO(self.variant(100)), 'Paper.pdf')
        b = self.library.import_stream(io.BytesIO(self.variant(101)), 'PAPER.pdf')
        c = self.library.import_stream(io.BytesIO(self.variant(102)), 'paper.pdf')
        self.assertEqual((a['stored_name'], b['stored_name'], c['stored_name']), ('Paper.pdf', 'PAPER_2.pdf', 'paper_3.pdf'))

    def test_import_never_overwrites_a_stray_file(self):
        (self.library.originals / 'taken.pdf').write_bytes(b'precious')
        doc = self.library.import_stream(io.BytesIO(self.payload), 'taken.pdf')
        self.assertEqual(doc['stored_name'], 'taken_2.pdf')
        self.assertEqual((self.library.originals / 'taken.pdf').read_bytes(), b'precious')

    def test_legacy_row_resolves_to_sha_name(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'paper.pdf')
        self.legacy(doc['id'])
        self.assertEqual(self.library.get_path(doc['id'], False).name, f'{doc["sha256"]}.pdf')

    def test_verified_metadata_renames_the_original(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'download.pdf')
        doc = self.library.apply_verified(doc['id'], self.record, 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertEqual(doc['stored_name'], 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertEqual(self.names(), ['Rappazzo_2013_ApJ_771_76.pdf'])
        self.assertEqual(self.library.get_path(doc['id'], False).read_bytes(), self.payload)

    def test_correction_renames_and_keeps_the_identified_reference(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'scan 17.pdf')
        self.library.apply_verified(doc['id'], self.record)
        doc = self.library.update_metadata(doc['id'], {'authors': 'Rappazzo, A. F.', 'year': 2014})
        self.assertEqual(doc['stored_name'], 'Rappazzo_2014_ApJ_771_76.pdf')
        doc = self.library.update_metadata(doc['id'], {'title': 'Another title'})
        self.assertEqual(doc['stored_name'], 'Rappazzo_2014_ApJ_771_76.pdf')

    def test_correcting_an_unverified_paper_keeps_its_name(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'mine.pdf')
        self.assertEqual(self.library.update_metadata(doc['id'], {'title': 'Fixed'})['stored_name'], 'mine.pdf')

    def test_second_paper_with_the_same_reference_gets_a_numbered_name(self):
        a = self.library.import_stream(io.BytesIO(self.payload), 'a.pdf')
        b = self.library.import_stream(io.BytesIO(self.variant(100)), 'b.pdf')
        self.library.apply_verified(a['id'], self.record)
        b = self.library.apply_verified(b['id'], {**self.record, 'doi': '10.1234/other'})
        self.assertEqual(b['stored_name'], 'Rappazzo_2013_ApJ_771_76_2.pdf')

    def test_failed_move_keeps_the_old_name_and_is_retried_later(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'download.pdf')
        old = doc['stored_name']
        with mock.patch('paperassist.library.os.replace', side_effect=PermissionError):
            doc = self.library.apply_verified(doc['id'], self.record)
        self.assertEqual((doc['stored_name'], doc['metadata_status']), (old, 'verified'))
        self.assertEqual(self.library.get_path(doc['id'], False).read_bytes(), self.payload)
        self.library.update_metadata(doc['id'], {'title': 'Nanoflares'}, status='corrected')
        self.assertEqual(self.library.get_document(doc['id'])['stored_name'], 'Rappazzo_2013_ApJ_771_76.pdf')

    def test_failed_database_update_puts_the_file_back(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'download.pdf')
        old = doc['stored_name']
        real = self.library._connect

        class Failing:
            def __init__(self, db):
                self.db = db
            def __getattr__(self, name):
                return getattr(self.db, name)
            def execute(self, sql, *args):
                if 'stored_name' in sql and sql.startswith('UPDATE'):
                    raise sqlite3.OperationalError('disk full')
                return self.db.execute(sql, *args)

        @contextmanager
        def connect():
            with real() as db:
                yield Failing(db)

        with mock.patch.object(self.library, '_connect', connect), self.assertRaises(sqlite3.OperationalError):
            self.library.apply_verified(doc['id'], self.record)
        self.assertEqual(self.names(), [old])
        self.assertEqual(self.library.get_document(doc['id'])['stored_name'], old)

    def test_reopening_renames_legacy_files_once(self):
        ids = [self.library.import_stream(io.BytesIO(self.variant(100 + n)), f'paper{n}.pdf')['id'] for n in range(3)]
        shas = {i: self.library.get_document(i)['sha256'] for i in ids}
        for i in ids:
            self.legacy(i)
        self.assertEqual(self.names(), sorted(f'{sha}.pdf' for sha in shas.values()))
        library = Library(self.root)
        after = self.names()
        self.assertEqual(after, ['paper0.pdf', 'paper1.pdf', 'paper2.pdf'])
        for i, sha in shas.items():
            self.assertEqual(hashlib.sha256(library.get_path(i, False).read_bytes()).hexdigest(), sha)
        with mock.patch('paperassist.library.hashlib.file_digest') as digest:
            Library(self.root)
        digest.assert_not_called()
        self.assertEqual(self.names(), after)

    def test_recovers_when_the_file_moved_but_the_database_did_not(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'paper.pdf')
        self.library.get_path(doc['id'], False).rename(self.library.originals / 'moved.pdf')
        library = Library(self.root)
        self.assertEqual(library.get_document(doc['id'])['stored_name'], 'moved.pdf')
        self.assertEqual(library.get_path(doc['id'], False).read_bytes(), self.payload)

    def test_recovers_when_the_database_moved_but_the_file_did_not(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'download.pdf')
        self.library.apply_verified(doc['id'], self.record)
        self.legacy(doc['id'])  # the file is back at its old name ...
        with self.library._connect() as db:  # ... while the database already records the new one
            db.execute("UPDATE documents SET stored_name = 'Rappazzo_2013_ApJ_771_76.pdf' WHERE id = ?", (doc['id'],))
        library = Library(self.root)
        self.assertEqual(library.get_document(doc['id'])['stored_name'], 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertEqual(self.names(), ['Rappazzo_2013_ApJ_771_76.pdf'])

    def test_a_foreign_file_is_never_adopted_for_a_missing_original(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'paper.pdf')
        self.library.get_path(doc['id'], False).unlink()
        (self.library.originals / 'other.pdf').write_bytes(self.variant(200))
        library = Library(self.root)
        self.assertEqual(library.get_document(doc['id'])['stored_name'], 'paper.pdf')
        self.assertEqual(sorted(self.names()), ['other.pdf'])

    def test_old_database_without_the_column_is_upgraded(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'paper.pdf')
        self.legacy(doc['id'])
        with self.library._connect() as db:
            db.execute('ALTER TABLE documents DROP COLUMN stored_name')
        library = Library(self.root)
        self.assertEqual(library.get_path(doc['id'], False).name, 'paper.pdf')

    def test_repairing_a_missing_original_gives_it_a_readable_name(self):
        doc = self.library.import_stream(io.BytesIO(self.payload), 'paper.pdf')
        self.legacy(doc['id'])
        self.library.get_path(doc['id'], False).unlink()
        again = self.library.import_stream(io.BytesIO(self.payload), 'new.pdf')
        self.assertEqual(again['stored_name'], 'paper.pdf')
        self.assertEqual(self.library.get_path(doc['id'], False).read_bytes(), self.payload)
