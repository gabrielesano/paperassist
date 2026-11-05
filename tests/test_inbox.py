import hashlib
import os
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from paperassist import inbox, references
from paperassist.library import Library
from paperassist.server import create_app
from tests.test_library import pdf

FILLER = 'This article studies the heating of the solar corona by many small and impulsive energy releases in loops.'
REF = {'id': '0', 'label': 'Rappazzo 2013', 'authors': 'Rappazzo', 'year': '2013', 'doi': '',
       'raw': 'Rappazzo, A. F., Velli, M., & Einaudi, G. 2013, ApJ, 771, 76', 'occurrences': [], 'citation_count': 0}
DOI = '10.1088/0004-637x/771/2/76'
TITLE = 'Nanoflares in a twisted coronal loop'


def text_pdf(*lines, subject=''):
    """A one-page PDF with a real text layer, built by hand because pypdf cannot draw text."""
    escape = lambda text: text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    body = 'BT /F1 10 Tf 12 TL 20 760 Td ' + ' '.join(f'({escape(line)}) Tj T*' for line in lines) + ' ET'
    objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
               f'<< /Length {len(body)} >>\nstream\n{body}\nendstream', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               f'<< /Subject ({escape(subject)}) >>']
    out, offsets = b'%PDF-1.4\n', []
    for number, content in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{number} 0 obj\n{content}\nendobj\n'.encode()
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode() + b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    return out + f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # No test may reach Crossref; a test that wants an identity sets the mock's return value.
        patcher = mock.patch('paperassist.retrieval._identity', return_value=None)
        self.identify = patcher.start()
        self.addCleanup(patcher.stop)
        root = Path(self.tmp.name)
        self.folder = root / 'downloads'
        self.folder.mkdir()
        self.library = Library(root / 'library')
        self.source = self.library.import_stream(BytesIO(pdf()), 'source.pdf')['id']
        self.reference(REF)

    def reference(self, ref):
        references._save(self.library, self.source, {'status': 'analyzed', 'extraction_version': references.EXTRACTION_VERSION,
                                                     'destination_version': references.DESTINATION_VERSION, 'references': [ref]})

    def put(self, name, data, age=0):
        path = self.folder / name
        path.write_bytes(data)
        os.utime(path, (time.time() - age, time.time() - age))
        return path

    def snapshot(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()}

    def check(self):
        return inbox.check_folder(self.library, self.source, '0', str(self.folder))

    def stored(self):
        return sorted(p.name for p in self.library.originals.iterdir())

    def current(self):
        return references.get(self.library, self.source)['references'][0]

    def test_doi_on_the_first_page_links_a_copy_and_leaves_the_folder_alone(self):
        self.reference({**REF, 'doi': DOI})
        self.put('other.pdf', text_pdf(FILLER, 'Something else entirely, with no relation to the cited work at all.'))
        self.put('paper.pdf', text_pdf(FILLER, f'Rappazzo et al. doi:{DOI}'))
        before = self.snapshot()
        result = self.check()
        self.assertEqual((result['status'], result['summary']),
                         ('linked', 'Linked from your folder as Rappazzo_2013_ApJ_771_76.pdf (was paper.pdf). The reference could not be identified online.'))
        ref = self.current()
        self.assertEqual((ref['local_document_id'], ref['manual_link']), (result['document_id'], True))
        self.assertEqual((ref['retrieval']['version'], ref['retrieval']['status'], ref['retrieval']['summary']),
                         (2, 'local', result['summary']))
        self.assertEqual(self.library.get_document(result['document_id'])['filename'], 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertEqual(self.snapshot(), before)

    def test_title_and_first_author_verify_and_name_the_copy_from_the_identity(self):
        identity = {'provider': 'crossref', 'doi': DOI, 'title': TITLE, 'first_author': 'Rappazzo', 'year': 2013,
                    'authors': 'A. F. Rappazzo', 'publication': ['APJ', '771', '76', '2013'], 'journal': 'ApJ'}
        self.reference({**REF, 'identity': identity, 'doi': ''})
        self.put('download (3).pdf', text_pdf(FILLER, TITLE.upper(), 'A. F. Rappazzo, M. Velli and G. Einaudi'))
        result = self.check()
        self.assertEqual(result['status'], 'linked')
        document = self.library.get_document(result['document_id'])
        self.assertEqual((result['filename'], document['filename']), ('Rappazzo_2013_ApJ_771_76.pdf',) * 2)
        self.assertEqual(result['summary'], 'Linked from your folder as Rappazzo_2013_ApJ_771_76.pdf (was download (3).pdf).')

    def test_title_without_the_first_author_is_not_enough(self):
        self.reference({**REF, 'identity': {'provider': 'crossref', 'doi': DOI, 'title': TITLE, 'first_author': 'Rappazzo', 'year': 2013}})
        self.put('a.pdf', text_pdf(FILLER, TITLE, 'By somebody else entirely, in another place and time.'), age=60 * 86400)
        self.assertEqual(self.check()['status'], 'none')

    def test_journal_reference_and_author_verify(self):
        self.put('a.pdf', text_pdf('ApJ, 771, 76 (2013)', FILLER, 'A. F. Rappazzo'))
        self.assertEqual(self.check()['status'], 'linked')

    def test_a_contradicting_reference_or_doi_is_rejected(self):
        self.reference({**REF, 'doi': DOI})
        # Same DOI text and author, but the page prints another journal reference.
        self.put('a.pdf', text_pdf('ApJ, 500, 10 (1998)', FILLER, f'Rappazzo doi:{DOI}'))
        # The PDF properties carry a different DOI.
        self.put('b.pdf', text_pdf(FILLER, f'Rappazzo doi:{DOI}', subject='doi:10.1000/another'))
        self.assertEqual(self.check()['status'], 'none')
        self.assertEqual(self.library.list_documents(), [self.library.get_document(self.source)])

    def test_several_matches_are_ambiguous_but_a_duplicate_copy_is_not(self):
        self.reference({**REF, 'doi': DOI})
        same = text_pdf(FILLER, f'Rappazzo doi:{DOI}')
        self.put('a.pdf', same)
        self.put('b.pdf', text_pdf(FILLER, f'Rappazzo doi:{DOI} (author version)'))
        result = self.check()
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual({c['name'] for c in result['candidates']}, {'a.pdf', 'b.pdf'})
        self.assertEqual(len(self.library.list_documents()), 1)
        (self.folder / 'b.pdf').write_bytes(same)
        self.assertEqual(self.check()['status'], 'linked')

    def test_unverifiable_files_are_offered_and_linked_only_on_request(self):
        self.put('scan.pdf', pdf() + b'\n%scan')
        self.put('old.pdf', pdf() + b'\n%old', age=90 * 86400)
        result = self.check()
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual([c['name'] for c in result['candidates']], ['scan.pdf'])
        self.assertEqual(set(result['candidates'][0]), {'name', 'size', 'modified'})
        self.assertEqual(len(self.library.list_documents()), 1)
        ref = inbox.link_file(self.library, self.source, '0', str(self.folder), 'scan.pdf')
        self.assertEqual((ref['manual_link'], ref['retrieval']['status']), (True, 'local'))
        document = self.library.get_document(ref['local_document_id'])
        self.assertEqual((document['filename'], document['metadata_status']), ('Rappazzo_2013_ApJ_771_76.pdf', 'needs_review'))
        self.assertIn('Rappazzo_2013_ApJ_771_76.pdf', self.stored())
        self.assertEqual(ref['retrieval']['summary'],
                         'Linked from your folder as Rappazzo_2013_ApJ_771_76.pdf (was scan.pdf). Chosen by hand, not verified.')

    def test_nothing_matching_and_no_folder(self):
        self.put('old.pdf', text_pdf(FILLER, 'Unrelated.'), age=90 * 86400)
        result = self.check()
        self.assertEqual((result['status'], result['summary']), ('none', f'No PDF in {self.folder} matches this reference. The reference could not be identified online.'))
        self.assertEqual(inbox.check_folder(self.library, self.source, '0', ''),
                         {'status': 'not_configured', 'summary': 'Set a folder for manual downloads in Sources.'})

    def test_only_regular_pdfs_directly_in_the_folder_are_scanned(self):
        self.reference({**REF, 'doi': DOI})
        outside = Path(self.tmp.name) / 'outside.pdf'
        outside.write_bytes(text_pdf(FILLER, f'Rappazzo doi:{DOI}'))
        (self.folder / 'link.pdf').symlink_to(outside)
        (self.folder / 'sub').mkdir()
        (self.folder / 'sub' / 'nested.pdf').write_bytes(outside.read_bytes())
        (self.folder / 'notes.txt').write_text(f'{DOI}')
        self.assertEqual(self.check()['status'], 'none')

    def test_link_file_accepts_only_names_of_pdfs_in_the_folder(self):
        outside = Path(self.tmp.name) / 'outside.pdf'
        outside.write_bytes(pdf())
        (self.folder / 'link.pdf').symlink_to(outside)
        (self.folder / 'sub').mkdir()
        (self.folder / 'sub' / 'nested.pdf').write_bytes(pdf())
        self.put('notes.txt', b'text')
        for name in ('../outside.pdf', 'sub/nested.pdf', str(outside), '..', '', 'link.pdf', 'notes.txt', 'a\\b.pdf', None, 7):
            with self.assertRaises(ValueError, msg=name):
                inbox.link_file(self.library, self.source, '0', str(self.folder), name)
        with self.assertRaises(FileNotFoundError):
            inbox.link_file(self.library, self.source, '0', str(self.folder), 'missing.pdf')
        with self.assertRaises(ValueError):
            inbox.link_file(self.library, self.source, '0', '', 'x.pdf')
        self.assertEqual(len(self.library.list_documents()), 1)

    def test_an_unidentified_reference_is_identified_then_verified_and_stored_under_its_name(self):
        identity = {'provider': 'crossref', 'doi': DOI, 'title': TITLE, 'first_author': 'Rappazzo', 'year': 2013,
                    'authors': 'A. F. Rappazzo, M. Velli', 'publication': ['APJ', '771', '76', '2013'], 'journal': 'ApJ'}
        self.identify.return_value = identity
        # Only the identified DOI can verify this file: the reference itself carries none.
        self.put('1-s2.0-S0000-main.pdf', text_pdf(FILLER, f'doi:{DOI}'))
        before = self.snapshot()
        result = self.check()
        self.assertEqual(result['summary'], 'Linked from your folder as Rappazzo_2013_ApJ_771_76.pdf (was 1-s2.0-S0000-main.pdf).')
        ref = self.current()
        self.assertEqual((ref['identity'], ref['doi']), (identity, DOI))
        document = self.library.get_document(result['document_id'])
        self.assertEqual((document['metadata_status'], document['title']), ('verified', TITLE))
        self.assertEqual(self.stored(), ['Rappazzo_2013_ApJ_771_76.pdf', 'source.pdf'])
        self.assertEqual(self.snapshot(), before)

    def test_a_stored_identity_is_not_looked_up_again(self):
        self.reference({**REF, 'identity': {'provider': 'crossref', 'doi': DOI, 'first_author': 'Rappazzo', 'year': 2013}})
        self.check()
        self.identify.assert_not_called()

    def test_identity_found_without_a_match_is_kept_for_later(self):
        self.identify.return_value = {'provider': 'crossref', 'doi': DOI, 'first_author': 'Rappazzo', 'year': 2013}
        self.put('old.pdf', text_pdf(FILLER, 'Unrelated.'), age=90 * 86400)
        result = self.check()
        self.assertEqual((result['status'], result['summary']), ('none', f'No PDF in {self.folder} matches this reference.'))
        self.assertEqual(self.current()['identity']['doi'], DOI)

    def test_failed_or_erroring_identification_falls_back_to_the_citation(self):
        self.reference({**REF, 'doi': DOI})
        self.identify.side_effect = OSError('offline')
        self.put('paper.pdf', text_pdf(FILLER, f'Rappazzo et al. doi:{DOI}'))
        result = self.check()
        self.assertEqual(result['status'], 'linked')
        self.assertEqual(result['filename'], 'Rappazzo_2013_ApJ_771_76.pdf')
        self.assertTrue(result['summary'].endswith('The reference could not be identified online.'))
        self.assertNotIn('identity', self.current())
        # Without an identity the file is linked by name only and its metadata stays reviewable.
        self.assertEqual(self.library.get_document(result['document_id'])['metadata_status'], 'needs_review')

    def test_a_citation_without_a_journal_reference_gives_author_and_year(self):
        self.reference({**REF, 'raw': 'Rappazzo, A. F. 2013, a private communication', 'doi': DOI})
        self.put('paper.pdf', text_pdf(FILLER, f'Rappazzo doi:{DOI}'))
        self.assertEqual(self.check()['filename'], 'Rappazzo_2013.pdf')

    def test_the_download_name_is_kept_only_without_a_surname(self):
        self.reference({**REF, 'authors': '', 'year': '', 'doi': DOI})
        self.put('Some Download.pdf', text_pdf(FILLER, f'doi:{DOI}'))
        result = self.check()
        self.assertEqual(result['filename'], 'Some Download.pdf')
        self.assertIn('Some_Download.pdf', self.stored())

    def test_a_duplicate_already_in_the_library_is_linked_and_verified_once(self):
        identity = {'provider': 'crossref', 'doi': DOI, 'title': TITLE, 'first_author': 'Rappazzo', 'year': 2013,
                    'publication': ['APJ', '771', '76', '2013']}
        self.reference({**REF, 'identity': identity})
        data = text_pdf(FILLER, f'Rappazzo doi:{DOI}')
        existing = self.library.import_stream(BytesIO(data), 'download.pdf')
        self.put('copy.pdf', data)
        result = self.check()
        self.assertEqual(result['document_id'], existing['id'])
        self.assertEqual(len(self.library.list_documents()), 2)
        self.assertEqual(self.library.get_document(existing['id'])['metadata_status'], 'verified')
        self.assertIn('Rappazzo_2013_ApJ_771_76.pdf', self.stored())

    def test_a_corrected_duplicate_is_linked_but_never_changed(self):
        self.reference({**REF, 'identity': {'provider': 'crossref', 'doi': DOI, 'title': TITLE, 'first_author': 'Rappazzo', 'year': 2013}})
        data = text_pdf(FILLER, f'Rappazzo doi:{DOI}')
        existing = self.library.import_stream(BytesIO(data), 'mine.pdf')
        self.library.update_metadata(existing['id'], {'title': 'My own title'}, status='corrected')
        self.put('copy.pdf', data)
        result = self.check()
        document = self.library.get_document(existing['id'])
        self.assertEqual((result['document_id'], document['title'], document['metadata_status']), (existing['id'], 'My own title', 'corrected'))

    def test_a_picked_file_with_an_identity_is_named_but_not_verified(self):
        self.reference({**REF, 'identity': {'provider': 'crossref', 'doi': DOI, 'title': TITLE, 'first_author': 'Rappazzo',
                                            'year': 2013, 'publication': ['APJ', '771', '76', '2013']}})
        self.put('scan.pdf', pdf() + b'\n%scan')
        ref = inbox.link_file(self.library, self.source, '0', str(self.folder), 'scan.pdf')
        document = self.library.get_document(ref['local_document_id'])
        self.assertEqual((document['filename'], document['metadata_status']), ('Rappazzo_2013_ApJ_771_76.pdf', 'needs_review'))
        self.assertIn('Rappazzo_2013_ApJ_771_76.pdf', self.stored())
        self.assertTrue(ref['retrieval']['summary'].endswith('Chosen by hand, not verified.'))

    def test_the_folder_files_keep_their_bytes_and_modification_time(self):
        self.put('a.pdf', text_pdf(FILLER, f'Rappazzo doi:{DOI}'), age=3600)
        self.reference({**REF, 'doi': DOI})
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.folder.iterdir()}
        self.check()
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.folder.iterdir()}, before)


