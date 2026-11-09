"""Deterministic parsing of astronomy-style journal citations."""

import html
import re
import unicodedata

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
SURNAME = r"[^\W\d_]+(?:[-'’][^\W\d_]+)*(?:\s+[^\W\d_]+(?:[-'’][^\W\d_]+)*)*"


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


def fold(text):
    """Case-, accent- and ligature-insensitive form for comparing names."""
    text = unicodedata.normalize('NFKD', unicodedata.normalize('NFKC', text or ''))
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).casefold().split())


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


def surnames(raw):
    """Author surnames listed before the year, in order."""
    head = re.split(r'\b(?:19|20)\d{2}[a-z]?\b', unicodedata.normalize('NFKC', raw), maxsplit=1)[0]
    return re.findall(rf'({SURNAME}),\s*(?:[^\W\d_]\.\s*)+', head)


def pdf_name(surname, year, fingerprint=None):
    """ASCII-safe library filename such as Rappazzo_2013_ApJ_771_76.pdf."""
    def ascii_part(text):
        text = unicodedata.normalize('NFKD', str(text or ''))
        return re.sub(r'[^A-Za-z0-9-]', '', text.encode('ascii', 'ignore').decode())
    parts = [ascii_part(surname), ascii_part(year)]
    if fingerprint:
        parts += [ascii_part(_DISPLAY[fingerprint[0]]), ascii_part(fingerprint[1]), ascii_part(fingerprint[2])]
    return '_'.join(part for part in parts if part) + '.pdf'
