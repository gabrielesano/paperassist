"""Conservative, per-file bibliography extraction and citation counts."""

import ctypes
import json
import math
import re
import statistics
import threading
import unicodedata
from bisect import bisect_right
from contextlib import closing
from functools import wraps
from pathlib import Path
from weakref import WeakValueDictionary

from paperassist.citation import (ARXIV_ID, DOI, NUMERIC_MARKER, PAREN_NUMERIC_MARKER, PARTICLES, SCIENTIFIC_UNITS,
                                  SUPERSCRIPT_MARKER, SURNAME, arxiv_id, citation_numbers, clean_text as _clean,
                                  describe, fold, has_surname, pdf_name, publication as _publication,
                                  reference_key as _normalize, strip_arxiv_version, superscript_to_ascii, surnames)
from paperassist.library import Library, manual_fields, sync_manual_links
from paperassist.retrieval import identify

try:
    import pypdfium2.raw as raw
    from paperassist.native.engine import Pdf, pdf_locked
except ModuleNotFoundError:  # CLI import must stay usable when reader packages are absent
    raw = None
    Pdf = None

    def pdf_locked(method):
        return method

HEADING = re.compile(r'^\s*(?:\d+\.?\s+)?(?:references|bibliography|literature\s+cited)\s*[.:]?\s*$', re.I | re.M)
FALLBACK_HEADING = re.compile(
    r'^\s*(?:\d+\s+)?(?:references|bibliography|literature\s+cited)(?:\s*\d+|\s*[.:])?\s*$', re.I | re.M)
_ASCII_DIGITS = re.compile(r'^[0-9]{1,5}$')
ENTRY = re.compile(r'^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$')
AUTHOR_ENTRY = re.compile(rf'^\s*({SURNAME}),\s*(?:[^\W\d_]\s?\.|[^\W\d_]+\s*,|[JS]r\.)')
# "Hood A. W., Browning P. K." has no comma after the surname; accepted only once the previous
# citation has ended, because the same shape also appears inside a wrapped author list.
INITIALS_ENTRY = re.compile(r"^\s*([^\W\d_]+(?:[-'’][^\W\d_]+)*),?\s(?:[A-Z][a-z]?\.\s?){1,3}(?:,|\s&|\sand\b)")
# Text extraction sometimes inserts a space inside a year ("200 8") or after "[".
YEAR = re.compile(r'\b(?:19|20)\d\s?\d[a-z]?\b')
JOINED_ENTRIES = re.compile(r"(?<=[\d.])\s+(?=[A-Z][^\W\d_]+(?:[-'’][^\W\d_]+)*,\s[A-Z]\.|[.;]\s*(?:1[89]|20)\d\d)")
IBID = re.compile(r'^[.;:—–_-]{1,8}\s*[.,]?\s*((?:1[89]|20)\d\d[a-z]?\b.*)')
ENTRY_LIKE = re.compile(r"[A-Z][^\W\d_]+,\s[A-Z]\..*\b(?:1[89]|20)\d\d\b")
# A second author may carry particles ("Rouppe van der Voort") but never a whole phrase,
# so prose after "Smith &" cannot be mistaken for a name.
PARTICLE = '(?:' + '|'.join(sorted(PARTICLES, key=len, reverse=True)) + ')'
# A few trailing letters after a space are a kerning gap ("Spi cer"), not another word.
SECOND_AUTHOR = rf"(?:[^\W\d_]+\s+(?={PARTICLE}\s))?(?:{PARTICLE}\s+)*[^\W\d_]+(?:[-'’][^\W\d_]+)*(?:\s[a-z]{{2,4}}(?![a-z]))?"
# "B" in "A & B 2001" is a second author, not a citation of B.
AFTER_NAME = re.compile(r'[^\W\d_]{2},?\s*(?:&|\band)\s+$', re.I)
DESTINATION_VERSION = 6
# Sections printed after the references. Bare words must fill the line, so a wrapped title such as
# "Table of integrals" stays in its entry.
END_HEADING = re.compile(
    r'\s*(?:\d+\.?\s+)?(?:(?:figure|table)s?(?:\s+(?:captions?|legends?))?|captions?|list\s+of\s+(?:figures|tables)'
    r'|acronyms|abbreviations|glossary|nomenclature|data\s+availability|author\s+contributions?|funding'
    r'|conflicts?\s+of\s+interests?)\s*[:.]?\s*', re.I)
END_START = re.compile(r'\s*(?:appendi(?:x|ces)\b|supplement(?:ary|al)\b|supporting\s+information|acknowledg)', re.I)
LETTERED_HEADING = re.compile(r'\s*[A-Z](?:\.\d+)*[.)]?\s+[A-Z][^\n,;]{2,}\s*$')
PAGE_LIST = re.compile(r'\d+(?:\s*[,–−-]\s*\d+)*[.]?')
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
EXTRACTION_VERSION = 8
_BLANK = {' ', '\r', '\n', '\t', '\ufffe'}
_SUP_DIGITS_MAP = str.maketrans('0123456789,;–—−-', '⁰¹²³⁴⁵⁶⁷⁸⁹˒˒⁻⁻⁻⁻')
_SUP_ASCII_GROUP = re.compile(r'\d+(?:[,;–—−-]\d+)*')
_PAREN_PREFIX_IGNORE = re.compile(
    r'(?:\b(?:eqs?|equations?|figs?|figures?|tables?|tabs?|secs?|sections?|models?|cases?|steps?)\.?\s*$'
    r'|(?:=|\+|-|–|—|−|/|\*|×|<|>|≤|≥|≈|∼|∝|\^|_)\s*$)',
    re.I,
)
_PAREN_CONT_IGNORE = re.compile(
    r'\b(?:eqs?|equations?|figs?|figures?|tables?|tabs?|secs?|sections?|models?|cases?|steps?)'
    r'\.?\s*\(\s*\d+(?:\s*[,;–—−-]\s*\d+)*\s*\)\s*(?:,\s*|and\s+|or\s+|to\s+|[–—−-]\s*)$',
    re.I,
)
_MATH_TRAILING = re.compile(r'^\s*(?:=|\+|\*|/|×|\^|_)')

_locks = WeakValueDictionary()
_locks_guard = threading.Lock()


def _synchronized(function):
    @wraps(function)
    def synchronized(library, *args, **kwargs):
        # Separate Library objects may share a database. Keep its JSON updates atomic.
        key = str(Path(library.db_path).resolve())
        with _locks_guard:
            lock = _locks.get(key)
            if lock is None:
                lock = _locks[key] = threading.RLock()
        with lock:
            return function(library, *args, **kwargs)
    return synchronized


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


def _lettered_heading(line):
    return bool(LETTERED_HEADING.fullmatch(line) and not YEAR.search(line) and not AUTHOR_ENTRY.match(line))


def _appendix_heading(line):
    return bool(re.match(r'\s*(?:appendi(?:x|ces)\b|supplement(?:ary|al)\b|supporting\s+information)', line, re.I)
                or _lettered_heading(line))


def _figure_page(text):
    """A page of axis ticks and labels: most of its lines are very short."""
    # Page numbers such as "– 21 –" say nothing about the layout.
    lines = [line for line in map(str.strip, text.splitlines()) if line and not re.fullmatch(r'[–-]?\s*\d+\s*[–-]?', line)]
    return len(lines) >= MIN_LINES and sum(len(line) < SHORT_LINE for line in lines) * 2 >= len(lines)


