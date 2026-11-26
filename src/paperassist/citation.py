"""Deterministic parsing of astronomy-style journal citations."""

import html
import re
import unicodedata

_RANGE_DASHES = '\u2010\u2011\u2012–—\u2015−-'
_RANGE_OP = rf'(?:[{_RANGE_DASHES}]{{1,2}}|(?i:\bto\b))'
_NUMBER_GROUP = rf'\d+(?:\s*(?:[,;]|{_RANGE_OP})\s*\d+)*'
_CITE_PREFIX = (
    r'(?:(?:see(?:\s+also)?|compare|cf\.?|e\.g\.?|i\.e\.?|for\s+(?:example|details|reviews?)|also)'
    r'(?:\s*,?\s*)?)*'
    r'(?:(?:refs?\.?|references?|papers?|citations?)\.?\s*)?'
)
_CITE_SUFFIX = (
    r'(?:\s*[,;]\s*(?:and\s+references?\s+therein|references?\s+therein|and\s+refs?\.?\s+therein'
    r'|etc\.?|among\s+others|and\s+others'
    r'|(?:pp?\.|pages?|ch(?:ap(?:ter)?)?\.?|secs?\.?|sections?|eqs?\.?|figs?\.?|tables?|appendix)\s*[\w.\-–—()]+))?'
)
NUMERIC_MARKER = re.compile(rf'\[\s*{_CITE_PREFIX}({_NUMBER_GROUP}){_CITE_SUFFIX}\s*\]', re.I)
BRACKET_RANGE_MARKER = re.compile(rf'\[\s*(\d+)\s*\]\s*{_RANGE_OP}\s*\[\s*(\d+)\s*\]')
_SUP_DIGITS = '⁰¹²³⁴⁵⁶⁷⁸⁹'
_SUP_DASHES = '⁻\u2010\u2011\u2012–—\u2015−'
_SUP_TO_ASCII = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹˒' + _SUP_DASHES, '0123456789,' + '-' * len(_SUP_DASHES))
SUPERSCRIPT_MARKER = re.compile(
    rf'(?<![0-9{_SUP_DIGITS}{_SUP_DASHES}+=^\-])'
    rf'([{_SUP_DIGITS}]+(?:\s*(?:[,;˒]|[{_SUP_DASHES}\-]{{1,2}})\s*[{_SUP_DIGITS}]+)*)'
    rf'(?![0-9{_SUP_DIGITS}])'
)
PAREN_NUMERIC_MARKER = re.compile(rf'(?<![A-Za-z0-9])\(\s*({_NUMBER_GROUP})\s*\)')
PAREN_RANGE_MARKER = re.compile(rf'(?<![A-Za-z0-9])\(\s*(\d+)\s*\)\s*{_RANGE_OP}\s*\(\s*(\d+)\s*\)')
_PROSE_ITEM = rf'\d+(?:\s*{_RANGE_OP}\s*\d+)?'
_PROSE_PLURAL_TAIL = (
    rf'(?:\s*(?:[,;]\s*(?:(?:and|&)\s+)?|{_RANGE_OP}\s*)\d+'
    rf'|\s+(?:and|&)\s+{_PROSE_ITEM}(?=\s*[.,;:)\]]|$))'
)
PROSE_NUMERIC_MARKER = re.compile(
    rf'\b(?:'
    rf'(?P<ref_cue>refs\.?|references|citations)\s+(?P<ref_nums>\d+{_PROSE_PLURAL_TAIL}*)'
    rf'|(?P<sing_cue>ref\.?|reference|citation)\s+(?P<sing_nums>{_NUMBER_GROUP})'
    rf'|(?P<paper_cue>papers?)\s+(?P<paper_nums>\d+{_PROSE_PLURAL_TAIL}+)'
    rf')(?!\.\d|\d|[A-Za-z%°])',
    re.I,
)
SCIENTIFIC_UNITS = frozenset({
    'cm', 'mm', 'km', 'um', 'nm', 'pm', 'erg', 'sec', 'hz', 'khz', 'mhz', 'ghz',
    'ev', 'kev', 'mev', 'gev', 'tev', 'k', 'yr', 'myr', 'gyr', 'kyr',
    'au', 'pc', 'kpc', 'mpc', 'gpc', 'sr', 'mol', 'rad', 'deg', 'arcsec', 'arcmin',
})
_RANGE_SEPARATOR = re.compile(rf'\s*{_RANGE_OP}\s*')


def superscript_to_ascii(text):
    """Translate Unicode superscript digits and separators to ASCII."""
    return (text or '').translate(_SUP_TO_ASCII)


