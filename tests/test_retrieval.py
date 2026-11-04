import json
import os
import unittest
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from paperassist import retrieval
from paperassist.settings import Settings
from paperassist.retrieval import Blocked, download, identify, search_urls, _download_pdf

RSPTA = {'raw': 'Bareford, P. K., & Hood, A. W. 2015, Phil. Trans. R. Soc. Lond. A, 373, 20140266',
         'authors': 'Bareford', 'year': '2015'}
REF = {'raw': 'Rappazzo, A. F., Velli, M., & Einaudi, G. 2013, ApJ, 771, 76', 'authors': 'Rappazzo', 'year': '2013'}


def feed(doi='10.1234/test', identifier='2401.12345v1', extra=''):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>https://arxiv.org/abs/{identifier}</id><arxiv:doi>{doi}</arxiv:doi>{extra}</entry>
    </feed>'''.encode()


def crossref(*items):
    return json.dumps({'message': {'items': list(items)}}).encode()


def work(doi='10.1088/0004-637x/771/2/76', family='Rappazzo', volume='771', page='76', year=2013,
         container='The Astrophysical Journal', title='Nanoflares in a twisted loop'):
    return {'DOI': doi, 'title': [title], 'author': [{'given': 'A. F.', 'family': family}],
            'container-title': [container], 'volume': volume, 'page': page, 'article-number': page,
            'issued': {'date-parts': [[year]]}}


class Network:
    """Route mocked urlopen calls by a host name fragment; unknown hosts answer 404."""

    def __init__(self, **routes):
        self.routes, self.urls, self.headers = routes, [], []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        self.headers.append(dict(request.header_items()))
        hostname = urlsplit(url).hostname
        route = next((value for key, value in self.routes.items() if key in hostname), None)
        if route is None:
            raise HTTPError(url, 404, 'Not found', {}, BytesIO())
        body = route(url) if callable(route) else route
        return BytesIO(body)


def ran(result):
    """Attempt statuses, leaving out sources the setup does not allow to run."""
    return [(a['provider'], a['status']) for a in result['attempts'] if a['status'] != 'not_configured']


class Base(unittest.TestCase):
    def setUp(self):
        retrieval._CACHE.clear()
        retrieval._LAST_REQUEST.clear()
        for target in (patch('time.sleep'), patch.dict(os.environ, {}, clear=True)):
            target.start()
            self.addCleanup(target.stop)

    def attempt(self, result, provider):
        return next(a for a in result['attempts'] if a['provider'] == provider)

    def library(self, identifier=42):
        library = Mock()
        library.import_stream.return_value = {'id': identifier, 'doi': ''}
        library.find_by_doi.return_value = []
        return library


class RetrievalTests(Base):
    def test_search_links_encode_input(self):
        urls = search_urls({'title': 'A & B', 'authors': 'Smith', 'year': 2020})
        self.assertEqual(parse_qs(urlsplit(urls['google']).query)['q'], ['A & B Smith 2020'])

    def test_reject_private_hosts(self):
        with self.assertRaises(ValueError):
            _download_pdf('https://127.0.0.1/private')

    def test_matching_doi_downloads_arxiv(self):
        library = self.library()
        with patch('paperassist.retrieval.urlopen', Network(arxiv=feed())), \
             patch('paperassist.retrieval._download_pdf', return_value=b'%PDF-1.7\ncontent') as pdf:
            result = download(library, {'doi': '10.1234/test'})
        self.assertEqual(result['document_id'], 42)
        self.assertEqual(ran(result), [('arxiv', 'downloaded')])
        pdf.assert_called_once_with('https://arxiv.org/pdf/2401.12345v1')

    def test_arxiv_id_and_mismatch(self):
        with patch('paperassist.retrieval.urlopen', Network(arxiv=feed())), \
             patch('paperassist.retrieval._download_pdf', return_value=b'%PDF-1.7'):
            self.assertEqual(download(self.library(7), {'title': 'arXiv:2401.12345'})['document_id'], 7)
        with patch('paperassist.retrieval.urlopen', Network(arxiv=feed('10.9999/other'))):
            result = download(self.library(), {'doi': '10.1234/test'})
        self.assertEqual(ran(result), [('arxiv', 'not_found'), ('openalex', 'not_found'), ('semantic_scholar', 'not_found')])

    def test_missing_identity_and_network_failure_are_distinct(self):
        result = download(None, {'title': 'Unknown'})
        self.assertEqual((result['version'], result['status']), (2, 'not_identified'))
        self.assertEqual(result['summary'], 'Not identified. No first author in this reference.')
        with patch('paperassist.retrieval.urlopen', side_effect=OSError('offline')):
            result = download(None, {'doi': '10.1234/test'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(ran(result)[0], ('arxiv', 'error'))
        self.assertEqual(result['summary'], 'Using DOI 10.1234/test. arXiv failed (offline); OpenAlex failed (offline); Semantic Scholar failed (offline). '
                                            'Try again later or search the web.')

    def test_identification_failure_skips_doi_providers_without_hiding_arxiv(self):
        ref = {'authors': 'Guarrasi', 'year': '2014', 'raw': 'Guarrasi, F. 2014, A&A 564, A48'}
        with patch('paperassist.retrieval.urlopen', Network(arxiv=feed('10.9999/other'))):
            result = download(None, ref)
        self.assertEqual(result['status'], 'not_identified')
        self.assertEqual([(a['provider'], a['status']) for a in result['attempts']],
                         [('crossref', 'not_found'), ('ads', 'not_configured'), ('arxiv', 'not_found'), ('openalex', 'skipped'),
                          ('semantic_scholar', 'skipped'), ('core', 'not_configured'), ('unpaywall', 'not_configured')])
        self.assertEqual(result['attempts'][3]['message'], 'Needs a DOI.')
        self.assertEqual(result['summary'], 'Not identified. Crossref has no record matching year 2014, volume 564, page A48 and first author Guarrasi.')

    def test_local_first(self):
        library = Mock()
        library.get_path.return_value.is_file.return_value = True
        with patch('paperassist.retrieval.urlopen') as request:
            self.assertEqual(download(library, {'local_document_id': 9})['status'], 'local')
        request.assert_not_called()

    def test_arxiv_journal_reference_requires_unique_exact_match(self):
        entry = '''<entry><id>https://arxiv.org/abs/2401.12345v1</id><author><name>F. Guarrasi</name></author>
          <arxiv:journal_ref>A&amp;A 564, A48 (2014)</arxiv:journal_ref></entry>'''
        xml = lambda *entries: ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
                                + ''.join(entries) + '</feed>').encode()
        ref = {'authors': 'Guarrasi', 'year': '2014', 'raw': 'Guarrasi, F. 2014, A&A 564, A48'}
        network = Network(arxiv=xml(entry))
        with patch('paperassist.retrieval.urlopen', network), \
             patch('paperassist.retrieval._download_pdf', return_value=b'%PDF-1.7'):
            self.assertEqual(download(self.library(), ref)['status'], 'downloaded')
        query = parse_qs(urlsplit(next(u for u in network.urls if 'arxiv' in u)).query)['search_query'][0]
        self.assertEqual(query, 'au:"guarrasi" AND jr:"564"')  # volume, not the journal name
        retrieval._CACHE.clear()
        other = entry.replace('2401.12345v1', '2401.99999v1')
        with patch('paperassist.retrieval.urlopen', Network(arxiv=xml(entry, other))), \
             patch('paperassist.retrieval._download_pdf') as pdf:
            result = download(None, ref)
        self.assertEqual(ran(result)[1], ('arxiv', 'ambiguous'))
        pdf.assert_not_called()

    def test_arxiv_journal_reference_with_other_volume_is_rejected(self):
        entry = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
          <id>https://arxiv.org/abs/2401.12345v1</id><author><name>F. Guarrasi</name></author>
          <arxiv:journal_ref>A&amp;A 565, A48 (2014)</arxiv:journal_ref></entry></feed>'''
        ref = {'authors': 'Guarrasi', 'year': '2014', 'raw': 'Guarrasi, F. 2014, A&A 564, A48'}
        with patch('paperassist.retrieval.urlopen', Network(arxiv=entry)):
            self.assertEqual(retrieval._arxiv_pdf(ref, Settings())[0], 'not_found')

    def test_arxiv_title_match_needs_author_and_nearby_year(self):
        def entry(author='A. F. Rappazzo', published='2013-01-31', title='Nanoflares in a\n twisted loop.'):
            return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
              <id>https://arxiv.org/abs/1301.7678v2</id><published>{published}T00:00:00Z</published>
              <title>{title}</title><author><name>{author}</name></author></entry></feed>'''.encode()
        ref = {**REF, 'identity': {'title': 'Nanoflares in a twisted loop', 'year': 2013}}
        with patch('paperassist.retrieval.urlopen', Network(arxiv=entry())):
            self.assertEqual(retrieval._arxiv_pdf(ref, Settings())[2], ['https://arxiv.org/pdf/1301.7678v2'])
        for changed in ({'author': 'M. Velli'}, {'published': '2010-01-01'}, {'title': 'Another paper'}):
            retrieval._CACHE.clear()
            with patch('paperassist.retrieval.urlopen', Network(arxiv=entry(**changed))):
                self.assertEqual(retrieval._arxiv_pdf(ref, Settings())[0], 'not_found', changed)

    def test_identify_accepts_only_one_fully_agreeing_record(self):
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(work(), work(page='77')))):
            status, _, identity = identify(REF)
        self.assertEqual(status, 'found')
        self.assertEqual((identity['doi'], identity['first_author'], identity['publication']),
                         ('10.1088/0004-637x/771/2/76', 'Rappazzo', ['APJ', '771', '76', '2013']))
        self.assertEqual(identity['provider'], 'crossref')
        self.assertEqual(identity['evidence']['page'], '76')

    def test_identify_rejects_near_misses(self):
        misses = {'wrong page': work(page='77'), 'wrong volume': work(volume='772'),
                  'wrong first author': work(family='Velli'), 'wrong year': work(year=2011)}
        for reason, item in misses.items():
            retrieval._CACHE.clear()
            with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(item))):
                self.assertEqual(identify(REF)[0], 'not_found', reason)

    def test_identify_two_qualifying_records_are_ambiguous(self):
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(work(), work(doi='10.1088/other')))):
            self.assertEqual(identify(REF)[0], 'ambiguous')

    def test_identify_handles_particles_and_letter_pages(self):
        ref = {'raw': 'van Ballegooijen, A. A., & Berger, M. 2014, ApJL, 787, L22', 'authors': 'van Ballegooijen', 'year': '2014'}
        item = work(family='Ballegooijen', volume='787', page='L22', year=2014)
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(item))):
            self.assertEqual(identify(ref)[0], 'found')

    def test_identify_needs_author_volume_and_page(self):
        self.assertEqual(identify({'raw': 'Smith 2020. A title.', 'authors': 'Smith', 'year': '2020'})[0], 'needs_identification')

    def test_identify_uses_mailto_only_when_configured(self):
        network = Network(crossref=crossref())
        with patch('paperassist.retrieval.urlopen', network):
            identify(REF)
            retrieval._CACHE.clear()
            with patch.dict(os.environ, {'PAPERASSIST_EMAIL': 'reader@example.org'}):
                identify(REF)
        self.assertNotIn('mailto', network.urls[0])
        self.assertIn('mailto=reader%40example.org', network.urls[1])

    def test_identified_reference_downloads_via_openalex_with_verified_name(self):
        library = self.library(5)
        openalex = json.dumps({'locations': [
            {'is_oa': True, 'pdf_url': 'https://publisher.example/a.pdf', 'source': {'type': 'journal'}},
            {'is_oa': True, 'pdf_url': 'https://repo.example/a.pdf', 'source': {'type': 'repository'}}]}).encode()
        network = Network(crossref=crossref(work()), openalex=openalex)
        with patch('paperassist.retrieval.urlopen', network), \
             patch('paperassist.retrieval._download_pdf', side_effect=[ValueError('HTTP 403'), b'%PDF-1.7']) as pdf:
            result = download(library, REF)
        self.assertEqual((result['status'], result['provider']), ('downloaded', 'openalex'))
        self.assertEqual(result['identity']['doi'], '10.1088/0004-637x/771/2/76')
        self.assertEqual([call.args[0] for call in pdf.call_args_list],
                         ['https://repo.example/a.pdf', 'https://publisher.example/a.pdf'])
        self.assertEqual(library.import_stream.call_args.args[1], 'Rappazzo_2013_ApJ_771_76.pdf')
        library.apply_verified.assert_called_once()

    def test_identified_doi_reuses_local_document(self):
        library = self.library()
        library.find_by_doi.return_value = [{'id': 8}]
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(work()))) as network:
            result = download(library, REF)
        self.assertEqual((result['status'], result['document_id']), ('local', 8))
        library.import_stream.assert_not_called()
        self.assertEqual(len(network.urls), 1)

    def test_openalex_requires_key_only_when_service_demands_it(self):
        def denied(url):
            raise HTTPError(url, 401, 'Unauthorized', {}, BytesIO())
        with patch('paperassist.retrieval.urlopen', Network(openalex=denied)):
            self.assertEqual(retrieval._openalex_pdf({'doi': '10.1/x'}, Settings())[0], 'not_configured')
        retrieval._CACHE.clear()
        network = Network(openalex=json.dumps({'locations': []}).encode())
        with patch.dict(os.environ, {'OPENALEX_API_KEY': 'configured'}), patch('paperassist.retrieval.urlopen', network):
            self.assertEqual(retrieval._openalex_pdf({'doi': '10.1/x'}, Settings())[0], 'not_found')
        self.assertIn('api_key=configured', network.urls[0])

    def test_unpaywall_after_arxiv_no_match(self):
        unpaywall = b'{"best_oa_location":{"url_for_pdf":"https://example.org/a.pdf"}}'
        with patch.dict(os.environ, {'PAPERASSIST_EMAIL': 'reader@example.org'}), \
             patch('paperassist.retrieval.urlopen', Network(arxiv=feed('10.9999/other'), unpaywall=unpaywall)), \
             patch('paperassist.retrieval._download_pdf', return_value=b'%PDF-1.7'):
            result = download(self.library(), {'doi': '10.1234/test'})
        self.assertEqual(ran(result), [('arxiv', 'not_found'), ('openalex', 'not_found'), ('semantic_scholar', 'not_found'), ('unpaywall', 'downloaded')])

    def test_identify_accepts_pageless_record_only_when_its_journal_agrees(self):
        ref = {'raw': 'Reale, F. 2014, LRSP, 11, 4', 'authors': 'Reale', 'year': '2014'}
        review = {**work(doi='10.12942/lrsp-2014-4', family='Reale', volume='11', year=2014,
                         container='Living Reviews in Solar Physics'), }
        del review['page'], review['article-number']
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(review))):
            self.assertEqual(identify(ref)[0], 'found')
        retrieval._CACHE.clear()
        review['container-title'] = ['Another Journal']
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(review))):
            self.assertEqual(identify(ref)[0], 'not_found')

    def search(self, ref, library=None, **routes):
        with patch('paperassist.retrieval.urlopen', Network(**routes)), \
             patch('paperassist.retrieval._download_pdf', return_value=b'%PDF-1.7'):
            return download(library or self.library(), ref)

    def test_unknown_journal_is_identified_only_when_four_facts_agree(self):
        item = work(doi='10.1098/rsta.2014.0266', family='Bareford', volume='373', page='20140266', year=2015,
                    container='Philosophical Transactions of the Royal Society A')
        ref = {'raw': 'Bareford, P. K. 2015, Obscure Journal Name, 373, 20140266', 'authors': 'Bareford', 'year': '2015'}
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(item))):
            status, _, identity = identify(ref)
        self.assertEqual((status, identity['doi']), ('found', '10.1098/rsta.2014.0266'))
        wrong = [{'author': work(family='Hood')['author']}, {'volume': '372'}, {'page': '1', 'article-number': '1'},
                 {'issued': {'date-parts': [[2011]]}}]
        for change in wrong:
            retrieval._CACHE.clear()
            with patch('paperassist.retrieval.urlopen', Network(crossref=crossref({**item, **change}))):
                self.assertEqual(identify(ref)[0], 'not_found', change)
        retrieval._CACHE.clear()
        twin = {**item, 'DOI': '10.1098/other'}
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(item, twin))):
            status, message, _ = identify(ref)
        self.assertEqual((status, message), ('ambiguous', 'Several Crossref records match; choose the PDF manually.'))

    def test_citation_tail_wins_over_a_later_journal_in_merged_text(self):
        ref = {'raw': 'Yeates, A. R., & Hornig, G. 2014, JPhCS, 544, 012002 7 The Astrophysical Journal, 817:5',
               'authors': 'Yeates', 'year': '2014'}
        item = work(doi='10.1088/1742-6596/544/1/012002', family='Yeates', volume='544', page='012002', year=2014,
                    container='Journal of Physics: Conference Series')
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(item))):
            self.assertEqual(identify(ref)[0], 'found')

    def test_listed_journal_spelling_is_known(self):
        item = work(doi='10.1098/rsta.2014.0266', family='Bareford', volume='373', page='20140266', year=2015,
                    container='Philosophical Transactions of the Royal Society A: Mathematical, Physical and Engineering Sciences')
        with patch('paperassist.retrieval.urlopen', Network(crossref=crossref(item))):
            self.assertEqual(identify(RSPTA)[2]['publication'], ['RSPTA', '373', '20140266', '2015'])

    def test_thesis_gets_a_specific_message(self):
        ref = {'raw': 'Tam, K. V. 2014, PhD thesis, St Andrews Univ., http://hdl.handle.net/ 10023/6373', 'authors': 'Tam', 'year': '2014'}
        result = self.search(ref)
        self.assertEqual((result['status'], result['attempts'][0]['status']), ('not_identified', 'needs_identification'))
        self.assertEqual(result['summary'], 'Not identified. No journal volume and page in this reference.')

    def test_downloaded_and_local(self):
        result = self.search(REF, crossref=crossref(work()), openalex=json.dumps({'locations': [
            {'is_oa': True, 'pdf_url': 'https://repo.example/a.pdf', 'source': {'type': 'repository'}}]}).encode())
        self.assertEqual((result['status'], result['provider']), ('downloaded', 'openalex'))
        self.assertEqual(result['summary'], 'Identified via Crossref (DOI 10.1088/0004-637x/771/2/76). Downloaded from OpenAlex.')
        library = self.library()
        library.find_by_doi.return_value = [{'id': 8}]
        result = self.search(REF, library, crossref=crossref(work()))
        self.assertEqual((result['status'], result['summary']),
                         ('local', 'Identified via Crossref (DOI 10.1088/0004-637x/771/2/76). Already in the library.'))

    def test_identified_without_pdf_is_not_found_and_unconfigured_provider_is_trailing_only(self):
        empty = json.dumps({'locations': []}).encode()
        result = self.search(REF, crossref=crossref(work()), arxiv=feed('10.9999/other'), openalex=empty)
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['summary'], 'Identified via Crossref (DOI 10.1088/0004-637x/771/2/76). '
                                            'No open-access PDF on arXiv, OpenAlex or Semantic Scholar.')
        self.assertEqual(result['attempts'][-1]['status'], 'not_configured')

    def test_error_makes_a_missing_pdf_inconclusive(self):
        def denied(url):
            raise HTTPError(url, 500, 'Server error', {}, BytesIO())
        result = self.search(REF, crossref=crossref(work()), arxiv=feed('10.9999/other'), openalex=denied)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['summary'], 'Identified via Crossref (DOI 10.1088/0004-637x/771/2/76). arXiv found no match; '
                                            'OpenAlex failed (HTTP 500); Semantic Scholar found no match. Try again later or search the web.')

    def test_ambiguous_identification_asks_the_user(self):
        result = self.search(REF, crossref=crossref(work(), work(doi='10.1088/other')))
        self.assertEqual((result['status'], result['summary']), ('ambiguous', 'Several Crossref records match; choose the PDF manually.'))

    def test_crossref_outage_is_failed_not_unidentified(self):
        def busy(url):
            raise HTTPError(url, 429, 'Too many requests', {}, BytesIO())
        result = self.search(REF, crossref=busy, arxiv=feed('10.9999/other'))
        self.assertEqual((result['status'], result['summary']),
                         ('failed', 'Not identified. Crossref failed (HTTP 429); arXiv found no match. Try again later or search the web.'))

    OPENALEX_PDF = json.dumps({'locations': [
        {'is_oa': True, 'pdf_url': 'https://publisher.example/a.pdf', 'source': {'type': 'journal'}}]}).encode()
    LEAD = 'Identified via Crossref (DOI 10.1088/0004-637x/771/2/76).'

    def blocked_search(self, pdf, ref=REF, **routes):
        routes = {'crossref': crossref(work()), 'arxiv': feed('10.9999/other'), 'openalex': self.OPENALEX_PDF, **routes}
        with patch('paperassist.retrieval.urlopen', Network(**routes)), \
             patch('paperassist.retrieval._download_pdf', **pdf):
            return download(self.library(), ref)

    def test_refused_download_is_blocked_with_the_link(self):
        result = self.blocked_search({'side_effect': Blocked('The site refused the automatic download (HTTP 403).')})
        self.assertEqual((result['status'], result['pdf_url']), ('blocked', 'https://publisher.example/a.pdf'))
        attempt = self.attempt(result, 'openalex')
        self.assertEqual((attempt['provider'], attempt['status'], attempt['url']),
                         ('openalex', 'blocked', 'https://publisher.example/a.pdf'))
        self.assertEqual(attempt['message'], 'The site refused the automatic download (HTTP 403).')
        self.assertEqual(result['summary'], self.LEAD + ' OpenAlex lists an open-access PDF, but the site refused '
                                            'the automatic download. Open it in your browser, then use Link local PDF.')

    def test_html_instead_of_pdf_is_blocked(self):
        result = self.blocked_search({'return_value': b'  <!DOCTYPE html><html><body>Captcha</body></html>'})
        self.assertEqual((result['status'], self.attempt(result, 'openalex')['message']),
                         ('blocked', 'The link opens a web page, not a PDF.'))
        self.assertIn('but the link opens a web page, not a PDF. Open it', result['summary'])

    def test_http_refusal_is_detected_in_the_real_download(self):
        response = Mock(status=403)
        with patch('paperassist.retrieval.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('93.184.216.34', 443))]), \
             patch('paperassist.retrieval.socket.create_connection'), patch('paperassist.retrieval.ssl'), \
             patch('paperassist.retrieval.http.client.HTTPSConnection') as connection:
            connection.return_value.getresponse.return_value = response
            with self.assertRaisesRegex(Blocked, 'HTTP 403'):
                _download_pdf('https://publisher.example/a.pdf')

    def test_other_provider_can_still_succeed_after_a_refusal(self):
        unpaywall = b'{"best_oa_location":{"url_for_pdf":"https://repo.example/a.pdf"}}'
        with patch.dict(os.environ, {'PAPERASSIST_EMAIL': 'reader@example.org'}):
            result = self.blocked_search({'side_effect': [Blocked('The site refused the automatic download (HTTP 403).'), b'%PDF-1.7']},
                                         unpaywall=unpaywall)
        self.assertEqual((result['status'], result['provider']), ('downloaded', 'unpaywall'))
        self.assertEqual(ran(result), [('crossref', 'found'), ('arxiv', 'not_found'), ('openalex', 'blocked'), ('semantic_scholar', 'not_found'), ('unpaywall', 'downloaded')])
        self.assertNotIn('pdf_url', result)

    def test_temporary_download_errors_stay_failed(self):
        for error in (TimeoutError('timed out'), ValueError('Download returned HTTP 503'), ValueError('Download returned HTTP 429')):
            result = self.blocked_search({'side_effect': error})
            self.assertEqual((result['status'], self.attempt(result, 'openalex')['status']), ('failed', 'error'), error)
            self.assertNotIn('pdf_url', result)

    def test_a_mix_of_refusal_and_timeout_is_not_blocked(self):
        two = json.dumps({'locations': [{'is_oa': True, 'pdf_url': f'https://{host}.example/a.pdf', 'source': {'type': 'journal'}}
                                        for host in ('one', 'two')]}).encode()
        result = self.blocked_search({'side_effect': [Blocked('The site refused the automatic download (HTTP 403).'), TimeoutError('timed out')]},
                                     openalex=two)
        self.assertEqual(result['status'], 'failed')

    def test_blocked_beats_failed_but_not_ambiguous(self):
        def busy(url):
            raise HTTPError(url, 500, 'Server error', {}, BytesIO())
        refused = {'side_effect': Blocked('The site refused the automatic download (HTTP 403).')}
        with patch.dict(os.environ, {'PAPERASSIST_EMAIL': 'reader@example.org'}):
            result = self.blocked_search(refused, unpaywall=busy)
        self.assertEqual((result['status'], [a['status'] for a in result['attempts'] if a['status'] != 'not_configured']),
                         ('blocked', ['found', 'not_found', 'blocked', 'not_found', 'error']))
        entries = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">' + ''.join(
            f'<entry><id>https://arxiv.org/abs/2401.1234{n}v1</id><author><name>R. Rappazzo</name></author>'
            '<arxiv:journal_ref>ApJ 771, 76 (2013)</arxiv:journal_ref></entry>' for n in (1, 2)) + '</feed>'
        retrieval._CACHE.clear()
        result = self.blocked_search(refused, arxiv=entries.encode())
        self.assertEqual(result['status'], 'ambiguous')


SECRET = 'secret-token-1234'


def ads(*docs):
    return json.dumps({'response': {'docs': list(docs)}}).encode()


def ads_doc(**changes):
    return {'bibcode': '2013ApJ...771...76R', 'doi': ['10.1088/0004-637X/771/2/76'], 'title': ['Nanoflares in a twisted loop'],
            'author': ['Rappazzo, A. F.', 'Velli, M.'], 'year': '2013', 'bibstem': ['ApJ'], 'volume': '771', 'page': ['76'],
            'pub': 'The Astrophysical Journal', 'esources': ['PUB_PDF', 'EPRINT_HTML', 'ADS_SCAN', 'EPRINT_PDF'], **changes}


def configured(**keys):
    return Settings({'keys': keys, 'email': 'reader@example.org'})


class SourceTests(Base):
    """New open-access sources, their keys and the provider order."""

    def run_download(self, routes, settings, ref=REF, library=None, pdf=b'%PDF-1.7'):
        network = Network(**routes)
        with patch('paperassist.retrieval.urlopen', network), \
             patch('paperassist.retrieval._download_pdf', return_value=pdf) as fetch:
            result = download(library or self.library(), ref, settings=settings)
        return result, network, fetch

    def test_ads_identifies_when_crossref_cannot_and_sends_the_token_in_a_header(self):
        network = Network(adsabs=ads(ads_doc()))
        with patch('paperassist.retrieval.urlopen', network):
            status, message, record = retrieval.identify_ads(REF, configured(ads=SECRET))
        self.assertEqual((status, record['bibcode'], record['doi'], record['first_author']), ('found', '2013ApJ...771...76R', '10.1088/0004-637x/771/2/76', 'Rappazzo'))
        self.assertEqual(record['publication'], ['APJ', '771', '76', '2013'])
        self.assertEqual(network.headers[0]['Authorization'], 'Bearer ' + SECRET)
        query = parse_qs(urlsplit(network.urls[0]).query)['q'][0]
        self.assertEqual(query, 'author:"^Rappazzo" year:2013 volume:771 page:76 bibstem:ApJ')
        self.assertNotIn(SECRET, network.urls[0])

    def test_ads_record_must_agree_on_every_fact(self):
        for change in ({'year': '2014'}, {'volume': '772'}, {'page': ['77']}, {'author': ['Someone, E.']}, {'bibstem': ['MNRAS']}):
            retrieval._CACHE.clear()
            with patch('paperassist.retrieval.urlopen', Network(adsabs=ads(ads_doc(**change)))):
                self.assertEqual(retrieval.identify_ads(REF, configured(ads=SECRET))[0], 'not_found', change)
        retrieval._CACHE.clear()
        with patch('paperassist.retrieval.urlopen', Network(adsabs=ads(ads_doc(), ads_doc(bibcode='2013ApJ...771...76X')))):
            self.assertEqual(retrieval.identify_ads(REF, configured(ads=SECRET))[0], 'ambiguous')

    def test_ads_identity_without_doi_downloads_through_the_link_gateway(self):
        old = ads_doc(doi=None, esources=['ADS_SCAN', 'PUB_PDF'])
        library = self.library()
        result, network, fetch = self.run_download({'adsabs': ads(old), 'arxiv': feed('10.9999/other')}, configured(ads=SECRET), library=library)
        self.assertEqual((result['status'], result['provider']), ('downloaded', 'ads'))
        self.assertEqual(result['summary'], 'Identified via ADS (bibcode 2013ApJ...771...76R). Downloaded from ADS.')
        self.assertEqual(fetch.call_args.args[0], 'https://ui.adsabs.harvard.edu/link_gateway/2013ApJ...771...76R/ADS_SCAN')
        library.find_by_doi.assert_not_called()
        self.assertEqual(result['identity']['provider'], 'ads')

    def test_ads_prefers_free_copies_in_a_fixed_order(self):
        every = ['PUB_PDF', 'AUTHOR_PDF', 'ADS_SCAN', 'ADS_PDF', 'EPRINT_PDF', 'EPRINT_HTML']
        with patch('paperassist.retrieval.urlopen', Network(adsabs=ads(ads_doc(esources=every, bibcode='2014A&A...564A..48G')))):
            status, message, urls = retrieval._ads_pdf({'doi': '10.1/x'}, configured(ads=SECRET))
        self.assertEqual([url.rsplit('/', 1)[1] for url in urls], ['EPRINT_PDF', 'ADS_PDF', 'ADS_SCAN', 'AUTHOR_PDF', 'PUB_PDF'])
        self.assertTrue(urls[0].startswith('https://ui.adsabs.harvard.edu/link_gateway/2014A%26A...564A..48G/'))

    def test_ads_rejected_token_never_leaks(self):
        def denied(url):
            raise HTTPError(url, 401, 'Unauthorized', {}, BytesIO())
        result, network, fetch = self.run_download({'crossref': crossref(work()), 'adsabs': denied, 'arxiv': feed('10.9999/other')}, configured(ads=SECRET))
        attempt = self.attempt(result, 'ads')
        self.assertEqual((attempt['status'], attempt['message']), ('error', retrieval.REJECTED))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_semantic_scholar_uses_arxiv_id_before_the_open_access_url(self):
        info = json.dumps({'externalIds': {'ArXiv': '1301.7678'}, 'openAccessPdf': {'url': 'https://repo.example/a.pdf'}}).encode()
        with patch('paperassist.retrieval.urlopen', Network(semanticscholar=info)) as network:
            status, message, urls = retrieval._semantic_scholar_pdf({'doi': '10.1/x'}, configured(semantic_scholar=SECRET))
        self.assertEqual(urls, ['https://arxiv.org/pdf/1301.7678', 'https://repo.example/a.pdf'])
        retrieval._CACHE.clear()
        only = json.dumps({'openAccessPdf': {'url': 'http://insecure.example/a.pdf'}}).encode()
        with patch('paperassist.retrieval.urlopen', Network(semanticscholar=only)):
            self.assertEqual(retrieval._semantic_scholar_pdf({'doi': '10.1/x'}, Settings())[0], 'not_found')

    def test_semantic_scholar_key_goes_in_a_header_and_rate_limit_is_a_failure(self):
        network = Network(semanticscholar=json.dumps({'openAccessPdf': None}).encode())
        with patch('paperassist.retrieval.urlopen', network):
            retrieval._semantic_scholar_pdf({'doi': '10.1/x'}, configured(semantic_scholar=SECRET))
        self.assertEqual(network.headers[0]['X-api-key'], SECRET)
        self.assertNotIn(SECRET, network.urls[0])
        def busy(url):
            raise HTTPError(url, 429, 'Too many requests', {}, BytesIO())
        result, *_ = self.run_download({'crossref': crossref(work()), 'arxiv': feed('10.9999/other'), 'semanticscholar': busy}, Settings())
        self.assertEqual((result['status'], self.attempt(result, 'semantic_scholar')['message']), ('failed', 'HTTP 429'))

    def test_core_uses_exact_doi_matches_only(self):
        results = {'results': [{'doi': '10.1088/0004-637x/771/2/76', 'downloadUrl': 'https://core.ac.uk/download/pdf/1.pdf'},
                               {'doi': '10.9999/other', 'downloadUrl': 'https://core.ac.uk/download/pdf/2.pdf'},
                               {'doi': '10.1088/0004-637x/771/2/76', 'downloadUrl': None}]}
        network = Network(core=json.dumps(results).encode())
        with patch('paperassist.retrieval.urlopen', network):
            status, message, urls = retrieval._core_pdf({'doi': '10.1088/0004-637X/771/2/76'}, configured(core=SECRET))
        self.assertEqual(urls, ['https://core.ac.uk/download/pdf/1.pdf'])
        self.assertEqual(network.headers[0]['Authorization'], 'Bearer ' + SECRET)

    def test_disabled_sources_are_skipped_and_do_not_change_the_outcome(self):
        settings = Settings({'enabled': {'ads': False, 'openalex': False, 'semantic_scholar': False, 'unpaywall': False}, 'email': 'reader@example.org'})
        result, network, fetch = self.run_download({'crossref': crossref(work()), 'arxiv': feed('10.9999/other')}, settings)
        by = {a['provider']: a for a in result['attempts']}
        self.assertEqual([by[name]['status'] for name in ('ads', 'openalex', 'semantic_scholar', 'unpaywall')], ['skipped'] * 4)
        self.assertEqual(by['ads']['message'], 'ADS is disabled in Settings.')
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['summary'], 'Identified via Crossref (DOI 10.1088/0004-637x/771/2/76). No open-access PDF on arXiv.')
        self.assertFalse([url for url in network.urls if any(host in url for host in ('adsabs', 'openalex', 'semanticscholar', 'unpaywall'))])

    def test_disabled_crossref_is_not_called(self):
        result, network, _ = self.run_download({}, Settings({'enabled': {'crossref': False}}), ref={'doi': '10.1234/test'})
        self.assertEqual(result['attempts'][0], {'provider': 'crossref', 'status': 'skipped', 'message': 'Crossref is disabled in Settings.'})
        self.assertFalse([url for url in network.urls if 'crossref' in url])

    def test_providers_run_in_the_documented_order(self):
        every = Settings({'keys': {'ads': SECRET, 'core': SECRET}, 'email': 'reader@example.org'})
        result, *_ = self.run_download({'crossref': crossref(work()), 'adsabs': ads(ads_doc(esources=[])), 'arxiv': feed('10.9999/other'),
                                           'core': b'{"results": []}', 'unpaywall': b'{}'}, every)
        self.assertEqual([a['provider'] for a in result['attempts']],
                         ['crossref', 'ads', 'arxiv', 'openalex', 'semantic_scholar', 'core', 'unpaywall'])
        self.assertEqual([a['status'] for a in result['attempts']], ['found'] + ['not_found'] * 6)
        self.assertNotIn('not_configured', json.dumps(result))

    def test_unconfigured_sources_never_change_the_status(self):
        result, *_ = self.run_download({'crossref': crossref(work()), 'arxiv': feed('10.9999/other')}, Settings())
        self.assertEqual({a['provider'] for a in result['attempts'] if a['status'] == 'not_configured'}, {'ads', 'core', 'unpaywall'})
        self.assertEqual(result['status'], 'not_found')
        self.assertIn('Add a NASA ADS token in Settings to use ADS.', [a['message'] for a in result['attempts']])

    def test_check_reports_each_source_without_echoing_keys(self):
        def route(url):
            if 'adsabs' in url:
                raise HTTPError(url, 401, 'Unauthorized', {}, BytesIO())
            if 'semanticscholar' in url:
                raise HTTPError(url, 429, 'Too many requests', {}, BytesIO())
            if 'unpaywall' in url:
                raise OSError('offline')
            return b'{}'
        cfg = configured(ads=SECRET, core=SECRET)
        with patch('paperassist.retrieval.urlopen', Network(adsabs=route, semanticscholar=route, unpaywall=route, core=route)) as network:
            results = retrieval.check(cfg)
        self.assertEqual(results['ads'], {'ok': False, 'message': 'Key rejected.'})
        self.assertEqual(results['core'], {'ok': True, 'message': 'Accepted.'})
        self.assertFalse(results['semantic_scholar']['ok'])
        self.assertEqual(results['unpaywall'], {'ok': False, 'message': 'Network error: offline'})
        self.assertNotIn('openalex', results)
        self.assertNotIn(SECRET, json.dumps(results))