def _split_entries(lines, page_starts=(), running_headers=()):
    """Return entries, leading text, and the source-line index of the ending section.

    Each entry is open until its citation ends. A line that is neither a continuation nor a new
    entry then closes it, so running headers and whatever follows the list stay out of it.
    """
    entries, leading, state, previous_index = [], [], 'open', None
    # Scanned two-column pages can run several entries together on one line.
    for index, line in ((i, part) for i, line in enumerate(lines) for part in JOINED_ENTRIES.split(line)):
        same_line, previous_index = index == previous_index, index
        line = line.strip()
        numeric = ENTRY.match(line)
        # A wrapped year followed by backreference pages ("2021. 6") is not entry 2021.
        if numeric and numeric[2] and YEAR.fullmatch(numeric[2]) and PAGE_LIST.fullmatch(numeric[3]):
            numeric = None
        author = _name_start(line) and AUTHOR_ENTRY.match(line)
        raw = entries[-1]['raw'] if entries else ''
        lettered = _lettered_heading(line)
        if lettered and re.sub(r'\s+\d+$', '', line).casefold() in running_headers:
            continue
        completed = False
        if entries and lettered:
            year = YEAR.search(raw)
            completed = _citation_end(CITATION_END, raw) or (
                index in page_starts and entries[0]['label'].isdigit() and year and len(raw[:year.start()]) >= 40)
        if entries and (END_HEADING.fullmatch(line) or END_START.match(line)
                        or (lettered and not raw.endswith((',', ';', ':', '-', '/')) and completed)):
            return entries, leading, index
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
        elif (author and (not same_line or YEAR.search(line) or state == 'done')
              and (not entries or not entries[0]['label'].isdigit() and YEAR.search(entries[-1]['raw']))):
            entries.append({'label': author.group(1), 'raw': line})
            state = 'done' if _ended(line) else 'open'
        elif entries and not entries[0]['label'].isdigit() and state != 'open' and (initials := _name_start(line) and INITIALS_ENTRY.match(line)):
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
    return entries, leading, None


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
            entry['raw'] = raw[:end.end()].rstrip()


def _citation_name(author):
    name = re.escape(author).replace(r'\-', r'-\s*').replace(r'\ ', r'\s+')
    return name[0] + r'\s?' + name[1:]


def _citation(author, year, names=()):
    """Match citation grammar, using known surnames for explicit multi-author lists."""
    base, suffix = year[:4], year[4:]
    year_part = r'\s*'.join(base) + (rf'(?:{suffix}|[a-z](?:\s*,\s*[a-z])*?\s*,\s*{suffix})' if suffix else '')
    second = rf'(?:{_citation_name(names[1])}|{SECOND_AUTHOR})' if len(names) >= 2 else SECOND_AUTHOR
    explicit = ''
    if len(names) >= 3:
        separator = r'(?:\s*,\s*(?:(?:and|&)\s+)?|\s+(?:and|&)\s+)'
        explicit = rf'(?P<explicit>{separator.join(_citation_name(name) for name in names[1:])})'
        explicit = rf'(?:\s*,\s*|\s+(?:and|&)\s+){explicit}|'
    return re.compile(rf"\b{_citation_name(author)}\b(?:['’]s)?(?:{explicit}(?P<more>\s+et\s+a\s*l\.?)"
                      rf'|\s*(?:and|&)\s+(?P<second>{second}))?'
                      rf'\s*[,\(]?\s*(?:(?:19|20)\d\d[a-z]?\s*[,;]\s*)*{year_part}(?![A-Za-z0-9])', re.I)


def _fits(names, count, second, citation):
    """Only a compatible author list can own a citation shared by homonymous entries."""
    if explicit := citation.groupdict().get('explicit'):
        listed = [fold(name) for name in re.split(r'\s*(?:,|&|\band\b)\s*', explicit) if name]
        return listed == [fold(name) for name in names[1:]]
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
        authors.append((names, count, fold(names[1]) if len(names) > 1 else None))
    owned, ambiguous = [[] for _ in group], set()
    events = {(page, m.start(), m.end()): m for hits in found for page, m in hits}
    for (page, _, _), m in sorted(events.items()):
        fits = [i for i, (names, count, second) in enumerate(authors) if _fits(names, count, second, m)]
        if len(fits) == 1:
            owned[fits[0]].append((page, m))
        else:
            ambiguous.update(fits or range(len(group)))
    return owned, ambiguous


def _looks_like_entry(line):
    return bool(ENTRY.match(line) or AUTHOR_ENTRY.match(line) or INITIALS_ENTRY.match(line) or ENTRY_LIKE.search(line))


def _find_heading(pages):
    """Locate the bibliography heading, tolerating trailing margin numbers when entries follow."""
    strict = next(((i, m) for i, page in enumerate(pages) if (m := HEADING.search(page))), None)
    if strict:
        return strict
    for i, page in enumerate(pages):
        for m in FALLBACK_HEADING.finditer(page):
            following = [line.strip() for line in page[m.end():].splitlines() if line.strip()]
            if len(following) < 12 and i + 1 < len(pages):
                following.extend(line.strip() for line in pages[i + 1].splitlines() if line.strip())
            if sum(_looks_like_entry(line) for line in following[:12]) >= 2:
                return i, m
    return None


def _chain_line_numbers(ordered, step, n_pages, pagewise=False):
    seqs = []
    for item in ordered:
        page, side, _, val, _, y, _ = item
        if step > 1 and val % step != 0:
            continue
        max_jump = (4 if pagewise else 20) * step
        for seq in reversed(seqs):
            lp, lside, _, lval, _, ly, _ = seq[-1]
            if val <= lval or ((page, side) == (lp, lside) and y >= ly - 2):
                continue
            if val - lval <= max_jump:
                seq.append(item)
                break
        else:
            if not pagewise or val in (1, step):
                seqs.append([item])
    if not seqs:
        return []
    best = max(seqs, key=len)
    same_col = [(a, b) for a, b in zip(best, best[1:]) if (a[0], a[1]) == (b[0], b[1])]
    exact = sum(1 for a, b in same_col if b[3] - a[3] == step)
    if not same_col or exact * 2 < len(same_col):
        return []
    if pagewise:
        return best if len(best) >= 4 else []
    pages_hit = {item[0] for item in best}
    min_pages = 1 if n_pages == 1 else 2
    min_len = (5 if n_pages == 1 else 10) if step == 1 else 5
    min_density = 4 if step == 1 else 2
    if (len(best) >= min_len and len(pages_hit) >= min_pages
            and len(same_col) >= 2 and len(best) >= min_density * len(pages_hit)):
        return best
    return []


def _detect_line_numbers(pages_chunks, page_widths, prose_xs, *, prose_right_xs=None, top_down=False):
    """Identify outer-margin and gutter line-number spans by alignment and step progression."""
    if not prose_xs or not any(pages_chunks):
        return set()
    sorted_xs = sorted(prose_xs)
    body_left = sorted_xs[int(len(sorted_xs) * 0.02)]
    sorted_rxs = sorted(prose_right_xs or prose_xs)
    body_right = sorted_rxs[min(len(sorted_rxs) - 1, int(len(sorted_rxs) * 0.99))]
    by_side = {'left': [], 'gutter': [], 'right': []}
    for page_idx, chunks in enumerate(pages_chunks):
        if not chunks:
            continue
        width = page_widths[page_idx]
        for chunk_idx, chunk in enumerate(chunks):
            text, x, y, _ = chunk[:4]
            x1 = chunk[4] if len(chunk) > 4 else x
            band_idx = chunk[5] if len(chunk) > 5 else None
            stripped = text.strip()
            if not _ASCII_DIGITS.match(stripped):
                continue
            val = int(stripped)
            if val < 1:
                continue
            y_val = -y if top_down else y
            if x < body_left - 2:
                by_side['left'].append((page_idx, 'left', chunk_idx, val, x, y_val, 0))
            elif x > body_right + 2 and x > width * 0.80:
                by_side['right'].append((page_idx, 'right', chunk_idx, val, x, y_val, 0))
            elif (band_idx is not None and width * 0.38 <= x and x1 <= width * 0.62
                  and not any(other_idx != chunk_idx and other[5] == band_idx
                              and min(other[4], x1 + 3) > max(other[1], x - 3)
                              for other_idx, other in enumerate(chunks))
                  and sum(other[4] <= x - 2 for other in chunks) >= 3
                  and sum(other[1] >= x1 + 2 for other in chunks) >= 3):
                by_side['gutter'].append((page_idx, 'gutter', chunk_idx, val, x, y_val, 0))

    candidates = []
    for items in by_side.values():
        if not items:
            continue
        med_x = statistics.median(item[4] for item in items)
        candidates.extend(item for item in items if abs(item[4] - med_x) <= 14.0)
    if len(candidates) < 4:
        return set()

    side_rank = {'left': 0, 'gutter': 1, 'right': 2}
    ordered = sorted(candidates, key=lambda item: (item[0], side_rank[item[1]], -item[5], item[2]))
    n_pages = len(pages_chunks)
    for step in (10, 5, 1):
        chain = _chain_line_numbers(ordered, step, n_pages)
        if chain:
            return {(item[0], item[2]) for item in chain}

    pagewise, pagewise_pages = set(), set()
    for page_idx in range(n_pages):
        page_items = [item for item in ordered if item[0] == page_idx]
        if len(page_items) < 4:
            continue
        for step in (10, 5, 1):
            chain = _chain_line_numbers(page_items, step, n_pages, pagewise=True)
            if chain:
                pagewise.update((item[0], item[2]) for item in chain)
                pagewise_pages.add(page_idx)
                break
    return pagewise if len(pagewise_pages) >= (1 if n_pages == 1 else 2) else set()