@mock.patch('paperassist.retrieval._identity', return_value=None)
class InboxEndpointTests(unittest.TestCase):
    def test_check_folder_runs_as_a_job_and_link_file_returns_the_reference(self, identify):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        folder = root / 'downloads'
        folder.mkdir()
        client = create_app(root / 'library').test_client()
        headers = {'X-PaperAssist-Token': client.get('/api/config').json['token']}
        source = Library(root / 'library').import_stream(BytesIO(pdf()), 'source.pdf')['id']
        references._save(Library(root / 'library'), source, {'status': 'analyzed', 'extraction_version': references.EXTRACTION_VERSION,
                                                              'destination_version': references.DESTINATION_VERSION, 'references': [REF]})
        endpoint = f'/api/documents/{source}/references/0/'

        def run(path, **body):
            response = client.post(endpoint + path, json=body, headers=headers)
            if response.status_code != 202:
                return response
            for _ in range(200):
                job = client.get('/api/jobs/' + response.json['id']).json
                if job['status'] in ('completed', 'failed'):
                    return job
                time.sleep(.01)

        self.assertEqual(client.post(endpoint + 'check-folder').status_code, 403)
        self.assertEqual(run('check-folder')['result']['status'], 'not_configured')
        self.assertEqual(client.put('/api/settings', json={'inbox': str(folder)}, headers=headers).status_code, 200)
        (folder / 'scan.pdf').write_bytes(pdf() + b'\n%scan')
        self.assertEqual(run('check-folder')['result']['status'], 'unverified')
        self.assertEqual(run('link-file', name='../library/.token').status_code, 400)
        linked = run('link-file', name='scan.pdf')
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(linked.json['retrieval']['status'], 'local')
        self.assertEqual(linked.json['local_document_id'], 2)
        # The job passes the stored settings on, so identification can use the email and ADS token.
        self.assertEqual(identify.call_args.args[2].inbox, str(folder))