def citation_numbers(group):
    """Expand a numeric marker's contents in order, once per reference number."""
    group = superscript_to_ascii(group)
    numbers = {}
    for part in re.split(r'\s*(?:,\s*(?:and|&)\s*|[,;]|(?:\band\b|&))\s*', group, flags=re.I):
        try:
            ends = [int(value) for value in _RANGE_SEPARATOR.split(part)]
        except ValueError:
            return []
        first, last = ends[0], ends[-1]
        if len(ends) > 2 or not 0 <= last - first <= 100:
            return []
        numbers.update(dict.fromkeys(range(first, last + 1)))
    return list(numbers)


# ADS-style journal codes and the spellings found in reference lists, page headers
# and publisher metadata. Periods and spacing are ignored when matching.
JOURNALS = {
    'A&A': ['A&A', 'A & A', 'Astron Astrophys', 'Astronomy and Astrophysics', 'Astronomy & Astrophysics'],
    'A&AS': ['A&AS', 'A&A Suppl', 'Astron Astrophys Suppl', 'Astron Astrophys Suppl Ser'],
    'ApJ': ['ApJ', 'Astrophys J', 'Astrophysical Journal'],
    'ApJL': ['ApJL', 'ApJ Lett', 'Astrophys J Lett', 'Astrophysical Journal Letters'],
    'ApJS': ['ApJS', 'ApJ Suppl', 'Astrophys J Suppl', 'Astrophysical Journal Supplement',
             'Astrophysical Journal Supplement Series'],
    'AJ': ['AJ', 'Astron J', 'Astronomical Journal'],
    'MNRAS': ['MNRAS', 'Mon Not R Astron Soc', 'Mon Not R astr Soc', 'Monthly Notices of the Royal Astronomical Society'],
    'SoPh': ['SoPh', 'Sol Phys', 'Solar Physics'],
    'Natur': ['Natur', 'Nature'],
    'Sci': ['Sci', 'Science'],
    'LRSP': ['LRSP', 'Living Rev Sol Phys', 'Living Rev Solar Phys', 'Liv Rev Sol Phys', 'Living Reviews in Solar Physics'],
    'JCoPh': ['JCoPh', 'J Comput Phys', 'Journal of Computational Physics'],
    'PhRvL': ['PhRvL', 'Phys Rev Lett', 'Physical Review Letters'],
    'PhRvE': ['PhRvE', 'Phys Rev E', 'Physical Review E'],
    'PhPl': ['PhPl', 'Phys Plasmas', 'Physics of Plasmas'],
    'JGR': ['JGR', 'J Geophys Res', 'Journal of Geophysical Research'],
    'GeCoA': ['GeCoA', 'Geochim Cosmochim Acta', 'Geochimica et Cosmochimica Acta'],
    'PhyS': ['PhyS', 'Phys Scr', 'Physica Scripta'],
    'GApFD': ['GApFD', 'Geophys Astrophys Fluid Dyn', 'Geophysical and Astrophysical Fluid Dynamics'],
    'SSRv': ['SSRv', 'Space Sci Rev', 'Space Science Reviews'],
    'ARA&A': ['ARA&A', 'Annu Rev Astron Astrophys', 'Annual Review of Astronomy and Astrophysics'],
    'PASJ': ['PASJ', 'Publ Astron Soc Jpn', 'Publications of the Astronomical Society of Japan'],
    'PASP': ['PASP', 'Publ Astron Soc Pac', 'Publications of the Astronomical Society of the Pacific'],
    'RSPTA': ['RSPTA', 'Phil Trans R Soc A', 'Philos Trans R Soc A', 'Phil Trans R Soc Lond A', 'Philos Trans R Soc Lond A', 'Philos Trans R Soc London Ser A',
              'Philosophical Transactions of the Royal Society A',
              'Philosophical Transactions of the Royal Society A: Mathematical, Physical and Engineering Sciences'],
    'RSPSA': ['RSPSA', 'Proc R Soc A', 'Proc R Soc London Ser A', 'Proceedings of the Royal Society A',
              'Proceedings of the Royal Society A: Mathematical, Physical and Engineering Sciences'],
    'A&ARv': ['A&ARv', 'Astron Astrophys Rev', 'The Astronomy and Astrophysics Review'],
    'AdSpR': ['AdSpR', 'Adv Space Res', 'Advances in Space Research'],
    'Icar': ['Icar', 'Icarus'],
    'PhRvA': ['PhRvA', 'Phys Rev A', 'Physical Review A'],
    'PhRvB': ['PhRvB', 'Phys Rev B', 'Physical Review B'],
    'PhRvD': ['PhRvD', 'Phys Rev D', 'Physical Review D'],
    'RvMP': ['RvMP', 'Rev Mod Phys', 'Reviews of Modern Physics'],
    'JPlPh': ['JPlPh', 'J Plasma Phys', 'Journal of Plasma Physics'],
    'PPCF': ['PPCF', 'Plasma Phys Control Fusion', 'Plasma Physics and Controlled Fusion'],
    'ApSS': ['ApSS', 'Astrophys Space Sci', 'Astrophysics and Space Science'],
    'PASA': ['PASA', 'Publ Astron Soc Aust', 'Publications of the Astronomical Society of Australia'],
    'JASTP': ['JASTP', 'J Atmos Sol Terr Phys', 'Journal of Atmospheric and Solar-Terrestrial Physics'],
}


