"""Search links, Crossref identification and conservative open-access retrieval."""

import difflib
import html
import http.client
import ipaddress
import json
import re
import xml.etree.ElementTree as ET
import socket
import threading
import time
import unicodedata
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
from io import BytesIO

from pypdf import PdfReader

from paperassist.citation import fingerprint, fold, generic_publication, journal_code, journal_name, pdf_name, publication, same_surname
from paperassist.settings import Settings

MAX_PDF = 100 * 1024 * 1024
# Minimum seconds between requests per host; arXiv asks for three.
_INTERVALS = {'export.arxiv.org': 3, 'api.crossref.org': 1, 'api.adsabs.harvard.edu': 1, 'api.semanticscholar.org': 1.1, 'api.core.ac.uk': 1}
_LOCK = threading.Lock()
_CACHE = {}
_LAST_REQUEST = {}
DOI_PATTERN = re.compile(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+')
CROSSREF_FIELDS = 'DOI,title,author,container-title,volume,page,article-number,issued,published-print,published-online'


def search_urls(ref):
    query = ' '.join(str(ref.get(k) or '') for k in ('title', 'authors', 'year', 'doi')).strip()
    return {'google': 'https://www.google.com/search?' + urlencode({'q': query}),
            'pdf': 'https://www.google.com/search?' + urlencode({'q': query + ' filetype:pdf'})}


class Blocked(Exception):
    """A PDF link exists but the site will not hand it to an automated client; retrying cannot help."""

    def __init__(self, message, url=None):
        super().__init__(message)
        self.url = url


def _download_pdf(url):
    # Connect to the address already checked, so DNS rebinding cannot reach a local host.
    for _ in range(6):
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError('Only public HTTPS downloads are allowed')
        addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('Download host is not public')
        address = addresses[0][4][0]
        connection = http.client.HTTPSConnection(parts.hostname, timeout=30)
        raw_socket = socket.create_connection((address, 443), timeout=30)
        try:
            connection.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parts.hostname)
            path = parts.path or '/'
            if parts.query:
                path += '?' + parts.query
            connection.request('GET', path, headers={'User-Agent': 'PaperAssist/0.1'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Redirect has no destination')
                url = urljoin(url, location)
                if not url.startswith('https://'):
                    # Publisher DOI links often pass through plain HTTP; that is a page for the browser, not an error.
                    raise Blocked('The link redirects to an address that cannot be fetched automatically.')
                continue
            if response.status in (401, 403):
                raise Blocked(f'The site refused the automatic download (HTTP {response.status}).')
            if response.status != 200:
                raise ValueError(f'Download returned HTTP {response.status}')
            return response.read(MAX_PDF + 1)
        finally:
            connection.close()
            raw_socket.close()
    raise ValueError('Too many redirects')





def _get(url, cfg, headers=None, fresh=False):
    """Fetch with per-host throttling. Credentials travel in `headers`, never in the cache key or messages."""
    host = urlsplit(url).hostname
    with _LOCK:
        if not fresh and url in _CACHE:
            return _CACHE[url]
        delay = _INTERVALS.get(host, 0) - (time.monotonic() - _LAST_REQUEST.get(host, float('-inf')))
        if delay > 0:
            time.sleep(delay)
        agent = 'PaperAssist/0.1' + (f' (mailto:{cfg.email})' if cfg.email else '')
        try:
            with urlopen(Request(url, headers={'User-Agent': agent, **(headers or {})}), timeout=15) as response:
                data = response.read(2 * 1024 * 1024)
        finally:
            _LAST_REQUEST[host] = time.monotonic()
        if not fresh:
            if len(_CACHE) >= 200:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[url] = data
        return data


def _crossref_url(cfg, path, **params):
    # The polite pool needs a real contact address, so it is used only when configured.
    if cfg.email:
        params['mailto'] = cfg.email
    return 'https://api.crossref.org/' + path + '?' + urlencode(params)


def _text(value):
    return ' '.join(re.sub(r'<[^>]+>', '', html.unescape(value or '')).split())


def _crossref_years(item):
    dates = [(item.get(key) or {}).get('date-parts') or [[None]] for key in ('published-print', 'issued', 'published-online')]
    return [parts[0][0] for parts in dates if parts[0] and parts[0][0]]


def _crossref_pages(item):
    first = re.split(r'[–-]', str(item.get('page') or ''))[0]
    return {value.upper() for value in (first, str(item.get('article-number') or '')) if value}


def _crossref_record(item):
    authors = item.get('author') or []
    containers = item.get('container-title') or []
    years = _crossref_years(item)
    pages = sorted(_crossref_pages(item))
    return {'provider': 'crossref', 'doi': item['DOI'].lower(), 'title': _text((item.get('title') or [''])[0]),
            'authors': ', '.join(' '.join(filter(None, (a.get('given'), a.get('family')))) for a in authors),
            'first_author': authors[0].get('family', '') if authors else '',
            'journal': _text(containers[0]) if containers else '', 'year': years[0] if years else None,
            'publication': fingerprint(journal_code(containers[0]) if containers else None,
                                       item.get('volume'), pages[0] if pages else '', years[0] if years else '')}


def crossref_metadata(doi, cfg=None, *, fresh=False):
    """Retrieve metadata only for an explicit DOI, not an unverified search hit."""
    cfg = cfg or Settings()
    item = json.loads(_get(_crossref_url(cfg, 'works/' + quote(doi, safe='/')), cfg, fresh=fresh))['message']
    return {**_crossref_record(item), 'provider': 'Crossref'}


def _candidates(ref):
    """(year, volume, page, journal code) readings of the reference's citation, best first."""
    raw = ref.get('raw') or ''
    # The tail right after the year is the most reliable reading; `publication` also knows
    # other layouts, but may pick a journal named later in text merged from the next entry.
    tail, cited = generic_publication(raw), publication(raw, ref.get('year', ''))
    candidates = [(tail[0], tail[1], tail[2], journal_code(tail[3]))] if tail else []
    if cited and not (tail and cited[1:3] == list(tail[1:3])):
        candidates.append((cited[3], cited[1], cited[2], cited[0]))
    return candidates


def identify(ref, cfg=None):
    """Find a cited work's DOI in Crossref, accepting only one record that fully agrees.

    Year, volume, first page and first-author family name must all match, so a
    plausible but different paper is never adopted on title or text similarity.
    A journal missing from the citation table is not needed: these four facts are
    enough to verify a record, as long as exactly one record carries them.
    """
    cfg = cfg or Settings()
    surname, raw = ref.get('authors'), ref.get('raw') or ''
    if not surname:
        return 'needs_identification', 'No first author in this reference.', None
    candidates = _candidates(ref)
    if not candidates:
        return 'needs_identification', 'No journal volume and page in this reference.', None
    outcomes = [_crossref_lookup(cfg, raw, surname, *candidate) for candidate in candidates]
    return next((outcome for outcome in outcomes if outcome[0] != 'not_found'), outcomes[0])


def _crossref_items(cfg, year, **query):
    query = {**query, 'select': CROSSREF_FIELDS, 'filter': f'type:journal-article,from-pub-date:{int(year) - 1},until-pub-date:{int(year) + 1}'}
    try:
        return json.loads(_get(_crossref_url(cfg, 'works', **query), cfg))['message']['items']
    except HTTPError as error:
        if error.code != 404:
            raise
        return []


def _crossref_lookup(cfg, raw, surname, year, volume, page, journal):
    def page_agrees(item):
        # Some journals (e.g. Living Reviews) deposit no page; then the journal itself must agree.
        pages = _crossref_pages(item)
        return page in pages or bool(journal) and not pages and journal_code((item.get('container-title') or [''])[0]) == journal
    items = _crossref_items(cfg, year, **{'query.bibliographic': unicodedata.normalize('NFKC', raw)[:300], 'rows': 5})
    agreeing = {item['DOI'].lower(): item for item in items
                if int(year) in _crossref_years(item) and item.get('volume') == volume
                and page_agrees(item)
                and same_surname((item.get('author') or [{}])[0].get('family', ''), surname)}
    relaxed = False
    if not agreeing and journal == 'SCI' and re.fullmatch(r'[A-Z]\d+', page):
        agreeing, relaxed = _science_electronic(cfg, surname, year, volume), True
    if not agreeing:
        return 'not_found', f'Crossref has no record matching year {year}, volume {volume}, page {page} and first author {surname}.', None
    if len(agreeing) > 1:
        return 'ambiguous', 'Several Crossref records match; choose the PDF manually.', None
    record = _crossref_record(next(iter(agreeing.values())))
    record.update(year=int(year), publication=fingerprint(journal, volume, page, year), checked_at=datetime.now(timezone.utc).isoformat(),
                  evidence={'year': year, 'volume': volume, 'page': page, 'first_author': record['first_author']})
    if relaxed:
        record['evidence']['page_check'] = 'relaxed: ADS-style page of an electronic article'
        return 'found', 'Crossref has one Science article by this first author, volume and year; the ADS-style page could not be compared.', record
    return 'found', 'Crossref record agrees on year, volume, page and first author.', record


def _science_electronic(cfg, surname, year, volume):
    """Science articles are cited from ADS with a page ID such as D315, while Crossref only knows their electronic ID.

    That ID cannot be compared with the page, so the match rests on journal, volume, year and
    first author, and only an electronic-ID article without a printed page qualifies.
    """
    items = _crossref_items(cfg, year, **{'query.author': surname, 'query.container-title': 'Science', 'rows': 20})
    return {item['DOI'].lower(): item for item in items
            if journal_code((item.get('container-title') or [''])[0]) == 'SCI' and item.get('volume') == volume
            and int(year) in _crossref_years(item) and not item.get('page') and item.get('article-number')
            and same_surname((item.get('author') or [{}])[0].get('family', ''), surname)}


def _squash(text):
    return re.sub(r'[^a-z0-9]', '', fold(_text(text)))


def _words(title):
    """Title tokens that survive typesetting: LaTeX, Greek letters, hyphens and punctuation drop out."""
    text = unicodedata.normalize('NFKC', _text(title))
    text = re.sub(r'\\(?:mathrm|mathit|mathbf|textrm|textit|textbf|text|emph|rm|it|bf)\b', ' ', text)
    text = re.sub(r'\\([A-Za-z]+)', r' \1 ', text)
    text = ''.join(f' {unicodedata.name(c).rsplit(" ", 1)[1].lower()} ' if unicodedata.name(c, '').startswith('GREEK') and c.isalpha() else c
                   for c in text)
    return re.findall(r'[a-z0-9]+', fold(text))


GREEK_NAMES = {unicodedata.name(chr(code)).rsplit(' ', 1)[1].lower() for code in range(0x3b1, 0x3ca)}


def _similar(a, b):
    """High token overlap, but a differing number or "I"/"II" marks another paper of a series."""
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    changed = [word for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != 'equal' for word in a[i1:i2] + b[j1:j2]]
    return matcher.ratio() >= 0.9 and not any(re.fullmatch(r'\d+|[ivx]+|[b-z]', word) for word in changed)


def same_title(a, b):
    """Equal up to typesetting, a trailing period, or a subtitle that only one source prints."""
    if _similar(_words(a), _words(b)):
        return True
    # Compare the part before the first colon only when it is long enough to be distinctive.
    heads = [_words(re.split(r'\s*[:\u2014]\s+', title, maxsplit=1)[0]) for title in (a, b)]
    return min(map(len, heads)) >= 4 and _similar(*heads)


MIN_SQUASHED_TITLE = 20


def title_in_header(title, header):
    """A title that line breaks or typesetting split differently still reads the same once spacing,
    case and punctuation are dropped. Short titles are too generic to find that way."""
    squashed = _squash(title)
    if len(squashed) < MIN_SQUASHED_TITLE:
        return False
    # "... gas. I" must not match inside "... gas. II": a title ending in a numeral or a letter
    # may not run on into one.
    open_end = re.search(r'(?:^|[^a-z0-9])(?:[ivx]+|\d+|[a-z])\W*$', fold(_text(title)))
    return re.search(re.escape(squashed) + (r'(?![ivx0-9])' if open_end else ''), _squash(header)) is not None


def title_agrees(record_title, candidates, header):
    return any(same_title(candidate, record_title) for candidate in candidates) or title_in_header(record_title, header)


def surname_in_header(surname, header, title):
    """The surname as a whole word of the first-page header, outside the record's own title.

    Only the record's title is removed: the candidate title lines often include the author line.
    A surname that is also a word of a title that cannot be located cleanly proves nothing.
    Small-caps author names often extract as "H ANSTEEN", so the glued reading is tried as well.
    """
    words = lambda value: ' ' + ' '.join(re.findall(r'[^\W\d_]+', fold(value))) + ' '
    name, own_title = words(surname), words(title)
    if not name.strip():
        return False
    for text in (header, re.sub(r'\b([A-Z]) (?=[A-Z]{2,}\b)', r'\1', header)):
        text = words(text)
        if own_title.strip() and own_title in text:
            text = text.replace(own_title, ' ')
        elif name in own_title:
            continue
        if name in text:
            return True
    return False


def _all_caps(text):
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and not any(c.islower() for c in letters)


def _restore_case(identity, spelling):
    """IOP deposits older ApJ titles in capitals, and no rule can tell an acronym from a word.

    The matched arXiv record keeps the authors' own case, so it replaces the title only when it
    spells exactly the same letters. Otherwise the title stays as deposited.
    """
    spelling, title = ' '.join(spelling.split()), identity.get('title') or ''
    if _all_caps(title) and not _all_caps(spelling) and _squash(spelling) == _squash(title):
        identity['title'] = spelling  # in place: the caller reports and stores this same identity


def _arxiv_id(ref):
    raw = ' '.join(str(ref.get(key) or '') for key in ('arxiv_id', 'title', 'raw'))
    match = re.search(r'(?i)(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)', raw)
    if match:
        return match.group(1)
    bare = str(ref.get('arxiv_id') or '')
    return bare if re.fullmatch(r'(?i)(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?', bare) else ''


def _arxiv_pdf(ref, cfg):
    arxiv_id = _arxiv_id(ref)
    doi = str(ref.get('doi') or '').strip()
    surname = str(ref.get('authors') or '').strip()
    cited = publication(ref.get('raw') or '', str(ref.get('year') or '')[:4])
    identity = ref.get('identity') or {}
    title = identity.get('title', '')
    year = int(identity.get('year') or (cited[3] if cited else 0)) or None
    ascii_surname = fold(surname).encode('ascii', 'ignore').decode()
    queries = []
    if arxiv_id:
        queries.append({'id_list': arxiv_id})
    else:
        if doi:
            queries.append({'search_query': 'all:"' + doi + '"', 'max_results': 20})
        if title and ascii_surname and _words(title):
            # A phrase would miss typesetting variants, so ask for the longest plain words instead.
            words = sorted({w for w in _words(title) if len(w) > 3 and w.isascii() and w not in GREEK_NAMES}, key=lambda w: (-len(w), w))[:6]
            queries.append({'search_query': ' AND '.join([f'au:"{ascii_surname}"', *(f'ti:{w}' for w in words)]), 'max_results': 20})
        elif ascii_surname and cited:
            # The journal reference is free text, so match the volume loosely and verify below.
            queries.append({'search_query': f'au:"{ascii_surname}" AND jr:"{cited[1]}"', 'max_results': 100})
    if not queries:
        return 'skipped', 'Needs a DOI, arXiv ID or identified title.', None
    ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
    status, message = 'not_found', 'No matching arXiv record found.'
    for params in queries:
        feed = ET.fromstring(_get('https://export.arxiv.org/api/query?' + urlencode(params), cfg))
        total = feed.findtext('{http://a9.com/-/spec/opensearch/1.1/}totalResults')
        if not arxiv_id and total and int(total) > 100:
            status, message = 'ambiguous', 'Too many arXiv results to choose from; search the web.'
            continue
        matches = {}
        for entry in feed.findall('atom:entry', ns):
            entry_id = entry.findtext('atom:id', default='', namespaces=ns).rsplit('/abs/', 1)[-1]
            first = fold(entry.findtext('atom:author/atom:name', default='', namespaces=ns))
            first_author = bool(surname and re.search(r'(?<!\w)' + re.escape(fold(surname)) + r'$', first))
            published = int(entry.findtext('atom:published', default='0000', namespaces=ns)[:4])
            by_id = bool(arxiv_id) and re.sub(r'v\d+$', '', entry_id.lower()) == re.sub(r'v\d+$', '', arxiv_id.lower())
            by_doi = bool(doi) and entry.findtext('arxiv:doi', default='', namespaces=ns).strip().casefold() == doi.casefold()
            by_title = bool(title) and first_author and year is not None and abs(published - year) <= 1 \
                and same_title(entry.findtext('atom:title', default='', namespaces=ns), title)
            journal_ref = entry.findtext('arxiv:journal_ref', default='', namespaces=ns)
            by_journal = bool(cited) and first_author and publication(journal_ref, cited[3]) == cited
            if by_id or by_doi or by_title or by_journal:
                matches[re.sub(r'v\d+$', '', entry_id)] = (entry_id, entry.findtext('atom:title', default='', namespaces=ns))
        if len(matches) == 1:
            entry_id, spelling = next(iter(matches.values()))
            _restore_case(identity, spelling)
            return 'found', 'Matching arXiv record found.', ['https://arxiv.org/pdf/' + quote(entry_id, safe='/')]
        if matches:
            status, message = 'ambiguous', 'Several arXiv records match; choose the PDF manually.'
    return status, message, None


REJECTED = 'Key rejected; check it in Settings.'
ADS_SEARCH = 'https://api.adsabs.harvard.edu/v1/search/query?'
# Free copies first: the author's preprint, ADS scans of old articles, then the publisher.
ADS_PDF_SOURCES = ('EPRINT_PDF', 'ADS_PDF', 'ADS_SCAN', 'AUTHOR_PDF', 'PUB_PDF')


def _https(urls):
    return [url for url in urls if url.startswith('https://')]


def _keyed_get(url, cfg, headers):
    """GET with credentials in headers; a refusal becomes a message that never echoes them."""
    try:
        return _get(url, cfg, headers)
    except HTTPError as error:
        if error.code in (401, 403):
            raise ValueError(REJECTED) from None
        raise


def _ads_search(cfg, query, fields):
    url = ADS_SEARCH + urlencode({'q': query, 'fl': fields, 'rows': 5})
    return json.loads(_keyed_get(url, cfg, {'Authorization': 'Bearer ' + cfg.key('ads')}))['response']['docs']


def identify_ads(ref, cfg):
    """Find a cited work in NASA ADS with the same strictness as Crossref: one agreeing record."""
    surname = (ref.get('authors') or '').replace('"', '')
    candidates = _candidates(ref)
    if not surname or not candidates:
        return 'needs_identification', 'No first author, volume and page in this reference.', None
    for year, volume, page, journal in candidates:
        journal = 'APJL' if journal == 'APJ' and page.startswith('L') else journal
        query = f'author:"^{surname}" year:{year} volume:{volume} page:{page}' + (f' bibstem:{journal_name(journal)}' if journal else '')
        docs = _ads_search(cfg, query, 'bibcode,doi,title,author,year,bibstem,volume,page,pub')
        agreeing = [doc for doc in docs
                    if str(doc.get('year')) == str(year) and str(doc.get('volume')) == volume
                    and page in {str(value).upper() for value in doc.get('page') or []}
                    and same_surname((doc.get('author') or [''])[0].split(',')[0], surname)
                    and (not journal or journal in {journal_code(stem) for stem in doc.get('bibstem') or []})]
        if len(agreeing) > 1:
            return 'ambiguous', 'Several ADS records match; choose the PDF manually.', None
        if agreeing:
            return 'found', 'ADS record agrees on year, volume, page and first author.', _ads_record(agreeing[0], year, volume, page, journal)
    return 'not_found', f'ADS has no record matching year {candidates[0][0]}, volume {candidates[0][1]}, page {candidates[0][2]} and first author {surname}.', None


def _ads_record(doc, year, volume, page, journal):
    names = [(author.split(',')[0], ' '.join(reversed([part.strip() for part in author.split(',', 1)]))) for author in doc.get('author') or []]
    # Collaboration papers list thousands of authors; the first ones identify the work.
    authors = ', '.join(full for _, full in names[:30]) + (' et al.' if len(names) > 30 else '')
    return {'provider': 'ads', 'doi': (doc.get('doi') or [''])[0].lower(), 'bibcode': doc['bibcode'],
            'title': _text((doc.get('title') or [''])[0]), 'authors': authors, 'first_author': names[0][0] if names else '',
            'journal': doc.get('pub') or '', 'year': int(year), 'publication': fingerprint(journal, volume, page, year),
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'evidence': {'year': year, 'volume': volume, 'page': page, 'first_author': names[0][0] if names else ''}}


def _ads_pdf(ref, cfg):
    bibcode, doi = (ref.get('identity') or {}).get('bibcode'), ref.get('doi')
    if not (bibcode or doi):
        return 'skipped', 'Needs a DOI or an ADS record.', None
    docs = _ads_search(cfg, f'bibcode:"{bibcode}"' if bibcode else f'doi:"{doi}"', 'bibcode,esources')
    if len(docs) > 1:
        return 'ambiguous', 'Several ADS records match; choose the PDF manually.', None
    sources = docs[0].get('esources') or [] if docs else []
    urls = [f'https://ui.adsabs.harvard.edu/link_gateway/{quote(docs[0]["bibcode"], safe="")}/{name}' for name in ADS_PDF_SOURCES if name in sources]
    if urls:
        return 'found', 'ADS lists a PDF.', urls
    return 'not_found', 'ADS lists no PDF.' if docs else 'ADS has no record for this work.', None


def _openalex_pdf(ref, cfg):
    doi = ref.get('doi')
    if not doi:
        return 'skipped', 'Needs a DOI.', None
    key = cfg.key('openalex')
    params = {'select': 'locations', **({'api_key': key} if key else {}), **({'mailto': cfg.email} if cfg.email else {})}
    try:
        info = json.loads(_get('https://api.openalex.org/works/doi:' + quote(doi, safe='/') + '?' + urlencode(params), cfg))
    except HTTPError as error:
        if error.code == 404:
            return 'not_found', 'OpenAlex does not know this DOI.', None
        if error.code in (401, 403):
            if key:
                raise ValueError(REJECTED) from None
            return 'not_configured', 'Add an OpenAlex key in Settings to use OpenAlex.', None
        raise
    locations = [place for place in info.get('locations') or [] if place.get('is_oa') and place.get('pdf_url')]
    # Repositories usually allow automated downloads; publisher sites often do not.
    locations.sort(key=lambda place: (place.get('source') or {}).get('type') != 'repository')
    urls = list(dict.fromkeys(place['pdf_url'] for place in locations))[:4]
    return ('found', 'Open-access PDF location found.', urls) if urls else ('not_found', 'OpenAlex lists no open PDF.', None)


def _semantic_scholar_pdf(ref, cfg):
    doi = ref.get('doi')
    if not doi:
        return 'skipped', 'Needs a DOI.', None
    key = cfg.key('semantic_scholar')
    try:
        url = 'https://api.semanticscholar.org/graph/v1/paper/DOI:' + quote(doi, safe='/') + '?fields=openAccessPdf,externalIds'
        info = json.loads(_get(url, cfg, {'x-api-key': key} if key else None))
    except HTTPError as error:
        if error.code == 404:
            return 'not_found', 'Semantic Scholar does not know this DOI.', None
        if error.code in (401, 403) and key:
            raise ValueError(REJECTED) from None
        raise  # 429 from the shared anonymous limit is temporary and reported as a failure
    arxiv_id = (info.get('externalIds') or {}).get('ArXiv')
    urls = _https([*(['https://arxiv.org/pdf/' + quote(arxiv_id, safe='/')] if arxiv_id else []), (info.get('openAccessPdf') or {}).get('url') or ''])
    return ('found', 'Open-access PDF found.', urls) if urls else ('not_found', 'Semantic Scholar lists no open PDF.', None)


def _core_pdf(ref, cfg):
    doi = ref.get('doi')
    if not doi:
        return 'skipped', 'Needs a DOI.', None
    url = 'https://api.core.ac.uk/v3/search/works?' + urlencode({'q': f'doi:"{doi}"', 'limit': 5})
    results = json.loads(_keyed_get(url, cfg, {'Authorization': 'Bearer ' + cfg.key('core')})).get('results') or []
    # The DOI must match exactly; repository copies of one work share it and are equally good.
    urls = _https(dict.fromkeys(r['downloadUrl'] for r in results if (r.get('doi') or '').lower() == doi.lower() and r.get('downloadUrl')))
    return ('found', 'Repository PDF found.', urls[:4]) if urls else ('not_found', 'CORE has no PDF for this DOI.', None)


def _unpaywall_pdf(ref, cfg):
    doi = ref.get('doi')
    if not doi:
        return 'skipped', 'Needs a DOI.', None
    try:
        info = json.loads(_get('https://api.unpaywall.org/v2/' + quote(doi, safe='/') + '?' + urlencode({'email': cfg.email}), cfg))
    except HTTPError as error:
        if error.code == 404:
            return 'not_found', 'Unpaywall does not know this DOI.', None
        raise
    places = [info.get('best_oa_location') or {}, *(info.get('oa_locations') or [])]
    urls = list(dict.fromkeys(place['url_for_pdf'] for place in places if place.get('url_for_pdf')))[:4]
    return ('found', 'Open-access PDF found.', urls) if urls else ('not_found', 'No PDF found by Unpaywall.', None)


def check(cfg):
    """One minimal live request per source that takes access data, to test it before a real search."""
    doi = '10.1086/186180'
    ads = ADS_SEARCH + urlencode({'q': f'doi:"{doi}"', 'fl': 'bibcode', 'rows': 1})
    core = 'https://api.core.ac.uk/v3/search/works?' + urlencode({'q': f'doi:"{doi}"', 'limit': 1})
    key = cfg.key('semantic_scholar')
    probes = {
        'ads': (ads, {'Authorization': 'Bearer ' + cfg.key('ads')}),
        'core': (core, {'Authorization': 'Bearer ' + cfg.key('core')}),
        'semantic_scholar': (f'https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=title', {'x-api-key': key} if key else None),
        'unpaywall': (f'https://api.unpaywall.org/v2/{doi}?' + urlencode({'email': cfg.email}), None),
        'openalex': (f'https://api.openalex.org/works/doi:{doi}?' + urlencode({'select': 'id', 'api_key': cfg.key('openalex')}), None)}
    results = {}
    for name, (url, headers) in probes.items():
        if not cfg.configured(name) or name == 'openalex' and not cfg.key('openalex'):
            continue
        if not cfg.enabled(name):
            results[name] = {'ok': False, 'message': 'Disabled in Settings.'}
            continue
        try:
            _get(url, cfg, headers, fresh=True)
            results[name] = {'ok': True, 'message': 'Accepted.'}
        except HTTPError as error:
            message = {401: 'Key rejected.', 403: 'Key rejected.', 422: 'Email rejected.', 429: 'Rate limit reached; try again later.'}
            results[name] = {'ok': False, 'message': message.get(error.code, f'HTTP {error.code}.')}
        except OSError as error:
            results[name] = {'ok': False, 'message': 'Network error: ' + (str(getattr(error, 'reason', error)) or type(error).__name__)}
    return results


class Mismatch(ValueError):
    """The PDF is readable but its first page does not name the expected first author."""


def _names_author(data, surname):
    """Whether the first two pages name the author; None when they hold no text to check (a scan)."""
    if not surname:
        return True
    squash = lambda text: re.sub(r'[\W_]', '', fold(text))  # also joins small caps such as "H OOD"
    try:
        text = squash(' '.join(page.extract_text() or '' for page in PdfReader(BytesIO(data)).pages[:2]))
    except Exception:
        return None
    return squash(surname) in text if len(text) >= 40 else None


def _fetch_pdf(urls, surname=None):
    """The first link that really is a PDF by `surname`: (bytes, True) or (bytes, None) when unreadable."""
    errors = []
    for url in urls:
        try:
            data = _download_pdf(url)
            if len(data) > MAX_PDF:
                raise ValueError('Download exceeds the size limit')
            if data.startswith(b'%PDF-'):
                names_author = _names_author(data, surname)
                if names_author is False:
                    raise Mismatch(surname)
                return data, names_author
            # Landing pages and captchas answer 200 with HTML; that is a refusal, not a broken link.
            head = data[:2048].lower().lstrip()
            if head.startswith(b'<') or b'<html' in head:
                raise Blocked('The link opens a web page, not a PDF.')
            raise ValueError('Download is not a PDF')
        except Blocked as blocked:
            blocked.url = url
            errors.append(blocked)
        except Exception as caught:
            errors.append(caught)
    # Only when every link was refused is the cause permanent; a timeout elsewhere may still pass.
    kinds = {type(error) for error in errors}
    raise errors[0] if len(kinds) == 1 and kinds <= {Blocked, Mismatch} else errors[-1]


def _failure(error):
    return f'HTTP {error.code}' if isinstance(error, HTTPError) else str(error) or type(error).__name__


def _disabled(name):
    return {'provider': name, 'status': 'skipped', 'message': f'{NAMES[name]} is disabled in Settings.'}


def _identity(ref, attempts, cfg):
    """Reuse a stored identity, read metadata of a given DOI, or search Crossref and then ADS."""
    if ref.get('identity'):
        return ref['identity']
    if ref.get('doi'):
        if not cfg.enabled('crossref'):
            attempts.append(_disabled('crossref'))
            return None
        try:
            record = crossref_metadata(ref['doi'], cfg)
        except Exception:
            return None  # DOI-based providers can still work without the metadata
        return {**record, 'provider': 'crossref', 'evidence': {'doi': 'given in the reference'},
                'checked_at': datetime.now(timezone.utc).isoformat()}
    status = 'skipped'
    if cfg.enabled('crossref'):
        try:
            status, message, identity = identify(ref, cfg)
        except Exception as error:
            status, message, identity = 'error', _failure(error), None
        attempts.append({'provider': 'crossref', 'status': status, 'message': message})
        if identity:
            return identity
    else:
        attempts.append(_disabled('crossref'))
    # An ambiguous Crossref answer needs the user; a second database must not overrule it.
    if status in ('skipped', 'not_found', 'error') and cfg.enabled('ads') and cfg.configured('ads'):
        try:
            status, message, identity = identify_ads(ref, cfg)
        except Exception as error:
            status, message, identity = 'error', _failure(error), None
        # A found record is reported by the ADS PDF step and the summary lead; the rest would only repeat Crossref.
        if status in ('error', 'ambiguous'):
            attempts.append({'provider': 'ads', 'status': status, 'message': message})
        return identity
    return None


NAMES = {'crossref': 'Crossref', 'ads': 'ADS', 'arxiv': 'arXiv', 'openalex': 'OpenAlex',
         'semantic_scholar': 'Semantic Scholar', 'core': 'CORE', 'unpaywall': 'Unpaywall'}
SETUP = {'ads': 'a NASA ADS token', 'core': 'a CORE API key', 'unpaywall': 'your email address'}
PROVIDERS = (('ads', _ads_pdf), ('arxiv', _arxiv_pdf), ('openalex', _openalex_pdf),
             ('semantic_scholar', _semantic_scholar_pdf), ('core', _core_pdf), ('unpaywall', _unpaywall_pdf))


def _join(words, last):
    return ', '.join(words[:-1]) + f' {last} ' + words[-1] if len(words) > 1 else ''.join(words)


def _summary(status, lead, attempts, provider=None):
    """One sentence that agrees with `status` and names only providers that really ran."""
    names = lambda wanted: [NAMES[a['provider']] for a in attempts if a['status'] in wanted and a['provider'] != 'crossref']
    if status == 'downloaded':
        text = f'Downloaded from {NAMES[provider]}.'
        if any(a.get('checked') is False for a in attempts):
            text += ' Its first page has no text, so the author could not be checked; review it.'
    elif status == 'local':
        text = 'Already in the library.'
    elif status == 'ambiguous':
        text = next(a['message'] for a in attempts if a['status'] == 'ambiguous')
    elif status == 'blocked':
        blocked = next(a for a in attempts if a['status'] == 'blocked')
        reason = re.sub(r' \(HTTP \d+\)', '', blocked['message'].rstrip('.'))
        text = (f'{NAMES[blocked["provider"]]} lists an open-access PDF, but {reason[0].lower() + reason[1:]}. '
                'Open it in your browser, then use Link local PDF.')
    elif status == 'not_identified':
        # The identification attempts come first; a disabled source says less than a real "no record".
        return 'Not identified. ' + next((a['message'] for a in attempts if a['status'] in ('needs_identification', 'not_found')), attempts[0]['message'])
    elif status == 'not_found':
        text = f'No open-access PDF on {_join(names({"not_found"}), "or")}.' if names({'not_found'}) else ''
        if names({'mismatch'}):
            # The file was fetched but is another paper, so it is reported, not imported.
            text = f'{text} The PDF from {_join(names({"mismatch"}), "and")} did not name the first author, so it was not imported.'.strip()
    else:
        parts = [f'{NAMES[a["provider"]]} found no match' if a['status'] == 'not_found' else f'{NAMES[a["provider"]]} returned another paper' if a['status'] == 'mismatch'
                 else f'{NAMES[a["provider"]]} failed ({a["message"]})'
                 for a in attempts if a['status'] in ('not_found', 'mismatch', 'error') and (a['provider'] != 'crossref' or a['status'] == 'error')]
        text = '; '.join(parts) + '. Try again later or search the web.'
    # Unconfigured providers are a setup fact the UI states once, not part of each result.
    return f'{lead} {text}'.strip()


def _result(status, lead, attempts, identity, **fields):
    summary = _summary(status, lead, attempts, fields.get('provider'))
    return {'version': 2, 'status': status, 'summary': summary, 'attempts': attempts, **fields,
            **({'identity': identity} if identity else {})}


def _lead(ref, identity, arxiv_id):
    doi = ref.get('doi')
    if identity and not (identity.get('evidence') or {}).get('doi'):
        what = f'DOI {doi}' if doi else f'bibcode {identity.get("bibcode")}'
        return f'Identified via {NAMES.get(identity["provider"], "Crossref")} ({what}).'
    if doi:
        return f'Using DOI {doi}.'
    return f'Using arXiv ID {arxiv_id}.' if arxiv_id else ''


def download(library, ref, progress=None, settings=None):
    """Search legitimate open-access sources in a fixed order and import the first PDF found.

    A library proxy is deliberately never used here: institutional licences forbid
    systematic automated downloading, so the proxy only builds links the user clicks.
    """
    cfg = settings or Settings()
    if ref.get('local_document_id'):
        try:
            if not library.get_path(ref['local_document_id']).is_file():
                raise FileNotFoundError
            return _result('local', '', [], None, document_id=ref['local_document_id'])
        except (FileNotFoundError, KeyError):
            pass
    attempts = []
    if progress:
        progress('crossref')
    identity = _identity(ref, attempts, cfg)
    if identity:
        ref = {**ref, 'doi': ref.get('doi') or identity['doi'], 'identity': identity}
    lead = _lead(ref, identity, _arxiv_id(ref))
    identified = bool(lead)
    if identity and ref['doi']:
        for document in (library.find_by_doi(ref['doi']) if library else []):
            try:
                library.get_path(document['id'])
            except (FileNotFoundError, KeyError):
                continue
            return _result('local', lead, attempts, identity, document_id=document['id'])
    for name, lookup in PROVIDERS:
        if progress:
            progress(name)
        if any(a['provider'] == name for a in attempts):
            continue  # identification already reported an ADS error or ambiguity
        if not cfg.enabled(name):
            attempts.append(_disabled(name))
            continue
        if not cfg.configured(name):
            attempts.append({'provider': name, 'status': 'not_configured', 'message': f'Add {SETUP[name]} in Settings to use {NAMES[name]}.'})
            continue
        link = {}
        try:
            status, message, urls = lookup(ref, cfg)
            if urls:
                surname = ref.get('authors') or (identity or {}).get('first_author')
                data, checked = _fetch_pdf(urls, surname)
                filename = pdf_name(surname, (identity or {}).get('year') or str(ref.get('year') or '')[:4],
                                    (identity or {}).get('publication')) if identity and surname else 'download.pdf'
                document = library.import_stream(BytesIO(data), filename)
                # A scan has no text to compare, so it stays unverified and reviewable.
                if identity and checked:
                    library.apply_verified(document['id'], identity, filename)
                attempts.append({'provider': name, 'status': 'downloaded', 'message': message, **({} if checked else {'checked': False})})
                return _result('downloaded', lead, attempts, identity, document_id=document['id'], provider=name)
        except Exception as error:
            status, message = 'error', _failure(error)
            if isinstance(error, Blocked):
                status, link = 'blocked', {'url': error.url}
            if isinstance(error, Mismatch):
                status, message = 'mismatch', f'The PDF from {NAMES[name]} does not name {error} on its first page.'
        attempts.append({'provider': name, 'status': status, 'message': message, **link})
    statuses = {attempt['status'] for attempt in attempts}
    # Ambiguity needs the user; a blocked link needs the browser; a failed provider makes "not found" inconclusive.
    status = 'ambiguous' if 'ambiguous' in statuses else 'blocked' if 'blocked' in statuses \
        else 'failed' if 'error' in statuses else 'not_found' if identified else 'not_identified'
    pdf_url = next((a['url'] for a in attempts if a['status'] == 'blocked'), None)
    return _result(status, lead or ('Not identified.' if status == 'failed' else ''), attempts, identity,
                   **({'pdf_url': pdf_url} if pdf_url else {}))