def _is_pdfium(source):
    return hasattr(source, '_doc') and hasattr(source, 'frame')


def _weighted_median(pairs):
    """Median of values weighted by positive integer counts."""
    ordered = sorted((val, w) for val, w in pairs if val > 0 and w > 0)
    if not ordered:
        return 0.0
    half = sum(w for _, w in ordered) / 2
    seen = 0
    for val, w in ordered:
        seen += w
        if seen >= half:
            return val
    return ordered[-1][0]


def _valid_sup_prev(prev_text):
    text = prev_text.strip()
    if not text or text[-1] in '-+=^/*×<>~':
        return False
    if text[-1] in '.,;:)"\'”’]':
        stem = text.rstrip('.,;:)"\'”’]').lstrip('("\'“‘[')
        return bool(stem) and stem.lower() not in SCIENTIFIC_UNITS and not (stem.isdigit() and int(stem) < 1000)
    stem = text.lstrip('("\'“‘[')
    return (len(stem) >= 3 and any(c.isalpha() for c in stem) and not any(c.isdigit() for c in stem)
            and stem.lower() not in SCIENTIFIC_UNITS)


def _format_segment_tokens(tokens):
    """Join horizontal tokens into text, promoting geometric superscript citation runs."""
    if not tokens:
        return ''
    alpha_sizes = [(t['fs'], len(t['text'])) for t in tokens if any(c.isalpha() for c in t['text'])]
    body_size = _weighted_median(alpha_sizes or [(t['fs'], len(t['text'])) for t in tokens])
    out = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if out and body_size > 0 and tok['fs'] <= 0.83 * body_size:
            prev = tokens[i - 1]
            prev_h = max(prev['y1'] - prev['y0'], 1.0)
            if (tok['x0'] - prev['x1'] <= max(body_size, 8.0) * 0.8
                    and tok['y1'] < prev['y1'] - 0.15 * prev_h
                    and _valid_sup_prev(prev['text'])
                    and tok['text'][0] not in '-+=^'):
                run = []
                k = i
                while k < len(tokens):
                    cand = tokens[k]
                    if (cand['fs'] > 0.83 * body_size
                            or cand['y1'] >= prev['y1'] - 0.15 * prev_h
                            or not re.fullmatch(r'[0-9,;–—−-]+', cand['text'])
                            or (k > i and cand['x0'] - tokens[k - 1]['x1'] > max(body_size, 8.0) * 0.6)):
                        break
                    run.append(cand['text'])
                    k += 1
                if run:
                    joined = ''.join(run)
                    trimmed = joined.rstrip(',;')
                    if trimmed and _SUP_ASCII_GROUP.fullmatch(trimmed):
                        out[-1] = out[-1] + trimmed.translate(_SUP_DIGITS_MAP)
                        if len(trimmed) < len(joined):
                            out.append(joined[len(trimmed):])
                        i = k
                        continue
        if out and (tok['space'] or tok['x0'] - tokens[i - 1]['x1'] > max(tok['fs'], tokens[i - 1]['fs'], 6.0) * 0.15):
            out.append(' ')
        out.append(tok['text'])
        i += 1
    return ''.join(out)


@pdf_locked
def _extract_pdfium_page_segments(pdf, page_idx):
    """Extract visual horizontal segments in displayed top-left coordinates for one page."""
    frame = pdf.frame(page_idx)
    page_w = float(pdf.sizes[page_idx][0])
    tokens = []
    with closing(pdf._doc[page_idx]) as page, closing(page.get_textpage()) as text_page:
        cur = None
        space_pending = False
        for i in range(text_page.count_chars()):
            code = raw.FPDFText_GetUnicode(text_page, i)
            ch = chr(code) if code else ' '
            hyphen = ch in ('\ufffe', '\x02', '-') and bool(raw.FPDFText_IsHyphen(text_page, i))
            if hyphen:
                ch = '-'
            elif ch in _BLANK:
                space_pending = True
                continue
            rect = frame.rect(*text_page.get_charbox(i, loose=True))
            fs = float(raw.FPDFText_GetFontSize(text_page, i))
            if rect.height <= 0 and rect.width <= 0:
                if hyphen and cur is not None:
                    cur['text'] += '-'
                continue
            same_tok = False
            if cur is not None and not space_pending:
                cur_h = max(cur['y1'] - cur['y0'], 1.0)
                ch_h = max(rect.y1 - rect.y0, 1.0)
                y_overlap = min(cur['y1'], rect.y1) - max(cur['y0'], rect.y0) > 0.25 * min(cur_h, ch_h)
                x_close = -2.0 <= rect.x0 - cur['x1'] <= max(cur['fs'], fs, 6.0) * 0.35
                sup_edge = ((fs <= 0.83 * cur['fs'] and rect.y1 < cur['y1'] - 0.15 * cur_h)
                            or (cur['fs'] <= 0.83 * fs and cur['y1'] < rect.y1 - 0.15 * ch_h))
                same_tok = y_overlap and x_close and not sup_edge
            if same_tok:
                cur['text'] += ch
                cur['x0'] = min(cur['x0'], rect.x0)
                cur['y0'] = min(cur['y0'], rect.y0)
                cur['x1'] = max(cur['x1'], rect.x1)
                cur['y1'] = max(cur['y1'], rect.y1)
                if fs > 0:
                    cur['fs'] = fs
            else:
                cur = {'text': ch, 'x0': rect.x0, 'y0': rect.y0, 'x1': rect.x1, 'y1': rect.y1,
                       'fs': fs, 'space': space_pending}
                tokens.append(cur)
            space_pending = False

    if not tokens:
        return page_w, [], [], []

    # Group tokens into visual horizontal y-bands sorted top-to-bottom.
    tokens.sort(key=lambda t: ((t['y0'] + t['y1']) / 2, t['x0']))
    y_bands = []
    for tok in tokens:
        placed = False
        for band in reversed(y_bands):
            by0, by1, btoks = band
            overlap = min(by1, tok['y1']) - max(by0, tok['y0'])
            if overlap > 0.25 * min(max(by1 - by0, 1.0), max(tok['y1'] - tok['y0'], 1.0)):
                btoks.append(tok)
                # Keep main baseline bounds anchored by non-tiny tokens.
                if tok['y1'] - tok['y0'] >= 0.75 * (by1 - by0):
                    band[0] = min(by0, tok['y0'])
                    band[1] = max(by1, tok['y1'])
                placed = True
                break
            if tok['y0'] > by1 + 2.0:
                break
        if not placed:
            y_bands.append([tok['y0'], tok['y1'], [tok]])

    segments, prose_left_xs, prose_right_xs = [], [], []
    for band_idx, (_, _, btoks) in enumerate(y_bands):
        btoks.sort(key=lambda t: t['x0'])
        groups = [[btoks[0]]]
        for tok in btoks[1:]:
            prev = groups[-1][-1]
            gap = tok['x0'] - prev['x1']
            ref_fs = max(prev['fs'], tok['fs'], 6.0)
            isolated_num = gap >= 5.0 and (_ASCII_DIGITS.match(tok['text']) or _ASCII_DIGITS.match(prev['text']))
            if gap > max(1.3 * ref_fs, 11.0) or isolated_num:
                groups.append([tok])
            else:
                groups[-1].append(tok)
        for grp in groups:
            text = _format_segment_tokens(grp)
            x0 = min(t['x0'] for t in grp)
            y0 = min(t['y0'] for t in grp)
            x1 = max(t['x1'] for t in grp)
            y1 = max(t['y1'] for t in grp)
            segments.append((text, x0, y0, max(y1 - y0, 0.5), x1, band_idx))
            stripped = text.strip()
            if len(stripped) >= 4 and any(c.isalpha() for c in stripped):
                prose_left_xs.append(x0)
                prose_right_xs.append(x1)
    return page_w, segments, prose_left_xs, prose_right_xs