def _key(name):
    name = re.sub(r'[^a-z&]', '', html.unescape(name).lower())
    return name[3:] if name.startswith('the') and len(name) > 8 else name


_CODES = {_key(alias): code for code, aliases in JOURNALS.items() for alias in aliases}
# Fingerprints keep journals upper-cased, as stored in earlier evidence caches.
_DISPLAY = {code.upper(): code for code in JOURNALS}
_ALIAS = '|'.join(r'[\s.]*'.join(map(re.escape, alias.split()))
                  for alias in sorted({a for v in JOURNALS.values() for a in v}, key=len, reverse=True))
_YEAR = r'((?:19|20)\d{2})'
PUBLICATION = re.compile(
    rf'(?<![^\W\d_])({_ALIAS})(?![^\W\d_])\.?\s*,?\s*(?:\({_YEAR}\)\s*,?\s*)?(\d+)'
    rf'\s*(?:\({_YEAR}\)\s*[,:]?|[,:])\s*([A-Z]?\d+)(?:[–-]\d+)?(?:\s*\({_YEAR}\))?', re.I)
APJ_HEADER = re.compile(r'\bThe Astrophysical Journal\s*(Letters|Supplement(?: Series)?)?\s*,?\s*(\d+)\s*:\s*([A-Z]?\d+)[^\n]{0,80}?\b((?:19|20)\d{2})\b', re.I)
NAME_LETTER = r"[^\W\d_][\u0300-\u036f]*"
_NAME_WORD = rf"(?:{NAME_LETTER})+"
_HYPHENATED_NAME = rf"{_NAME_WORD}(?:[-'’]{_NAME_WORD})*"
SURNAME = rf"{_HYPHENATED_NAME}(?:\s+{_HYPHENATED_NAME})*"


def journal_code(name):
    """Fingerprint code for a journal name, or None when it is not recognised."""
    code = _CODES.get(_key(name))
    return code.upper() if code else None


def fingerprint(journal, volume, article, year):
    """Normalized [JOURNAL, volume, article, year], or None when incomplete."""
    article = str(article).upper()
    if journal == 'APJ' and article.startswith('L'):
        journal = 'APJL'
    if journal and volume and article and re.fullmatch(r'(?:19|20)\d{2}', str(year)):
        return [journal, str(volume), article, str(year)]


def publication(text, year=''):
    """Return [JOURNAL, volume, first page or article ID, year] when the text states one."""
    candidates = []
    if match := PUBLICATION.search(unicodedata.normalize('NFKC', text)):
        journal, y1, volume, y2, article, y3 = match.groups()
        candidates.append((journal_code(journal), volume, article, y1 or y2 or y3))
    if match := APJ_HEADER.search(text):
        kind, volume, article, printed_year = match.groups()
        journal = 'APJL' if kind and kind.lower() == 'letters' else 'APJS' if kind else 'APJ'
        candidates.append((journal, volume, article, printed_year))
    for journal, volume, article, printed_year in candidates:
        if result := fingerprint(journal, volume, article, printed_year or str(year)[:4]):
            return result


# "..., 2015, <journal>, <volume>, <page or article ID>" for journals the table does not list.
GENERIC_TAIL = re.compile(rf'\b{_YEAR}[a-z]?,\s*([^\W\d_][^,\d]{{1,60}}?),\s*(\d+),\s*([A-Z]?\d+)(?!\d)')


def generic_publication(text):
    """Return (year, volume, page or article ID, journal text) from "YEAR, journal, volume, page"."""
    if match := GENERIC_TAIL.search(unicodedata.normalize('NFKC', text)):
        year, journal, volume, page = match.groups()
        return year, volume, page.upper(), journal.strip()


