"""Conservative, per-file bibliography extraction and citation counts."""

import json
import math
import re
import statistics
import unicodedata
from contextlib import contextmanager
import sqlite3

from pypdf import PdfReader

from paperassist.citation import SURNAME, describe, fold, pdf_name, publication as _publication, surnames
from paperassist.library import manual_fields
from paperassist.retrieval import identify, search_urls

DOI = re.compile(r'10\.\d{4,9}/[-._;()/:A-Z0-9]+', re.I)
HEADING = re.compile(r'^\s*(?:references|bibliography)\s*$', re.I | re.M)
ENTRY = re.compile(r'^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$')
AUTHOR_ENTRY = re.compile(rf'^\s*({SURNAME}),\s*(?:[^\W\d_]\s?\.|[^\W\d_]+\s*,|[JS]r\.)')
# "Hood A. W., Browning P. K." has no comma after the surname; accepted only once the previous
# citation has ended, because the same shape also appears inside a wrapped author list.
INITIALS_ENTRY = re.compile(r"^\s*([^\W\d_]+(?:[-'’][^\W\d_]+)*),?\s(?:[A-Z][a-z]?\.\s?){1,3}(?:,|\s&|\sand\b)")
# Text extraction sometimes inserts a space inside a year ("200 8") or after "[".
YEAR = re.compile(r'\b(?:19|20)\d\s?\d[a-z]?\b')
MARKER = re.compile(r'\[\s*(\d+(?:\s*[,;–−-]\s*\d+)*)\s*\]')
JOINED_ENTRIES = re.compile(r"(?<=\d)\s+(?=[A-Z][^\W\d_]+(?:[-'’][^\W\d_]+)*,\s[A-Z]\.|[.;]\s*(?:1[89]|20)\d\d)")
IBID = re.compile(r'^[.;:—–_-]{1,8}\s*[.,]?\s*((?:1[89]|20)\d\d[a-z]?\b.*)')
ENTRY_LIKE = re.compile(r"[A-Z][^\W\d_]+,\s[A-Z]\..*\b(?:1[89]|20)\d\d\b")
# A second author may carry particles ("Rouppe van der Voort") but never a whole phrase,
# so prose after "Smith &" cannot be mistaken for a name.
PARTICLE = r'(?:van|von|de|del|der|den|di|da|dos|ter|la|le)'
# A few trailing letters after a space are a kerning gap ("Spi cer"), not another word.
SECOND_AUTHOR = rf"(?:[^\W\d_]+\s+(?={PARTICLE}\s))?(?:{PARTICLE}\s+)*[^\W\d_]+(?:[-'’][^\W\d_]+)*(?:\s[a-z]{{2,4}}(?![a-z]))?"
# "B" in "A & B 2001" is a second author, not a citation of B.
AFTER_NAME = re.compile(r'[^\W\d_]{2}\s*(?:&|\band)\s+$', re.I)
SPACING_ACCENT = re.compile(r'\s*([´¨˜ˆˇ˚¸])\s*([A-Za-zı])')
COMBINING = {'´': '\u0301', '¨': '\u0308', '˜': '\u0303', 'ˆ': '\u0302', 'ˇ': '\u030c', '˚': '\u030a', '¸': '\u0327'}
DESTINATION_VERSION = 5
# Sections printed after the references. Bare words must fill the line, so a wrapped title such as
# "Table of integrals" stays in its entry.
END_HEADING = re.compile(
    r'\s*(?:\d+\.?\s+)?(?:(?:figure|table)s?(?:\s+(?:captions?|legends?))?|captions?|list\s+of\s+(?:figures|tables)'
    r'|acronyms|abbreviations|glossary|nomenclature|data\s+availability|author\s+contributions?|funding'
    r'|conflicts?\s+of\s+interests?)\s*[:.]?\s*', re.I)