def _join_band_lines(segs):
    """Join segments within the same column ordered by (y0, x0), merging same y-band segments."""
    by_band = {}
    for seg in segs:
        by_band.setdefault(seg[5], []).append(seg)
    rows = sorted(by_band.values(), key=lambda items: (min(s[2] for s in items), min(s[1] for s in items)))
    return [' '.join(s[0] for s in sorted(row, key=lambda item: item[1])) for row in rows]


def _order_pdfium_page_lines(segments, page_w):
    """Order page segments with spanning-line band splitting and two-column detection."""
    if not segments:
        return ''
    by_band = {}
    for seg in segments:
        by_band.setdefault(seg[5], []).append(seg)
    ordered_bands = sorted(by_band.values(), key=lambda items: (min(s[2] for s in items), min(s[1] for s in items)))

    def is_spanning(band_segs):
        return any(
            (s[4] - s[1]) > 0.52 * page_w
            or (s[1] < 0.44 * page_w and s[4] > 0.56 * page_w)
            or (len(band_segs) == 1 and s[1] < 0.48 * page_w and s[4] > 0.52 * page_w)
            for s in band_segs
        )

    lines_out = []
    pending = []

    def flush_band(band_items):
        if not band_items:
            return
        start_x = int(0.40 * page_w)
        end_x = int(0.60 * page_w)
        candidates = list(range(start_x, end_x + 1, 2))
        mid = 0.50 * page_w
        if mid not in candidates:
            candidates.append(mid)
        best = None
        max_cross = 0 if len(band_items) < 20 else len(band_items) // 20
        for sx in candidates:
            left = [s for s in band_items if s[4] <= sx + 1.0]
            right = [s for s in band_items if s[1] >= sx - 1.0]
            cross = [s for s in band_items if not (s[4] <= sx + 1.0 or s[1] >= sx - 1.0)]
            if len(cross) > max_cross:
                continue
            if len({s[5] for s in left}) < 3 or len({s[5] for s in right}) < 3:
                continue
            clearance = min(sx - max(s[4] for s in left), min(s[1] for s in right) - sx)
            score = (len(cross), -clearance, abs(sx - mid))
            if best is None or score < best[0]:
                best = (score, sx, left, right, cross)
        if best is not None:
            _, sx, left, right, cross = best
            for s in cross:
                (left if (s[1] + s[4]) / 2 < sx else right).append(s)
            lines_out.extend(_join_band_lines(left))
            lines_out.extend(_join_band_lines(right))
        else:
            lines_out.extend(_join_band_lines(band_items))

    for band_segs in ordered_bands:
        if is_spanning(band_segs):
            flush_band(pending)
            pending = []
            lines_out.append(' '.join(s[0] for s in sorted(band_segs, key=lambda item: item[1])))
        else:
            pending.extend(band_segs)
    flush_band(pending)
    return '\n'.join(lines_out)


def _extract_pdfium_pages_with_chunks(pdf):
    pages_segments, page_widths, prose_left_xs, prose_right_xs = [], [], [], []
    for page_idx in range(len(pdf.sizes)):
        width, segments, left_xs, right_xs = _extract_pdfium_page_segments(pdf, page_idx)
        page_widths.append(width)
        pages_segments.append(segments)
        prose_left_xs.extend(left_xs)
        prose_right_xs.extend(right_xs)

    stripped_ids = _detect_line_numbers(
        pages_segments, page_widths, prose_left_xs, prose_right_xs=prose_right_xs, top_down=True
    )
    pages, ignored, page_chunks = [], {}, []
    for page_idx, segments in enumerate(pages_segments):
        kept = []
        for seg_idx, seg in enumerate(segments):
            if (page_idx, seg_idx) in stripped_ids:
                ignored.setdefault(page_idx, set()).add((round(seg[1], 1), round(seg[2], 1)))
            else:
                kept.append(seg)
        pages.append(_order_pdfium_page_lines(kept, page_widths[page_idx]))
        page_chunks.append([(s[0], s[1], s[2], s[3]) for s in kept])
    return pages, ignored, page_chunks


def _extract_pages_with_chunks(reader):
    """Extract per-page text while stripping verified margin line numbers, returning geometry chunks too."""
    if _is_pdfium(reader):
        return _extract_pdfium_pages_with_chunks(reader)
    pages_raw, pages_chunks, page_widths, prose_xs = [], [], [], []
    for page in reader.pages:
        box = getattr(page, 'mediabox', None)
        width = float(getattr(box, 'width', getattr(box, 'right', 612.0))) if box is not None else 612.0
        page_widths.append(width)
        chunks = []

        def visit(text, cm, tm, font, size):
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            height = abs(size) * math.hypot(tm[2] * cm[0] + tm[3] * cm[2],
                                            tm[2] * cm[1] + tm[3] * cm[3])
            chunks.append((text, x, y, height))
            stripped = text.strip()
            if len(stripped) >= 4 and any(ch.isalpha() for ch in stripped):
                prose_xs.append(x)

        try:
            raw = page.extract_text(visitor_text=visit) or ''
        except TypeError:
            raw = page.extract_text() or ''
            chunks = []
        pages_raw.append(raw)
        pages_chunks.append(chunks if chunks else None)

    stripped_ids = _detect_line_numbers(pages_chunks, page_widths, prose_xs)
    if not stripped_ids:
        return pages_raw, {}, pages_chunks

    pages, ignored = [], {}
    for page_idx, chunks in enumerate(pages_chunks):
        if chunks is None:
            pages.append(pages_raw[page_idx])
            continue
        out = []
        for chunk_idx, (text, x, y, _) in enumerate(chunks):
            if (page_idx, chunk_idx) in stripped_ids:
                ignored.setdefault(page_idx, set()).add((round(x, 1), round(y, 1)))
                if '\n' in text:
                    out.append('\n' * text.count('\n'))
            else:
                out.append(text)
        pages.append(''.join(out))
    return pages, ignored, pages_chunks


def _extract_pages(reader):
    """Extract per-page text while stripping verified margin line numbers."""
    pages, ignored, _ = _extract_pages_with_chunks(reader)
    return pages, ignored


def _prepare_bibliography(pages):
    pages = [_clean(page) for page in pages]
    return pages, _find_heading(pages)


def extract(pages):
    """Return detected references and occurrences; coverage is always partial."""
    pages, heading = _prepare_bibliography(pages)
    return _extract_references(pages, heading)


def _valid_superscript_context(text, start):
    before = text[:start].rstrip(' ')
    if not before:
        return False
    if before[-1] in '-+=^/*×<>~⁻0123456789':
        return False
    if before[-1] in '.,;:)"\'”’]':
        stem = re.split(r'\s+', before.rstrip('.,;:)"\'”’]'))[-1].lstrip('("\'“‘[')
        if not stem or stem.lower() in SCIENTIFIC_UNITS or (stem.isdigit() and int(stem) < 1000):
            return False
        return True
    word = re.split(r'\s+', before)[-1].lstrip('("\'“‘[')
    return (len(word) >= 3 and any(c.isalpha() for c in word)
            and not any(c.isdigit() for c in word) and word.lower() not in SCIENTIFIC_UNITS)


def _valid_paren_context(text, match):
    start, end = match.start(), match.end()
    line_start = text.rfind('\n', 0, start) + 1
    line_end = text.find('\n', end)
    if line_end < 0:
        line_end = len(text)
    before_on_line = text[line_start:start]
    if _PAREN_PREFIX_IGNORE.search(before_on_line) or _PAREN_CONT_IGNORE.search(before_on_line):
        return False
    if _MATH_TRAILING.match(text[end:line_end]):
        return False
    # Reject standalone or line-end/margin equation numbers like "(1)" or "x = y   (1)".
    line_after = text[end:line_end].strip()
    if not line_after:
        if not before_on_line.strip():
            return False
        if not (',' in match[1] or ';' in match[1] or any(d in match[1] for d in '–—−-')):
            prose_words = [w for w in re.findall(r'[A-Za-z]+', before_on_line) if len(w) >= 2]
            if (len(before_on_line) - len(before_on_line.rstrip(' ')) >= 2
                    or re.search(r'[=+×÷∑∫]|(?:^|\s)/(?:\s|$)', before_on_line)
                    or len(prose_words) < 3):
                return False
    return True