# ADS bibcode, e.g. 1984ApJ...279..857R: journal and page are padded with dots, and scans
# often print the dots with spaces ("1984ApJ. . .279. .857R"). Spaces are removed first.
BIBCODE = re.compile(r'(?<!\d)((?:19|20)\d{2})([A-Za-z&]+?)\.+(\d+)([L]?)\.*(\d+)[A-Z]')


def printed_references(lines):
    """Fingerprints of the paper's own reference as a page header prints it, in order of appearance.

    Only a complete journal, volume and first page count; a year alone or a journal name never does.
    """
    found = []
    for line in lines:
        line = unicodedata.normalize('NFKC', line)
        candidates = [publication(line)]
        if match := BIBCODE.search(line.replace(' ', '')):
            year, journal, volume, letter, page = match.groups()
            candidates.append(fingerprint(journal_code(journal), volume, letter + page, year))
        found += [value for value in candidates if value and value not in found]
    return found


def journal_name(code):
    """Display code (an ADS bibstem) for a fingerprint journal."""
    return _DISPLAY[code]


def describe(fingerprint):
    journal, volume, article, year = fingerprint
    return f'{_DISPLAY[journal]} {volume}, {article} ({year})'


DOI = re.compile(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+')
ARXIV_ID_PATTERN = r'(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?'
ARXIV_ID = re.compile(ARXIV_ID_PATTERN, re.I)
ARXIV_REF = re.compile(rf'(?:\barxiv\s*:\s*|(?:https?://(?:www\.)?)?arxiv\.org/(?:abs|pdf)/)({ARXIV_ID_PATTERN})', re.I)


def normalize_doi(value, *, strict=True):
    """Strip DOI URL/prefix and lowercase; return None when empty (or invalid if strict=False)."""
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(value or '').strip(), flags=re.I).strip().lower()
    if not value:
        return None
    if not re.fullmatch(r'10\.\d{4,9}/\S+', value):
        if strict:
            raise ValueError('Invalid DOI')
        return None
    return value


def strip_arxiv_version(identifier):
    return re.sub(r'v\d+$', '', str(identifier or '').strip().lower())


def arxiv_id(ref):
    raw = ' '.join(str(ref.get(key) or '') for key in ('arxiv_id', 'title', 'raw'))
    if match := ARXIV_REF.search(raw):
        return match.group(1)
    bare = str(ref.get('arxiv_id') or '').strip()
    return bare if ARXIV_ID.fullmatch(bare) else ''


def fold(text):
    """Case-, accent- and ligature-insensitive form for comparing names."""
    text = unicodedata.normalize('NFKD', unicodedata.normalize('NFKC', text or ''))
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).casefold().split())


def squash(text):
    """Letters and digits only, folded, for matching text across line breaks and small caps."""
    return re.sub(r'[^a-z0-9]', '', fold(text))


SPACING_ACCENT = re.compile(r'\s*([´¨˜ˆˇ˚¸])\s*([A-Za-zı])')
COMBINING = {'´': '\u0301', '¨': '\u0308', '˜': '\u0303', 'ˆ': '\u0302', 'ˇ': '\u030c', '˚': '\u030a', '¸': '\u0327'}


_SUP_PRESERVED = re.compile(r'[^⁰¹²³⁴⁵⁶⁷⁸⁹⁻˒]+')


def clean_text(text):
    """Repair detached accents ("M¨ uller"), ligatures and line-end hyphenated names."""
    joined = SPACING_ACCENT.sub(lambda m: m.group(2).replace('ı', 'i') + COMBINING[m.group(1)], text or '')
    joined = re.sub(r'(?<=[^\W\d_])`([AEIOUaeiou])\b', r'\1' + '\u0300', joined)
    # Preserve Unicode superscripts so NFKC does not flatten citations like "text¹⁻³" into "text1−3".
    normalized = _SUP_PRESERVED.sub(lambda m: unicodedata.normalize('NFKC', m.group()), joined)
    return re.sub(r'(?<=[^\W\d_])-\n(?=[a-z])', '', normalized)


def reference_key(text):
    """Normalized alphanumeric key for matching bibliography entries across analyses."""
    return ''.join(c for c in unicodedata.normalize('NFKC', clean_text(text)).casefold() if c.isalnum())


def has_surname(text, surname):
    """Whether `surname` appears as a whole word in `text` (case- and accent-insensitive)."""
    target = fold(surname)
    return bool(target) and bool(re.search(r'(?<!\w)' + re.escape(target) + r'(?!\w)', fold(text)))