END_START = re.compile(r'\s*(?:appendi(?:x|ces)\b|supplement(?:ary|al)\b|supporting\s+information|acknowledg)', re.I)
# Preprints and emulateapj papers float figures and tables between reference pages, so a caption
# only closes the entry before it; the list resumes at the next entry.
FLOAT = re.compile(r'\s*(?:fig(?:ure)?|table)s?\.?\s*(?:\d|[IVX]+\b)', re.I)
# A journal reference ends a citation: "ApJ, 745, 152", "Phys. Rev. Lett. 99, 1 (2007)" or, in year-last
# styles, "Journal 4, 2020". Numbers tolerate a kerning gap ("73 4"); "AR 11158, 11159" is not a journal.
_NUMBER = r'[A-Z]?\d(?:\s?\d){0,5}'
_JOURNAL_REF = (rf'(?<!\w)(?!(?:AR|NOAA|NGC|Fig|Table|Eq)\b)[A-Z][\w.&]*\.?,?\s*(?:[Vv]ol\.?\s*)?{_NUMBER}\s*(?:\(\d+\))?[,:]\s*'
                rf'(?:p{{1,2}}\.\s*|id\.\s*)?{_NUMBER}(?:\s?[-–−]\s?(?:{_NUMBER}|\+))?')
_STATUS = (r'(?:submitted(?:\s+to\s+[\w. &]{1,40})?|in\s+press|in\s+prep(?:aration)?|to\s+appear|accepted|unpublished'
           r'|priv(?:ate|\.)\s+comm(?:unication|\.))')
_BOOK = (r'(?:(?:thesis|dissertation)\b[^.]{0,80}|(?:Press|Springer|Wiley|Elsevier|Kluwer|Reidel|Dover|Pergamon|Verlag'
         r'|Publish\w*|Books)\b[^.]{0,25})')
# Conference papers and book chapters end on a page: "(San Francisco: ASP), 235" or "..., 235–+".
_PAGES = r'(?:\)\s*,?\s*(?:pp?\.\s*)?\d+|[,)]\s*(?:pp?\.\s*)?\d+\s?[-–−]\s?(?:\d+|\+))'
_TAIL = (r'(?:\s*\(\d+\s?pp\)|\s*\[[\w.-]+\]|\s*[\[(]?(?i:arXiv|doi)[^\])]*[\])]?|\s*\(?(?:19|20)\d\d[a-z]?\)?|[\s.,;:)\]]'
         r'|,?\s*pp?\.\s*\d+(?:\s?[-–−]\s?\d+)?)*')
CITATION_END = re.compile(rf'(?:(?P<strong>{_JOURNAL_REF}|(?i:arXiv:\s*[\w./-]+|{DOI.pattern}))|(?i:{_STATUS}|{_BOOK}|{_PAGES})){_TAIL}')
ENDS_ENTRY = re.compile(CITATION_END.pattern + r'$')
# What may still follow a finished citation: wrapped URLs and DOIs, page ranges, parenthetical notes.
CONTINUATION = re.compile(r'(?:[-–−]\s?\d+[.,;)]?$|\d+\s?[-–−]\s?\d+[.,;)]?$|[(\[]|\S+/\S+$|10\.\d{4}'
                          r'|(?:arXiv|doi|https?:|www\.|erratum|errata|addendum|isbn|issn|url)\b)', re.I)
# A bibliography page made mostly of short lines holds axis ticks and labels, not entries.
SHORT_LINE, MIN_LINES = 16, 8
# An entry this many times longer than the median entry is suspect, however long real author lists are.
OUTLIER = 6
EXTRACTION_VERSION = 3