def _extract_references(pages, heading):
    if not heading:
        return []
    page_index, match = heading
    # Scanned two-column pages can print the left column of entries before the heading.
    above = pages[page_index][:match.start()].splitlines()
    top = len(above)
    while top and ENTRY_LIKE.search(above[top - 1]):
        top -= 1
    bibliography = [(page_index, line) for line in above[top:] + pages[page_index][match.end():].splitlines()]
    bibliography.extend((i, line) for i in range(page_index + 1, len(pages)) if not _figure_page(pages[i])
                        for line in pages[i].splitlines())
    first_lines = {}
    for index, (page, line) in enumerate(bibliography):
        if line.strip():
            first_lines.setdefault(page, index)
    running_headers = {re.sub(r'\s+\d+$', '', line.strip()).casefold()
                       for page in pages[:page_index] for line in page.splitlines()[:2]}
    entries, leading, ending = _split_entries([line for _, line in bibliography], set(first_lines.values()), running_headers)
    _trim_outliers(entries)
    # In two-column layouts a mid-page heading can precede the rest of the body text.
    body = pages[:page_index] + ['\n'.join([*above[:top], *leading])]
    body.extend('' for _ in pages[page_index + 1:])
    if ending is not None and _appendix_heading(bibliography[ending][1]):
        ending_page = bibliography[ending][0]
        body[ending_page] += '\n' + '\n'.join(line for page, line in bibliography[ending:] if page == ending_page)
        body[ending_page + 1:] = pages[ending_page + 1:]
    body_text = '\n'.join(body)
    page_starts, offset = [], 0
    for text in body:
        page_starts.append(offset)
        offset += len(text) + 1
    numeric_labels = {int(entry['label']) for entry in entries if entry['label'].isdigit()}
    numeric_markers = {}
    seen_spans = {}
    for match in NUMERIC_MARKER.finditer(body_text):
        for number in citation_numbers(match[1]):
            span = (number, match.start(), match.end())
            if span not in seen_spans:
                seen_spans[span] = True
                numeric_markers.setdefault(number, []).append((match, False))
    if numeric_labels:
        max_entry = max(numeric_labels)
        for match in SUPERSCRIPT_MARKER.finditer(body_text):
            if not _valid_superscript_context(body_text, match.start()):
                continue
            nums = citation_numbers(match[1])
            if nums and all(1 <= n <= max_entry and not (1900 <= n <= 2099) for n in nums):
                for number in nums:
                    span = (number, match.start(), match.end())
                    if span not in seen_spans:
                        seen_spans[span] = True
                        numeric_markers.setdefault(number, []).append((match, True))
        if 1 in numeric_labels:
            for match in PAREN_NUMERIC_MARKER.finditer(body_text):
                if not _valid_paren_context(body_text, match):
                    continue
                nums = citation_numbers(match[1])
                if nums and all(1 <= n <= max_entry and not (1900 <= n <= 2099) for n in nums):
                    for number in nums:
                        span = (number, match.start(), match.end())
                        if span not in seen_spans:
                            seen_spans[span] = True
                            numeric_markers.setdefault(number, []).append((match, False))
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
            matches = numeric_markers.get(int(entry['label']), [])
        elif author and year:
            pattern = _citation(surname, year_text, surnames(raw))
            matches = [(m, False) for m in pattern.finditer(body_text)
                       if not AFTER_NAME.search(body_text[max(0, m.start() - 30):m.start()])]
        else:
            matches = []
        found.append([(bisect_right(page_starts, m.start()), m, is_sup) for m, is_sup in matches
                      if bisect_right(page_starts, m.end() - 1) - bisect_right(page_starts, m.start()) <= 1])
        entry.update(id=str(index), title=raw, authors=surname,
                     year=year_text, doi=doi.group().rstrip('.,;') if doi else '',
                     count_status='partial', local_document_id=None)
    homonyms = {}
    for i, entry in enumerate(entries):
        if not entry['label'].isdigit():
            homonyms.setdefault(entry['label'].lower(), []).append(i)
    ambiguous = set()
    for members in homonyms.values():
        if len(members) > 1:
            owned, unclear = _share([entries[i] for i in members],
                                    [[(page, m) for page, m, _ in found[i]] for i in members])
            for i, hits in zip(members, owned):
                found[i] = [(page, m, False) for page, m in hits]
            ambiguous.update(members[j] for j in unclear)
    for i, entry in enumerate(entries):
        entry['occurrences'] = []
        for page, m, is_sup in found[i]:
            marker_text = superscript_to_ascii(m.group()) if is_sup else m.group()
            occurrence = {'page': page, 'context': body_text[max(0, m.start()-60):m.end()+60], 'marker': marker_text}
            last = bisect_right(page_starts, m.end() - 1)
            if last > page:
                parts = []
                for number in range(page, last + 1):
                    start = max(m.start() - page_starts[number - 1], 0)
                    end = min(m.end() - page_starts[number - 1], len(body[number - 1]))
                    marker = body[number - 1][start:end].strip()
                    ordinal = body[number - 1][:start].casefold().count(marker.casefold()) if marker else 0
                    parts.append({'page': number, 'marker': marker, 'ordinal': ordinal})
                occurrence['marker_parts'] = parts
            entry['occurrences'].append(occurrence)
        entry['citation_count'] = len(entry['occurrences'])
        if i in ambiguous:
            entry.update(citation_count=None, count_status='ambiguous', occurrences=[])
    return entries


def analyze(library, document_id, *, resolve=True):
    return _store_analysis(library, document_id, _analyze_pdf(library, document_id), resolve=resolve)


def _analyze_pdf(library, document_id, refs=None):
    data, _ = library.read_pdf(document_id, annotated=False)
    pdf = Pdf(data)
    try:
        pages, ignored, page_chunks = _extract_pages_with_chunks(pdf)
        pages, heading = _prepare_bibliography(pages)
        if refs is None:
            refs = _extract_references(pages, heading)
        _map_destinations(pdf, heading, refs, ignored, page_chunks=page_chunks)
        _crosscheck_links(pdf, heading, refs)
        return refs
    finally:
        pdf.close()


@_synchronized
def _store_analysis(library, document_id, refs, *, resolve=True):
    # Extraction can be slow; merge the latest user changes only after it finishes.
    previous_payload = _load(library, document_id)
    previous = previous_payload.get('references', [])
    reserved = {str(old['id']) for old in previous if 'id' in old}
    next_id = max(previous_payload.get('next_reference_id', 0),
                  max((int(value) for value in reserved if value.isdigit()), default=-1) + 1)
    entries = [(ref, _normalize(ref['raw'])) for ref in refs]
    previous_entries = [(old, _normalize(old['raw'])) for old in previous]
    retained = {}
    for ref, raw in entries:
        matches = [old for old, old_raw in previous_entries if 'id' in old and str(old['id']) not in retained
                   and (raw == old_raw or
                        (str(ref['label']).isdigit() and ref['label'] == old.get('label')
                         and min(len(raw), len(old_raw)) >= 20
                         and (raw.startswith(old_raw) or old_raw.startswith(raw))))]
        if len(matches) == 1:
            ref['id'] = str(matches[0]['id'])
            retained[ref['id']] = matches[0]
        else:
            # Never reuse removed IDs: a download may still hold an older selection.
            ref['id'] = str(next_id)
            next_id += 1
    preserved = [(old, raw) for old, raw in previous_entries
                 if old.get('manual_link') or old.get('retrieval') or old.get('identity')]
    claimed = set()
    for ref, raw in entries:
        matches = []
        for i, (old, old_raw) in enumerate(preserved):
            old_id = str(old['id']) if 'id' in old else None
            if i in claimed or (old_id in retained and ref['id'] != old_id):
                continue
            if (old_raw == raw and raw) or retained.get(ref['id']) is old:
                matches.append(i)
                continue
            # An older parser may have merged the next entry into this one.
            if (len(raw) >= 12 and old_raw.startswith(raw) and old.get('year') == ref.get('year')
                    and old.get('authors') and _normalize(ref.get('authors', '')).startswith(_normalize(old['authors']))
                    and sum(old_raw.startswith(text) for _, text in entries) == 1):
                matches.append(i)
        old = preserved[matches[0]][0] if len(matches) == 1 else {}
        if old:
            claimed.add(matches[0])
            if 'id' in old:
                ref['id'] = str(old['id'])
                retained[ref['id']] = old
        if state := old.get('retrieval'):
            ref['retrieval'], ref['status'] = state, state['status']
        if identity := old.get('identity'):
            ref['identity'], ref['doi'] = identity, ref['doi'] or identity['doi']
        linked = (old.get('manual_target_id') or old.get('local_document_id')) if old.get('manual_link') else None
        if linked:
            ref['local_document_id'], ref['manual_link'] = linked, True
    for i, (old, _) in enumerate(preserved):
        if i not in claimed:
            reference_id = str(old['id']) if 'id' in old else str(next_id)
            next_id += 'id' not in old
            refs.append({**old, 'id': reference_id, 'destinations': [], 'occurrences': [],
                         'citation_count': None, 'count_status': 'ambiguous', 'legacy': True,
                         'status': 'Previous reference — verify link'})
    payload = {'references': sorted(refs, key=lambda r: -(r['citation_count'] if r['citation_count'] is not None else -1)), 'status': 'partial', 'destination_version': DESTINATION_VERSION, 'extraction_version': EXTRACTION_VERSION,
               'next_reference_id': next_id,
               'message': 'Detected citations only; bibliography and counts may be incomplete.'}
    _save(library, document_id, payload)
    return _resolve(library, document_id, payload, library.list_documents()) if resolve else payload