def same_surname(a, b):
    """Equal surnames, tolerating a particle ("van", "De") kept by only one source."""
    a, b = fold(a), fold(b)
    return bool(a and b) and (a == b or a.endswith(' ' + b) or b.endswith(' ' + a))


PARTICLES = {'van', 'von', 'de', 'der', 'den', 'del', 'della', 'di', 'da', 'du', 'dos', 'das', 'le', 'la', 'ter', 'ten', 'bin'}
# A dotted run of initials ("P.", "K.P.", "J.-L.") not glued to a longer word.
INITIALS = re.compile(r'(?<![^\W\d_])[^\W\d_]\.(?:-?[^\W\d_]\.)*')


def first_surname(authors):
    """Surname of the first author of "Surname I.", "I. Surname", "Surname, I." or "First Last" lists."""
    first = re.split(r'[,;&]|\band\b', re.sub(r'\bet\s+al\b\.?', '', authors or '', flags=re.I), maxsplit=1, flags=re.I)[0]
    words = [word for word in INITIALS.sub(' ', first).split() if not (len(word) == 1 and word.isupper() or word.lower() in ('jr', 'jr.', 'sr', 'sr.'))]
    start = next((i for i, word in enumerate(words) if word.lower() in PARTICLES), len(words) - 1)
    return ' '.join(words[start:])


_INITIAL_SEQ = r'(?:[^\W\d_]{1,2}\.(?:\s*-?\s*[^\W\d_]{1,2}\.)*\s*)+'
_INITIAL_AUTHOR = re.compile(rf'\s*({SURNAME})(?:,\s*(?:[JS]r\.,?\s*)?|\s+){_INITIAL_SEQ}(?=[,.\s]|$)')


def _valid_surname(name):
    words = name.split()
    return bool(words) and (words[0][0].isupper() or (words[0].lower() in PARTICLES and len(words) > 1 and words[-1][0].isupper()))


def surnames(raw):
    """Author surnames listed before the year, in order."""
    head = re.split(r'\b(?:19|20)\d{2}[a-z]?\b', unicodedata.normalize('NFKC', raw), maxsplit=1)[0]
    head = re.sub(r'^\s*(?:\[\d+\]|\d+\.)\s*', '', head)
    head = re.sub(r'\bet\s+al\.?.*$', '', head, flags=re.I).strip()
    if not head:
        return []
    clauses = [c.strip() for c in re.split(r'\s*(?:,\s*(?:&|\band\b)|[;&]|\band\b)\s*', head) if c.strip()]
    found = []
    for clause in clauses:
        pos = 0
        while pos < len(clause):
            while pos < len(clause) and clause[pos] in ' ,.':
                pos += 1
            m = _INITIAL_AUTHOR.match(clause, pos)
            if not m:
                break
            name = m.group(1).strip()
            if _valid_surname(name):
                found.append(name)
            pos = m.end()
    if found:
        return found
    # Fallback for "Surname, GivenName" author lists without dotted initials.
    given_names = []
    for clause in clauses:
        # Stop before a sentence-ending period following a multi-letter word (e.g. "Carol. Title").
        trimmed = re.split(r'(?<=[^\W\d_]{2})\.\s+', clause, maxsplit=1)[0].rstrip('., ')
        parts = [p.strip() for p in trimmed.split(',') if p.strip()]
        if len(parts) < 2 or len(parts) % 2:
            return []
        for idx, part in enumerate(parts):
            words = part.split()
            if not words or len(words) > 3 or any(any(ch.isdigit() or ch == '.' for ch in w) for w in words):
                return []
            if idx % 2 == 0 and not _valid_surname(part):
                return []
            if idx % 2 == 1 and not all(w[0].isupper() or w.lower() in PARTICLES for w in words):
                return []
        given_names.extend(parts[0::2])
    return given_names


def pdf_name(surname, year, fingerprint=None):
    """ASCII-safe library filename such as Rappazzo_2013_ApJ_771_76.pdf."""
    def ascii_part(text):
        text = unicodedata.normalize('NFKD', str(text or ''))
        return re.sub(r'[^A-Za-z0-9-]', '', text.encode('ascii', 'ignore').decode())
    parts = [ascii_part(surname), ascii_part(year)]
    if fingerprint:
        parts += [ascii_part(_DISPLAY[fingerprint[0]]), ascii_part(fingerprint[1]), ascii_part(fingerprint[2])]
    return '_'.join(part for part in parts if part) + '.pdf'