@contextmanager
def _connection(library):
    db = sqlite3.connect(library.db_path)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS reference_analyses (source_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            yield db
    finally:
        db.close()


def _numbers(marker):
    result = set()
    for part in re.split(r'\s*[,;]\s*', marker):
        match = re.fullmatch(r'(\d+)\s*[–−-]\s*(\d+)', part)
        if match:
            a, b = map(int, match.groups())
            if a <= b and b - a <= 100:
                result.update(range(a, b + 1))
        elif part.isdigit():
            result.add(int(part))
    return result


def _clean(text):
    """Repair what PDF text extraction breaks: detached accents ("M¨ uller"), ligatures and
    names hyphenated at a line end."""
    joined = SPACING_ACCENT.sub(lambda m: m.group(2).replace('ı', 'i') + COMBINING[m.group(1)], text)
    return re.sub(r'(?<=[^\W\d_])-\n(?=[a-z])', '', unicodedata.normalize('NFKC', joined))


def _citation_end(pattern, raw):
    """First match of a citation-ending cue in the text, or None.

    Status, publisher and page cues count only after the year: "Springer" or "Press" can be a
    surname in the author list.
    """
    year = YEAR.search(raw)
    if year:
        return next((m for m in pattern.finditer(raw) if m['strong'] or m.start() >= year.start()), None)


def _name_start(line):
    """Surnames start with a capital or a particle ("de Groof"); prose such as "appearance, i.e." does not."""
    return line[:1].isupper() or bool(re.match(PARTICLE + r'[\s-]', line))


def _ended(raw):
    """Whether the text stops at the end of a citation, so what follows is no longer part of it."""
    return bool(_citation_end(ENDS_ENTRY, raw.rstrip()))


def _continues(raw, line):
    """Whether a line can still belong to an entry whose citation has ended."""
    return raw.endswith(('-', '/', ',', ';', ':')) or bool(CONTINUATION.match(line))


def _ends_bibliography(line):
    return bool(END_HEADING.fullmatch(line) or END_START.match(line))


def _figure_page(text):
    """A page of axis ticks and labels: most of its lines are very short."""
    # Page numbers such as "– 21 –" say nothing about the layout.
    lines = [line for line in map(str.strip, text.splitlines()) if line and not re.fullmatch(r'[–-]?\s*\d+\s*[–-]?', line)]
    return len(lines) >= MIN_LINES and sum(len(line) < SHORT_LINE for line in lines) * 2 >= len(lines)


def _split_entries(lines):
    """Group bibliography lines into entries; also return the lines before the first entry.

    Each entry is open until its citation ends. A line that is neither a continuation nor a new
    entry then closes it, so running headers and whatever follows the list stay out of it.
    """
    entries, leading, state = [], [], 'open'
    # Scanned two-column pages can run several entries together on one line.
    for line in (part for line in lines for part in JOINED_ENTRIES.split(line)):
        line = line.strip()
        numeric = ENTRY.match(line)
        author = _name_start(line) and AUTHOR_ENTRY.match(line)
        if entries and _ends_bibliography(line):
            break
        if entries and FLOAT.match(line):
            state = 'closed'
            continue
        if ibid := IBID.match(line):
            if entries:  # "———. 1988, ..." repeats the previous author list
                entries.append({'label': '', 'raw': YEAR.split(entries[-1]['raw'], maxsplit=1)[0] + ibid.group(1)})
                state = 'done' if _ended(entries[-1]['raw']) else 'open'
        elif numeric:
            entries.append({'label': numeric.group(1) or numeric.group(2), 'raw': numeric.group(3)})
            state = 'done' if _ended(entries[-1]['raw']) else 'open'
        elif author and (not entries or YEAR.search(entries[-1]['raw'])):
            entries.append({'label': author.group(1), 'raw': line})
            state = 'done' if _ended(line) else 'open'
        elif entries and state != 'open' and (initials := _name_start(line) and INITIALS_ENTRY.match(line)):
            entries.append({'label': initials.group(1), 'raw': line})
            state = 'done' if _ended(line) else 'open'
        elif entries and line:
            if state == 'done' and not _continues(entries[-1]['raw'], line):
                state = 'closed'
            if state != 'closed':
                entries[-1]['raw'] += ' ' + line
                state = 'done' if state == 'done' or _ended(entries[-1]['raw']) else 'open'
        elif line:
            leading.append(line)
    return entries, leading


def _trim_outliers(entries):
    """Cut an entry far longer than its list's typical one at the end of its first citation.

    Entries that never state a citation end cannot be cut and stay as they are.
    """
    if len(entries) < 5:
        return
    limit = OUTLIER * statistics.median(len(entry['raw']) for entry in entries)
    for entry in entries:
        raw = entry['raw']
        end = len(raw) > limit and _citation_end(CITATION_END, raw)
        if end and raw[end.end():].strip():
            entry.update(raw=raw[:end.end()].rstrip(), truncated=True)


def _citation(author, year):
    """Regex for citations of one author-year entry.

    Accepts "A 2001", "A et al. (2001)", "A & B, 2001", earlier years of the same
    group ("A 1999, 2001") and later suffixes ("A 2001a,b"). Intervening text is
    limited to citation grammar, not arbitrary prose.
    """
    base, suffix = year[:4], year[4:]
    # "2001a,b" cites both suffixed entries, so b is also found after an earlier suffix.
    year_part = base + (rf'(?:{suffix}|[a-z](?:\s*,\s*[a-z])*?\s*,\s*{suffix})' if suffix else '')
    # A large kerning gap after the first capital reads as a space ("W atanabe").
    name = re.escape(author).replace(r'\-', r'-\s*').replace(r'\ ', r'\s+')
    name = name[0] + r'\s?' + name[1:]
    return re.compile(rf'\b{name}\b(?:(?P<more>\s+et\s+al\.?)|\s*(?:and|&)\s+(?P<second>{SECOND_AUTHOR}))?'
                      rf'\s*[,\(]?\s*(?:(?:19|20)\d\d[a-z]?\s*,\s*)*{year_part}(?![A-Za-z0-9])', re.I)


def _fits(count, second, citation):
    """Whether an entry with this many authors and second author can be that citation."""
    if citation['more']:
        return count >= 3
    if citation['second']:
        return count == 2 and second == fold(citation['second'])
    return count == 1


def _share(group, found):
    """Share citations between entries with the same first author and year.

    "Smith & Jones 2001" belongs to the two-author entry and "Smith et al. 2001" to an
    entry with three or more authors. A citation that fits none or several entries makes
    all of them ambiguous rather than guessing.
    """
    authors = []
    for entry in group:
        names = surnames(entry['raw'])
        # "Smith, A., et al." lists one name but stands for three or more authors.
        count = max(len(names), 3) if re.search(r'\bet\s+al\b', re.split(YEAR, entry['raw'], maxsplit=1)[0]) else len(names)
        authors.append((count, fold(names[1]) if len(names) > 1 else None))
    owned, ambiguous = [[] for _ in group], set()
    # Every member found the same text, so one member's matches stand for all.
    for page, m in found[0]:
        fits = [i for i, (count, second) in enumerate(authors) if _fits(count, second, m)]
        if len(fits) == 1:
            owned[fits[0]].append((page, m))
        else:
            ambiguous.update(fits or range(len(group)))
    return owned, ambiguous


def extract(pages):
    """Return detected references and occurrences; coverage is always partial."""
    pages = [_clean(page) for page in pages]
    heading = next(((i, m) for i, page in enumerate(pages) if (m := HEADING.search(page))), None)
    if not heading:
        return []
    page_index, match = heading
    # Scanned two-column pages can print the left column of entries before the heading.
    above = pages[page_index][:match.start()].splitlines()
    top = len(above)
    while top and ENTRY_LIKE.search(above[top - 1]):
        top -= 1
    later = [page for page in pages[page_index + 1:] if not _figure_page(page)]
    bibliography = '\n'.join([*above[top:], pages[page_index][match.end():], *later])
    entries, leading = _split_entries(bibliography.splitlines())
    _trim_outliers(entries)
    # In two-column layouts a mid-page heading can precede the rest of the body text.
    body = pages[:page_index] + ['\n'.join([*above[:top], *leading])]
    found = []
    for index, entry in enumerate(entries):
        raw = entry['raw']
        doi = DOI.search(raw)
        year = YEAR.search(raw)
        year_text = re.sub(r'\s', '', year.group()) if year else ''
        author = AUTHOR_ENTRY.match(raw) or INITIALS_ENTRY.match(raw)
        surname = re.sub(r'\b([A-Z])\s(?=[a-z]{2})', r'\1', author.group(1)) if author else ''
        numeric = entry['label'].isdigit()
        if not numeric and author and year:
            entry['label'] = f'{surname} {year_text}'
        if numeric:
            find = lambda text, n=int(entry['label']): [m for m in MARKER.finditer(text) if n in _numbers(m.group(1))]
        elif author and year:
            pattern = _citation(surname, year_text)
            find = lambda text: [m for m in pattern.finditer(text) if not AFTER_NAME.search(text[max(0, m.start() - 30):m.start()])]
        else:
            find = lambda text: []
        found.append([(page, m) for page, text in enumerate(body, 1) for m in find(text)])
        entry.update(id=str(index), title=raw, authors=surname,
                     year=year_text, doi=doi.group().rstrip('.,;') if doi else '',
                     count_status='partial', local_document_id=None)
        entry['search_urls'] = search_urls(entry)
    homonyms = {}
    for i, entry in enumerate(entries):
        if not entry['label'].isdigit():
            homonyms.setdefault(entry['label'].lower(), []).append(i)
    ambiguous = set()
    for members in homonyms.values():
        if len(members) > 1:
            owned, unclear = _share([entries[i] for i in members], [found[i] for i in members])
            for i, hits in zip(members, owned):
                found[i] = hits
            ambiguous.update(members[j] for j in unclear)
    for i, entry in enumerate(entries):
        entry['occurrences'] = [{'page': page, 'context': body[page - 1][max(0, m.start()-60):m.end()+60], 'marker': m.group()}
                                for page, m in found[i]]
        entry['citation_count'] = len(entry['occurrences'])
        if i in ambiguous:
            entry.update(citation_count=None, count_status='ambiguous', occurrences=[])
    return entries


def analyze(library, document_id):
    path = library.get_path(document_id, annotated=False)
    reader = PdfReader(str(path))
    pages = [(page.extract_text() or '') for page in reader.pages]
    refs = extract(pages)
    map_destinations(reader, pages, refs)
    previous = _load(library, document_id).get('references', [])
    preserved = [r for r in previous if r.get('manual_link') or r.get('retrieval') or r.get('identity')]
    claimed = set()
    documents = library.list_documents()
    for ref in refs:
        raw = _normalize(ref['raw'])
        matches = []
        for i, old in enumerate(preserved):
            if i in claimed:
                continue
            old_raw = _normalize(old['raw'])
            if old_raw == raw and raw:
                matches.append(i)
                continue
            # An older parser may have merged the next entry into this one.
            same_author = old.get('authors') and _normalize(ref.get('authors', '')).startswith(
                _normalize(old['authors']))
            unique_prefix = sum(old_raw.startswith(_normalize(item['raw'])) for item in refs) == 1
            if (len(raw) >= 12 and old_raw.startswith(raw) and same_author
                    and old.get('year') == ref.get('year') and unique_prefix):
                matches.append(i)
        old = preserved[matches[0]] if len(matches) == 1 else {}
        if old:
            claimed.add(matches[0])
        if state := old.get('retrieval'):
            ref['retrieval'], ref['status'] = state, state['status']
        if identity := old.get('identity'):
            ref['identity'], ref['doi'] = identity, ref['doi'] or identity['doi']
        linked = (old.get('manual_target_id') or old.get('local_document_id')) if old.get('manual_link') else None
        if linked:
            ref['local_document_id'], ref['manual_link'] = linked, True
        elif ref['doi']:
            matches = [d for d in documents if (d.get('doi') or '').lower() == ref['doi'].lower() and str(d['id']) != str(document_id)]
            if len(matches) == 1:
                ref['local_document_id'] = matches[0]['id']
    for i, old in enumerate(preserved):
        if i not in claimed:
            refs.append({**old, 'id': f'legacy-{i}', 'destinations': [], 'occurrences': [],
                         'citation_count': None, 'count_status': 'ambiguous', 'legacy': True,
                         'status': 'Previous reference — verify link'})
    payload = {'references': sorted(refs, key=lambda r: -(r['citation_count'] if r['citation_count'] is not None else -1)), 'status': 'partial', 'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION,
               'message': 'Detected citations only; bibliography and counts may be incomplete.'}
    _save(library, document_id, payload)
    return _resolve(library, document_id, payload, documents)


def _normalize(text):
    # Entries saved before accents were repaired must still match their new text.
    return ''.join(c for c in _clean(text).casefold() if c.isalnum())


def map_destinations(reader, pages, refs):
    """Match bibliography destinations by geometry, entry text and optional numeric label."""
    for ref in refs:
        ref['destinations'] = []
    heading = next((i for i, page in enumerate(pages) if HEADING.search(page)), None)
    if heading is None:
        return
    destinations = {}
    for name, dest in reader.named_destinations.items():
        try:
            page = reader.get_destination_page_number(dest)
            if page >= heading and dest.typ == '/XYZ' and dest.left is not None and dest.top is not None:
                destinations.setdefault(page, []).append((name, float(dest.left), float(dest.top)))
        except (TypeError, ValueError, AttributeError):
            continue
    entries = [(_normalize(ref['raw']), ref) for ref in refs]
    for page_number, targets in destinations.items():
        lines = []
        def visit(text, cm, tm, font, size):
            if not text.strip():
                return
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            height = abs(size) * math.hypot(tm[2] * cm[0] + tm[3] * cm[2],
                                            tm[2] * cm[1] + tm[3] * cm[3])
            if height:
                lines.append((x, y, y + height, text.splitlines()[0]))
        reader.pages[page_number].extract_text(visitor_text=visit)
        for name, left, top in targets:
            nearby = [(baseline, text) for x, baseline, y, text in lines
                      if abs(x - left) <= 3 and -1 <= top - y <= 4]
            if len(nearby) == 1:
                baseline = nearby[0][0]
                fragments = sorted((x, text) for x, y, _, text in lines
                                   if abs(y - baseline) < 0.5 and left - 3 <= x < left + 240)
                line = ''.join(text for _, text in fragments)
                numbered = ENTRY.match(line)
                label = (numbered[1] or numbered[2]) if numbered else None
                line = _normalize(numbered[3] if numbered else line)
                # A short line could start several entries; a short complete entry cannot.
                matches = [ref for raw, ref in entries if raw.startswith(line) and (len(line) >= 20 or raw == line)
                           and (label is None or str(ref.get('label')) == label)]
                if len(matches) == 1:
                    matches[0]['destinations'].append(name)



def local_candidates(ref, documents, source_id, evidence=None):
    """Suggest possible matches without silently merging different works.

    `evidence` maps a document to its publication evidence; a document whose own
    journal, volume, page or year contradicts the reference is never suggested.
    """
    raw_words = set(re.findall(r'[a-z0-9]+', ref['raw'].lower()))
    names = [fold(name) for name in surnames(ref['raw'])]
    author = fold(ref.get('authors', ''))
    year = (ref.get('year') or '')[:4]
    candidates = []
    for document in documents:
        if str(document['id']) == str(source_id):
            continue
        title_words = set(re.findall(r'[a-z0-9]+', (document.get('title') or '').lower()))
        title_words = {word for word in title_words if len(word) > 3}
        doc_authors = fold(document.get('authors') or '')
        def position(name):
            match = re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', doc_authors)
            return match.start() if match else None
        title_match = len(title_words) >= 3 and len(title_words & raw_words) / len(title_words) >= .8
        positions = [pos for name in dict.fromkeys(names) if (pos := position(name)) is not None]
        author_match = len(positions) >= 2 and author and position(author) is not None and positions == sorted(positions)
        if str(document.get('year') or '') not in ('', year) and year:
            continue
        if not (title_match or author_match):
            continue
        if author and doc_authors and position(author) is None:
            continue
        if _conflict(ref, document, evidence(document) if evidence else None):
            continue
        years = {int(value) for value in document.get('metadata', {}).get('candidates', {}).get('year', [])}
        # A paper almost always prints its own year on the first page.
        if years and year and not any(abs(value - int(year)) <= 1 for value in years):
            continue
        candidates.append(((bool(author) and position(author) == 0, int(year or 0) in years),
                           {'id': document['id'], 'title': document.get('title'),
                            'authors': document.get('authors'), 'year': document.get('year')}))
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    return [candidate for _, candidate in candidates[:5]]


def _conflict(ref, document, evidence):
    """Short reason why the document cannot be the cited work, or '' when none is known."""
    cited = _publication(ref['raw'], ref.get('year', ''))
    found = (evidence or {}).get('publication')
    if cited and found and (cited[:3] != found[:3] or abs(int(cited[3]) - int(found[3])) > 1):
        return f'Linked PDF is {describe(found)}; reference is {describe(cited)}'
    doi, own = (ref.get('doi') or '').lower(), (document.get('doi') or '').lower()
    if doi and own and doi != own:
        return f'Linked PDF has DOI {own}; reference has DOI {doi}'
    year = (ref.get('year') or '')[:4]
    if year and document.get('year') and abs(int(document['year']) - int(year)) > 1:
        return f'Linked PDF is from {document["year"]}; reference is from {year}'
    return ''


def _save(library, document_id, payload):
    with _connection(library) as db:
        db.execute('INSERT OR REPLACE INTO reference_analyses VALUES (?, ?)', (str(document_id), json.dumps(payload)))


def _load(library, document_id):
    with _connection(library) as db:
        row = db.execute('SELECT payload FROM reference_analyses WHERE source_id = ?', (str(document_id),)).fetchone()
    if not row:
        return {'references': [], 'status': 'not_analyzed'}
    return json.loads(row[0])


def _available(library, document_id):
    try:
        library.get_path(document_id)
        return True
    except (KeyError, FileNotFoundError, OSError):
        return False


def _publication_evidence(library, document):
    sha = document.get('sha256')
    if not sha:
        return None
    with _connection(library) as db:
        db.execute('CREATE TABLE IF NOT EXISTS publication_evidence_v4 (sha TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        row = db.execute('SELECT payload FROM publication_evidence_v4 WHERE sha = ?', (sha,)).fetchone()
        if row:
            return json.loads(row[0])
        try:
            path = library.get_path(document['id'], annotated=False)
            text = PdfReader(str(path)).pages[0].extract_text() or ''
            lines = text.splitlines()
            evidence = {'publication': _publication('\n'.join(lines[:6])), 'lines': lines[:15]}
        except Exception:
            return None
        db.execute('INSERT OR REPLACE INTO publication_evidence_v4 VALUES (?, ?)', (sha, json.dumps(evidence)))
        return evidence


def _resolve(library, document_id, payload, documents):
    with _connection(library) as db:
        rows = db.execute('SELECT source_id, payload FROM reference_analyses WHERE source_id != ?', (str(document_id),)).fetchall()
    shared = {}
    for source_id, saved_payload in rows:
        for old in json.loads(saved_payload).get('references', []):
            target = old.get('manual_target_id') or old.get('local_document_id')
            if (old.get('manual_link') and not old.get('legacy') and not old.get('local_invalid')
                    and target and str(target) != source_id):
                shared.setdefault(_normalize(old['raw']), set()).add(str(target))
    available = {str(d['id']): d for d in documents if _available(library, d['id'])}
    evidence_cache = {}

    def evidence_for(d):
        if d['id'] not in evidence_cache:
            found = _publication_evidence(library, d)
            # PDFs without a printed journal header (preprints) keep the verified citation.
            verified = d.get('metadata', {}).get('verified', {}).get('publication')
            if verified and not (found and found['publication']):
                found = {'lines': (found or {}).get('lines', []), 'publication': verified}
            evidence_cache[d['id']] = found
        return evidence_cache[d['id']]

    for ref in payload['references']:
        ref['local_candidates'] = local_candidates(ref, documents, document_id, evidence_for)
        for key in ('local_match', 'local_missing', 'local_invalid', 'local_conflict'):
            ref.pop(key, None)
        if ref.get('manual_link'):
            target = ref.get('manual_target_id') or ref.get('local_document_id')
            ref['manual_target_id'] = target
            ref['local_invalid'] = str(target) == str(document_id)
            ref['local_document_id'] = target if str(target) in available and not ref['local_invalid'] else None
            ref['local_missing'] = str(target) not in available and not ref['local_invalid']
            ref['local_match'] = 'manual'
            if ref['local_document_id'] and (reason := _conflict(ref, available[str(target)], evidence_for(available[str(target)]))):
                ref['local_conflict'] = reason  # flagged for review; the user's link is kept
            continue
        ref['local_document_id'] = None
        possible = [d for d in available.values() if str(d['id']) != str(document_id)]
        matches = [d for d in possible if ref.get('doi') and (d.get('doi') or '').lower() == ref['doi'].lower()]
        reason = 'doi'
        if not matches and ref.get('doi'):
            matches = [d for d in possible if _first_page_doi(ref, d, evidence_for)]
            reason = 'first_page_doi'
        if not matches:
            targets = shared.get(_normalize(ref['raw']), set())
            matches = [d for d in possible if str(d['id']) in targets] if len(targets) == 1 else []
            reason = 'shared_manual'
        else:
            targets = set()
        if not matches and len(targets) <= 1 and (fingerprint := _publication(ref['raw'], ref.get('year', ''))):
            author = fold(ref.get('authors', ''))
            matches = []
            for d in possible:
                if not author:
                    continue
                evidence = evidence_for(d)
                if not evidence or evidence['publication'] != fingerprint:
                    continue
                authors = fold(d.get('authors') or '')
                in_metadata = re.search(r'(?<!\w)' + re.escape(author) + r'(?!\w)', authors)
                # Authors the user typed outrank the printed header; extracted ones do not.
                if 'authors' in manual_fields(d) and authors and not in_metadata:
                    continue
                if d.get('year') and str(d['year']) != fingerprint[3]:
                    continue
                first_author = re.compile(r'^\s*(?:[A-Z]\.\s*)+' + re.escape(author) + r'(?![^\W\d_])', re.I)
                in_header = any(first_author.search(unicodedata.normalize('NFKC', line)) for line in evidence['lines'])
                if in_metadata or in_header:
                    matches.append(d)
            reason = 'publication'
        if len(matches) == 1:
            ref['local_document_id'], ref['local_match'] = matches[0]['id'], reason
    return payload


def _first_page_doi(ref, document, evidence_for):
    """The reference's DOI is printed on the PDF's first page and nothing else disagrees."""
    printed = {value.lower() for value in document.get('metadata', {}).get('candidates', {}).get('doi', [])}
    if ref['doi'].lower() not in printed:
        return False
    evidence = evidence_for(document)
    author = fold(ref.get('authors', ''))
    seen = fold(f"{document.get('authors') or ''} {' '.join((evidence or {}).get('lines', []))}")
    return bool(author) and not _conflict(ref, document, evidence) and bool(re.search(r'(?<!\w)' + re.escape(author) + r'(?!\w)', seen))


def get(library, document_id):
    payload = _load(library, document_id)
    if payload['status'] == 'not_analyzed':
        return payload
    if payload.get('extraction_version') != EXTRACTION_VERSION:
        return analyze(library, document_id)
    if payload.get('destination_version') != DESTINATION_VERSION:
        reader = PdfReader(str(library.get_path(document_id, annotated=False)))
        pages = [(page.extract_text() or '') for page in reader.pages]
        map_destinations(reader, pages, payload['references'])
        payload['destination_version'] = DESTINATION_VERSION
        _save(library, document_id, payload)
    return _resolve(library, document_id, payload, library.list_documents())


def link(library, document_id, reference_id, target_id):
    if target_id is not None:
        if str(document_id) == str(target_id):
            raise ValueError('A reference cannot link to its source document')
        library.get_document(target_id)
    payload = get(library, document_id)
    ref = next((r for r in payload['references'] if r['id'] == str(reference_id)), None)
    if ref is None:
        raise KeyError('Reference not found')
    if target_id is None:
        for key in ('manual_link', 'manual_target_id', 'local_document_id', 'local_match', 'local_missing', 'local_invalid'):
            ref.pop(key, None)
    else:
        ref['local_document_id'], ref['manual_target_id'], ref['manual_link'] = target_id, target_id, True
    _save(library, document_id, payload)
    return next(r for r in get(library, document_id)['references'] if r['id'] == str(reference_id))


def set_retrieval(library, document_id, reference_id, result):
    from datetime import datetime, timezone
    payload = get(library, document_id)
    ref = next((r for r in payload['references'] if r['id'] == str(reference_id)), None)
    if ref is None:
        raise KeyError('Reference not found')
    identity = result.get('identity')
    ref['retrieval'] = {**{k: v for k, v in result.items() if k != 'identity'}, 'checked_at': datetime.now(timezone.utc).isoformat()}
    ref['status'] = result['status']
    if identity:
        ref['identity'], ref['doi'] = identity, ref.get('doi') or identity['doi']
    _save(library, document_id, payload)


def repair_downloads(library, document_id, find=identify):
    """Name and describe documents that were downloaded before identities existed.

    Only unnamed, uncorrected PDFs linked from this paper are touched, and only when
    the identified first author appears on the PDF's first page.
    """
    payload = get(library, document_id)
    report = []
    for ref in payload['references']:
        target = ref.get('local_document_id')
        doc = library.get_document(target) if target else None
        if not doc or doc['filename'] != 'download.pdf' or doc['metadata_status'] == 'corrected':
            continue
        try:
            status, message, identity = ('found', '', ref['identity']) if ref.get('identity') else find(ref)
        except OSError as error:
            status, message, identity = 'error', str(error), None
        lines = ' '.join((_publication_evidence(library, doc) or {}).get('lines', []))
        # Small-caps headers extract as "D AHLBURG", so compare without spaces.
        squash = lambda text: fold(text).replace(' ', '')
        if not identity or not identity.get('first_author') or squash(identity['first_author']) not in squash(lines):
            report.append({'reference': ref['id'], 'document': target, 'status': 'skipped', 'message': message or 'First author not on the first page'})
            continue
        ref['identity'], ref['doi'] = identity, ref.get('doi') or identity['doi']
        name = pdf_name(ref.get('authors') or identity['first_author'], identity['year'], identity.get('publication'))
        library.apply_verified(target, identity, name)
        report.append({'reference': ref['id'], 'document': target, 'status': 'repaired', 'message': name})
    _save(library, document_id, payload)
    return report