def crosscheck_links(data, pages, refs):
    """Reconcile link and text events by position, retaining ambiguous counts as unknown."""
    _, heading = _prepare_bibliography(pages)
    _crosscheck_links(data, heading, refs)


def _crosscheck_links(data, heading, refs):
    linked = [ref for ref in refs if ref.get('destinations') and ref.get('citation_count') is not None]
    if not linked or heading is None:
        return
    from paperassist.citations import CitationIndex

    index = CitationIndex(data)
    try:
        index.prepare_bibliography(refs, heading[0] + 1)
        for ref in linked:
            # Rebuild link events from the PDF, rather than treating saved geometry as new text.
            ref.pop('occurrence_geometry', None)
            occurrences = index.pdf_occurrences(ref)
            ref.update(occurrences=occurrences, citation_count=len(occurrences), occurrence_geometry='pdf')
    finally:
        index.close()


STRUCTURAL_DESTINATION = re.compile(
    r'^(?:page|section|subsection|subsubsection|chapter|appendix|part|figure|table|equation|footnote|Hfootnote)\.')


def _destination_coordinates(destination):
    left, top = destination.left, destination.top
    if destination.typ == '/FitR' and top is not None:
        bottom = destination.bottom
        top = max(float(top), float(bottom)) if bottom is not None else top
    elif destination.typ in ('/FitH', '/FitBH'):
        left = None
    elif destination.typ != '/XYZ':
        return None, None
    return float(left) if left is not None else None, float(top) if top is not None else None


@pdf_locked
def _pdfium_named_destinations(pdf, heading_page):
    destinations = {}
    page_count = len(pdf.sizes)
    for index in range(raw.FPDF_CountNamedDests(pdf._doc)):
        length = ctypes.c_long()
        raw.FPDF_GetNamedDest(pdf._doc, index, None, length)
        if length.value <= 0:
            continue
        name_buf = ctypes.create_string_buffer(length.value)
        dest = raw.FPDF_GetNamedDest(pdf._doc, index, name_buf, length)
        if not dest:
            continue
        name = name_buf.raw.decode('utf-16-le', errors='replace').rstrip('\0')
        if STRUCTURAL_DESTINATION.match(name):
            continue
        page, top = pdf._dest_target(dest)
        if page is None or not (heading_page <= page < page_count):
            continue
        has_x, has_y, has_zoom = (ctypes.c_int() for _ in range(3))
        x, y, zoom = (ctypes.c_float() for _ in range(3))
        raw.FPDFDest_GetLocationInPage(dest, has_x, has_y, has_zoom, x, y, zoom)
        frame = pdf.frame(page)
        left = None
        if has_x.value:
            left = frame.point(x.value, y.value if has_y.value else frame.top)[0]
        else:
            count, params = ctypes.c_ulong(4), (ctypes.c_float * 4)()
            view = raw.FPDFDest_GetView(dest, count, params)
            if view == raw.PDFDEST_VIEW_FITR:
                left = frame.rect(params[0], params[1], params[2], params[3]).x0
            elif view in (raw.PDFDEST_VIEW_FITV, raw.PDFDEST_VIEW_FITBV):
                left = frame.point(params[0], frame.top)[0]
        destinations.setdefault(page, []).append((name, left, top))
    return destinations


def map_destinations(reader, pages, refs, _ignored=None):
    """Match the nearest bibliography row; unrecognized or ambiguous rows stay unmapped."""
    _, heading = _prepare_bibliography(pages)
    _map_destinations(reader, heading, refs, _ignored)


def _map_destinations(reader, heading, refs, ignored, page_chunks=None):
    for ref in refs:
        ref['destinations'] = []
    if heading is None:
        return
    heading_page, _ = heading
    top_down = _is_pdfium(reader)
    if top_down:
        destinations = _pdfium_named_destinations(reader, heading_page)
        page_count = len(reader.sizes)
    else:
        destinations = {}
        page_count = len(reader.pages)
        for name, dest in reader.named_destinations.items():
            if STRUCTURAL_DESTINATION.match(str(name)):
                continue
            try:
                page = reader.get_destination_page_number(dest)
                left, top = _destination_coordinates(dest)
                if page is not None and heading_page <= page < page_count and top is not None:
                    destinations.setdefault(page, []).append((name, left, top))
            except (TypeError, ValueError, AttributeError):
                continue
    entries = [(_normalize(ref['raw']), ref) for ref in refs]
    for page_number, targets in destinations.items():
        lines = []
        skip_coords = (ignored or {}).get(page_number, set())
        chunks = page_chunks[page_number] if page_chunks and page_number < len(page_chunks) else None
        if chunks is not None:
            for text, x, y, height in chunks:
                if not text.strip() or (round(x, 1), round(y, 1)) in skip_coords or not height:
                    continue
                lines.append((x, y, y + height, text.splitlines()[0]))
            page_right = (float(reader.sizes[page_number][0]) if top_down
                          else float(reader.pages[page_number].mediabox.right)
                          if hasattr(reader.pages[page_number], 'mediabox') else None)
        elif top_down:
            _, segments, _, _ = _extract_pdfium_page_segments(reader, page_number)
            for text, x, y, height, _, _ in segments:
                if not text.strip() or (round(x, 1), round(y, 1)) in skip_coords or not height:
                    continue
                lines.append((x, y, y + height, text.splitlines()[0]))
            page_right = float(reader.sizes[page_number][0])
        else:
            page = reader.pages[page_number]

            def visit(text, cm, tm, font, size):
                if not text.strip():
                    return
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                if (round(x, 1), round(y, 1)) in skip_coords:
                    return
                height = abs(size) * math.hypot(tm[2] * cm[0] + tm[3] * cm[2],
                                                tm[2] * cm[1] + tm[3] * cm[3])
                if height:
                    lines.append((x, y, y + height, text.splitlines()[0]))

            page.extract_text(visitor_text=visit)
            page_right = float(page.mediabox.right) if hasattr(page, 'mediabox') else None

        for name, left, top in targets:
            if top_down:
                # In top-left display coordinates, font_top = y0 and font_bottom = y1.
                nearby = [(font_bottom - top, font_bottom) for x, font_top, font_bottom, _ in lines
                          if font_top - 14 <= top <= font_bottom + 1
                          and (left is None or -10 <= x - left <= 120)]
            else:
                # An anchor inside the font band can be closer to the next row's estimated top.
                nearby = [(top - baseline, baseline) for x, baseline, y, _ in lines
                          if -5 <= top - y <= 14 and top >= baseline - 1
                          and (left is None or -10 <= x - left <= 120)]
            if not nearby:
                continue
            distance, baseline = min(nearby)
            # Do not try another row when this row's text cannot be recognized.
            if any(abs(dy - distance) < .1 and abs(b - baseline) >= .8 for dy, b in nearby):
                continue
            right = min((x - 10 for _, x, _ in targets if left is not None and x is not None and x > left + 150),
                        default=page_right if page_right is not None else (left or 0) + 260)
            row = sorted(((x, order, text) for order, (x, y0, y1, text) in enumerate(lines)
                          if abs((y1 if top_down else y0) - baseline) < .8 and (left is None or left - 10 <= x < right)),
                         key=lambda span: (round(span[0], 1), span[1]))
            line = (' ' if top_down else '').join(text for _, _, text in row)
            repeated = re.match(r'^\s*[—–-]{1,3}\.?\s*(.+)', line)
            numbered = ENTRY.match(line)
            label = (numbered[1] or numbered[2]) if numbered else None
            normalized = _normalize(repeated[1] if repeated else numbered[3] if numbered else line)
            matches = [ref for raw, ref in entries if normalized
                       and ((repeated and len(normalized) >= 12 and normalized in raw)
                            or (not repeated and raw.startswith(normalized)
                                and (len(normalized) >= 20 or raw == normalized and len(normalized) >= 12)))
                       and (label is None or str(ref.get('label')) == label)]
            if len(matches) == 1:
                matches[0]['destinations'].append(name)


def _prepare_documents(documents, source_id):
    """Precompute per-document tokens once per resolution pass instead of per reference."""
    source = str(source_id)
    prepared = []
    for document in documents:
        if str(document['id']) == source:
            continue
        title_words = frozenset(w for w in re.findall(r'[a-z0-9]+', (document.get('title') or '').lower()) if len(w) > 3)
        doc_authors = fold(document.get('authors') or '')
        years = frozenset(int(value) for value in document.get('metadata', {}).get('candidates', {}).get('year', []))
        prepared.append((document, title_words, doc_authors, years, str(document.get('year') or ''),
                         {'id': document['id'], 'title': document.get('title'),
                          'authors': document.get('authors'), 'year': document.get('year')}))
    return prepared


def _local_candidates(ref, prepared, evidence=None):
    raw_words = frozenset(re.findall(r'[a-z0-9]+', ref['raw'].lower()))
    names = list(dict.fromkeys(fold(name) for name in surnames(ref['raw'])))
    author = fold(ref.get('authors', ''))
    year = (ref.get('year') or '')[:4]
    patterns = {name: re.compile(r'(?<!\w)' + re.escape(name) + r'(?!\w)')
                for name in (*names, *( [author] if author else [] )) if name}
    candidates = []
    for document, title_words, doc_authors, years, year_str, summary in prepared:
        if year and year_str not in ('', year):
            continue
        def position(name):
            if not name or name not in doc_authors:
                return None
            match = patterns[name].search(doc_authors)
            return match.start() if match else None
        author_pos = position(author) if author else None
        if author and doc_authors and author_pos is None:
            continue
        title_match = len(title_words) >= 3 and len(title_words & raw_words) / len(title_words) >= .8
        if not title_match:
            if not (author and author_pos is not None):
                continue
            positions = [pos for name in names if (pos := position(name)) is not None]
            if len(positions) < 2 or positions != sorted(positions):
                continue
        if has_conflict(ref, document, evidence(document) if evidence else None):
            continue
        # A paper almost always prints its own year on the first page.
        if years and year and not any(abs(value - int(year)) <= 1 for value in years):
            continue
        candidates.append(((bool(author) and author_pos == 0, int(year or 0) in years), summary))
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    return [candidate for _, candidate in candidates[:5]]


def local_candidates(ref, documents, source_id, evidence=None):
    """Suggest possible matches without silently merging different works.

    `evidence` maps a document to its publication evidence; a document whose own
    journal, volume, page or year contradicts the reference is never suggested.
    """
    return _local_candidates(ref, _prepare_documents(documents, source_id), evidence)


def has_conflict(ref, document, evidence):
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
    with library._connect() as db:
        db.execute('INSERT OR REPLACE INTO reference_analyses VALUES (?, ?)', (str(document_id), json.dumps(payload)))
        sync_manual_links(db, str(document_id), payload.get('references', []))


def _load(library, document_id):
    with library._connect() as db:
        row = db.execute('SELECT payload FROM reference_analyses WHERE source_id = ?', (str(document_id),)).fetchone()
    if not row:
        return {'references': [], 'status': 'not_analyzed'}
    return json.loads(row[0])


def _available(library, document):
    if type(library).get_path is Library.get_path:
        return library.has_pdf(document)
    try:
        library.get_path(document['id'])
        return True
    except (KeyError, FileNotFoundError, OSError):
        return False


def _first_page_text(pdf):
    if _is_pdfium(pdf):
        width, segments, _, _ = _extract_pdfium_page_segments(pdf, 0)
        return _order_pdfium_page_lines(segments, width)
    return pdf.pages[0].extract_text() or ''


def _publication_evidence(library, document):
    sha = document.get('sha256')
    if not sha:
        return None
    with library._connect() as db:
        row = db.execute('SELECT payload FROM publication_evidence_v4 WHERE sha = ?', (sha,)).fetchone()
        if row:
            return json.loads(row[0])
        try:
            data, _ = library.read_pdf(document['id'], annotated=False)
            pdf = Pdf(data)
            try:
                text = _first_page_text(pdf)
            finally:
                pdf.close()
            lines = text.splitlines()
            evidence = {'publication': _publication('\n'.join(lines[:6])), 'lines': lines[:15]}
        except Exception:
            return None
        db.execute('INSERT OR REPLACE INTO publication_evidence_v4 VALUES (?, ?)', (sha, json.dumps(evidence)))
        return evidence


def _load_evidence_batch(library, documents):
    shas = list({d['sha256'] for d in documents if d.get('sha256')})
    cached = {}
    if not shas:
        return cached
    with library._connect() as db:
        for start in range(0, len(shas), 900):
            chunk = shas[start:start + 900]
            placeholders = ','.join('?' * len(chunk))
            for sha, raw in db.execute(f'SELECT sha, payload FROM publication_evidence_v4 WHERE sha IN ({placeholders})', chunk):
                cached[sha] = json.loads(raw)
    return cached


def _resolve(library, document_id, payload, documents):
    source = str(document_id)
    with library._connect() as db:
        rows = db.execute('SELECT raw_key, target_id FROM reference_manual_links WHERE source_id != ?', (source,)).fetchall()
    shared = {}
    for raw_key, target_id in rows:
        shared.setdefault(raw_key, set()).add(target_id)
    available = {str(d['id']): d for d in documents if _available(library, d)}
    possible = [d for key, d in available.items() if key != source]
    possible_by_id = {str(d['id']): d for d in possible}
    by_doi = {}
    by_first_page_doi = {}
    by_arxiv = {}
    by_first_page_arxiv = {}
    by_bibcode = {}
    folded_authors = {}
    manual_authors = {}
    for d in possible:
        doi = (d.get('doi') or '').lower()
        if doi:
            by_doi.setdefault(doi, []).append(d)
        meta = d.get('metadata') or {}
        candidates = meta.get('candidates') or {}
        for printed_doi in {value.lower() for value in candidates.get('doi', [])}:
            by_first_page_doi.setdefault(printed_doi, []).append(d)
        verified = meta.get('verified') or {}
        doc_arxiv_ids = set()
        if aid := strip_arxiv_version(verified.get('arxiv_id') or ''):
            if ARXIV_ID.fullmatch(aid):
                doc_arxiv_ids.add(aid)
        if doi.startswith('10.48550/arxiv.'):
            aid = strip_arxiv_version(doi.removeprefix('10.48550/arxiv.'))
            if ARXIV_ID.fullmatch(aid):
                doc_arxiv_ids.add(aid)
        for aid in doc_arxiv_ids:
            by_arxiv.setdefault(aid, []).append(d)
        for printed_arxiv in {strip_arxiv_version(value) for value in candidates.get('arxiv', [])}:
            if ARXIV_ID.fullmatch(printed_arxiv):
                by_first_page_arxiv.setdefault(printed_arxiv, []).append(d)
        if bib := str(verified.get('bibcode') or '').strip().lower():
            by_bibcode.setdefault(bib, []).append(d)
        folded_authors[d['id']] = fold(d.get('authors') or '')
        manual_authors[d['id']] = 'authors' in manual_fields(d)

    evidence_by_sha = _load_evidence_batch(library, documents)
    evidence_cache = {}

    def evidence_for(d):
        if d['id'] not in evidence_cache:
            sha = d.get('sha256')
            found = evidence_by_sha[sha] if sha and sha in evidence_by_sha else _publication_evidence(library, d)
            # PDFs without a printed journal header (preprints) keep the verified citation.
            verified = d.get('metadata', {}).get('verified', {}).get('publication')
            if verified and not (found and found['publication']):
                found = {'lines': (found or {}).get('lines', []), 'publication': verified}
            evidence_cache[d['id']] = found
        return evidence_cache[d['id']]

    by_publication = None
    prepared = _prepare_documents(documents, document_id)

    for ref in payload['references']:
        ref['local_candidates'] = _local_candidates(ref, prepared, evidence_for)
        for key in ('local_match', 'local_missing', 'local_invalid', 'local_conflict'):
            ref.pop(key, None)
        if ref.get('manual_link'):
            target = ref.get('manual_target_id') or ref.get('local_document_id')
            ref['manual_target_id'] = target
            ref['local_invalid'] = str(target) == source
            ref['local_document_id'] = target if str(target) in available and not ref['local_invalid'] else None
            ref['local_missing'] = str(target) not in available and not ref['local_invalid']
            ref['local_match'] = 'manual'
            if ref['local_document_id'] and (reason := has_conflict(ref, available[str(target)], evidence_for(available[str(target)]))):
                ref['local_conflict'] = reason  # flagged for review; the user's link is kept
            continue
        ref['local_document_id'] = None
        ref_doi = (ref.get('doi') or '').lower()
        matches = by_doi.get(ref_doi, []) if ref_doi else []
        reason = 'doi'
        if not matches and ref_doi:
            matches = [d for d in by_first_page_doi.get(ref_doi, []) if _first_page_doi(ref, d, evidence_for)]
            reason = 'first_page_doi'
        if not matches and (ref_arxiv := strip_arxiv_version(arxiv_id(ref))) and ARXIV_ID.fullmatch(ref_arxiv):
            matches = by_arxiv.get(ref_arxiv, [])
            reason = 'arxiv'
            if not matches:
                matches = [d for d in by_first_page_arxiv.get(ref_arxiv, []) if _first_page_arxiv(ref, d, ref_arxiv, evidence_for)]
                reason = 'first_page_arxiv'
        if not matches and (ref_bib := str((ref.get('identity') or {}).get('bibcode') or '').strip().lower()):
            matches = by_bibcode.get(ref_bib, [])
            reason = 'bibcode'
        if not matches:
            targets = shared.get(_normalize(ref['raw']), set())
            matches = [possible_by_id[t] for t in targets if t in possible_by_id] if len(targets) == 1 else []
            reason = 'shared_manual'
        else:
            targets = set()
        if not matches and len(targets) <= 1 and (fingerprint := _publication(ref['raw'], ref.get('year', ''))):
            author = fold(ref.get('authors', ''))
            matches = []
            if author:
                if by_publication is None:
                    by_publication = {}
                    for d in possible:
                        ev = evidence_for(d)
                        if ev and ev.get('publication'):
                            by_publication.setdefault(tuple(ev['publication']), []).append((d, ev))
                author_word = re.compile(r'(?<!\w)' + re.escape(author) + r'(?!\w)')
                first_author = re.compile(r'^\s*(?:[A-Z]\.\s*)+' + re.escape(author) + r'(?![^\W\d_])', re.I)
                for d, evidence in by_publication.get(tuple(fingerprint), ()):
                    authors = folded_authors[d['id']]
                    in_metadata = author in authors and bool(author_word.search(authors))
                    # Authors the user typed outrank the printed header; extracted ones do not.
                    if manual_authors[d['id']] and authors and not in_metadata:
                        continue
                    if d.get('year') and str(d['year']) != fingerprint[3]:
                        continue
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
    author = ref.get('authors', '')
    seen = f"{document.get('authors') or ''} {' '.join((evidence or {}).get('lines', []))}"
    return bool(author) and not has_conflict(ref, document, evidence) and has_surname(seen, author)


def _first_page_arxiv(ref, document, ref_arxiv, evidence_for):
    """The reference's arXiv ID is printed on the PDF's first page and nothing else disagrees."""
    printed = {strip_arxiv_version(value) for value in document.get('metadata', {}).get('candidates', {}).get('arxiv', [])}
    if ref_arxiv not in printed:
        return False
    evidence = evidence_for(document)
    author = ref.get('authors', '')
    seen = f"{document.get('authors') or ''} {' '.join((evidence or {}).get('lines', []))}"
    return bool(author) and not has_conflict(ref, document, evidence) and has_surname(seen, author)


@_synchronized
def get(library, document_id, *, resolve=True):
    """Load or update the analysis, optionally skipping local matching."""
    payload = _load(library, document_id)
    if payload['status'] == 'not_analyzed':
        return payload
    if payload.get('extraction_version') != EXTRACTION_VERSION:
        return analyze(library, document_id, resolve=resolve)
    if payload.get('destination_version') != DESTINATION_VERSION:
        _analyze_pdf(library, document_id, payload['references'])
        payload['destination_version'] = DESTINATION_VERSION
        _save(library, document_id, payload)
    return _resolve(library, document_id, payload, library.list_documents()) if resolve else payload


def _reference_update(library, document_id, reference_id, target_id):
    if target_id is not None:
        if str(document_id) == str(target_id):
            raise ValueError('A reference cannot link to its source document')
        library.get_document(target_id)
    payload = get(library, document_id, resolve=False)
    ref = next((r for r in payload['references'] if r['id'] == str(reference_id)), None)
    if ref is None:
        raise KeyError('Reference not found')
    if target_id is not None:
        ref['local_document_id'], ref['manual_target_id'], ref['manual_link'] = target_id, target_id, True
        # A stale self-link flag would hide this valid link from other papers.
        ref.pop('local_invalid', None)
    return payload, ref


@_synchronized
def link(library, document_id, reference_id, target_id):
    payload, ref = _reference_update(library, document_id, reference_id, target_id)
    if target_id is None:
        for key in ('manual_link', 'manual_target_id', 'local_document_id', 'local_match', 'local_missing', 'local_invalid'):
            ref.pop(key, None)
    _save(library, document_id, payload)
    _resolve(library, document_id, payload, library.list_documents())
    return ref


@_synchronized
def set_retrieval(library, document_id, reference_id, result, *, target_id=None):
    """Save a retrieval outcome and any associated manual link in one update."""
    from datetime import datetime, timezone
    payload, ref = _reference_update(library, document_id, reference_id, target_id)
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
            _, message, identity = ('found', '', ref['identity']) if ref.get('identity') else find(ref)
        except OSError as error:
            message, identity = str(error), None
        report.append(_repair_reference(library, document_id, ref['id'], target, identity, message))
    return report


@_synchronized
def _repair_reference(library, document_id, reference_id, target, identity, message):
    payload = get(library, document_id, resolve=False)
    ref = next((ref for ref in payload['references'] if ref['id'] == reference_id), None)
    if ref is not None and not ref.get('manual_link'):
        payload = get(library, document_id)
        ref = next((ref for ref in payload['references'] if ref['id'] == reference_id), None)
    result = {'reference': reference_id, 'document': target, 'status': 'skipped'}
    current_target = (ref.get('manual_target_id') or ref.get('local_document_id')) if ref else None
    if ref is None or str(current_target) != str(target):
        return {**result, 'message': 'Reference link changed during lookup'}
    doc = library.get_document(target)
    if doc['filename'] != 'download.pdf' or doc['metadata_status'] == 'corrected':
        return {**result, 'message': 'Document changed during lookup'}
    identity = ref.get('identity') or identity
    lines = ' '.join((_publication_evidence(library, doc) or {}).get('lines', []))
    # Small-caps headers extract as "D AHLBURG", so compare without spaces.
    squash = lambda text: fold(text).replace(' ', '')
    if not identity or not identity.get('first_author') or squash(identity['first_author']) not in squash(lines):
        return {**result, 'message': message or 'First author not on the first page'}
    ref['identity'], ref['doi'] = identity, ref.get('doi') or identity['doi']
    name = pdf_name(ref.get('authors') or identity['first_author'], identity['year'], identity.get('publication'))
    library.apply_verified(target, identity, name)
    _save(library, document_id, payload)
    return {**result, 'status': 'repaired', 'message': name}
