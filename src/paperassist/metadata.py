"""Conservative, deterministic PDF metadata extraction."""

import math
import re
import statistics
from pathlib import Path
from urllib.parse import unquote

from pypdf import PdfReader

DOI = re.compile(r"10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+", re.I)
PARSER_VERSION = 4
ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
ARXIV = re.compile(rf"(?:\barxiv\s*:\s*|https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)({ARXIV_ID})", re.I)
GENERIC = re.compile(r"(?:download|document|file|untitled|unknown)(?:[\s_()-]*\d+[\s_()-]*)?|[\d_.-]+(?:v\d+)?|[a-f\d]{16,}", re.I)
BOILERPLATE = re.compile(
    r"arxiv|doi\s*:|https?://|abstract|journal|copyright|©|typeset|latex|preprint|received|accepted|"
    r"university|università|department|institute|laboratory|college|conference|proceedings|"
    r"correspondence|contribut(?:ed|ion)|project page|\b20\d{2}\b", re.I)
PERSON = re.compile(r"(?:[A-Z][a-z]+|[A-Z]\.)(?:\s+(?:[A-Z][a-z]+|[A-Z]\.)){1,3}")


def useful(value: str) -> bool:
    value = value.strip()
    return bool(value and len(value) >= 4 and not GENERIC.fullmatch(value) and not re.fullmatch(
        r"(?:untitled|unknown|none|microsoft word.*|document\d*|[^ ]+\.(?:pdf|e?ps|dvi|tex|docx?)|\d{4}[A-Za-z&.]{3,}\d.*)", value, re.I))


def _header(text):
    lines = []
    for line in text.splitlines()[:30]:
        line = " ".join(line.split())
        if re.match(r"^(?:\d+\.?\s+)?(?:abstract|introduction)\b", line, re.I):
            break
        if line:
            lines.append(line)
    return lines


def _title_line(line):
    return (12 <= len(line) <= 220 and len(line.split()) >= 2 and not BOILERPLATE.search(line)
            and sum(c.isalpha() for c in line) >= len(line) * .65)


def _prominent_title(spans, page):
    """A large header font can supply a readable provisional title, never a confirmed identity."""
    height = float(getattr(getattr(page, "mediabox", None), "height", 0))
    if not height or getattr(page, "rotation", 0) or len(spans) < 3:
        return ""
    body_size = statistics.median(size for _, _, size, _ in spans)
    boundary = max((y for _, y, _, text in spans if re.match(r"\s*(?:abstract|introduction)\b", text, re.I)), default=0)
    lines = []
    for x, y, size, text in spans:
        if lines and abs(lines[-1][1] - y) < 1 and min(lines[-1][2], size) >= max(lines[-1][2], size) * .75:
            old_x, old_y, old_size, old_text = lines[-1]
            if size < old_size * .9 and re.search(r"\b[A-Z]$", old_text) and re.match(r" [A-Z]{2,}", text):
                text = text.lstrip()
            separated = abs(old_size - size) < .5 and not old_text.endswith(" ") and not text.startswith(" ")
            gap = " " if separated and x > old_x + len(old_text) * size * .5 else ""
            lines[-1] = (old_x, old_y, max(old_size, size), old_text + gap + text)
        else:
            lines.append((x, y, size, text))
    candidates = [(x, y, size, " ".join(text.split())) for x, y, size, text in lines
                  if max(height * .55, boundary) < y < height * .96 and
                  (size >= max(11, body_size * 1.18) or text.isupper() and
                   (size >= body_size * 1.08 or len(text.split()) >= 8 and size >= body_size * .9))]
    if not candidates:
        return ""
    largest = max(size for _, _, size, _ in candidates if size > 0)
    selected = []
    for _, y, size, text in sorted(candidates, key=lambda line: -line[1]):
        continuation = selected and selected[-1][1].endswith(":") and size >= largest * .68
        if abs(size - largest) > largest * .08 and not continuation:
            continue
        smaller_names = any(other_y < y and other_size < size * .85 and PERSON.fullmatch(other_text.strip())
                            for _, other_y, other_size, other_text in spans)
        title_continuation = (selected and 4 <= len(text) <= 180 and not BOILERPLATE.search(text)
                              and sum(char.isalpha() for char in text) >= len(text) * .45)
        if not (_title_line(text) or title_continuation) or (PERSON.fullmatch(text) and not selected and not
                                    (len(text.split()) >= 4 and size >= body_size * 1.5 and smaller_names)):
            continue
        if selected and selected[-1][0] - y > largest * 1.8:
            break
        selected.append((y, text))
        if len(selected) == 4:
            break
    title = " ".join(text for _, text in selected)
    title = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", title)
    title = re.sub(r"(?<=[A-Z])-\s+(?=[A-Z])", "", title)
    title = re.sub(r"\s+-\s*", "-", title)
    return title if 12 <= len(title) <= 300 and len(title.split()) >= 3 else ""


def _header_authors(lines, title):
    if not title:
        return ""
    normalized_title = re.sub(r"\W", "", title).casefold()
    start = max((i + 1 for i, line in enumerate(lines[:6]) if len(line) >= 8 and
                 re.sub(r"\W", "", line).casefold() in normalized_title), default=1)
    found = []
    name = re.compile(r"(?:[^\W\d_][\w’'’-]*\.?|[A-Z](?:\.[A-Z])*\.|van|von|de|der|den|del|la|le)", re.UNICODE)
    for line in lines[start:]:
        if re.search(r"\b(?:Figure|Project Page)\b", line, re.I):
            break
        if BOILERPLATE.search(line) or re.search(r"@|\b(?:Tech|Research|Centre|Center|School|Street|Parkway|USA|Ltd|Labs|AI|team)\b", line, re.I):
            continue
        line = re.sub(r"(?<=[^\W\d_])\d+", "", line)
        line = re.sub(r"[*†‡∗∧♯¶]+", ";", line)
        line = re.sub(r"[\d¨´]", "", line)
        parts = [part.strip(" ,;") for part in re.split(r"[,;]|\s+(?:and|&)\s+", line)]
        if not parts or any(not 2 <= len(part.split()) <= 5 or
                            any(not name.fullmatch(word) for word in part.split()) or
                            not any(char.isupper() for char in part) or re.search(r"\b[A-Z]\.$", part)
                            for part in parts if part):
            continue
        # Every non-particle word starts with a capital; this excludes ordinary body prose.
        if any(any(word[0].islower() and word not in ("van", "von", "de", "der", "den", "del", "la", "le")
                   for word in part.split()) for part in parts if part):
            continue
        found.extend(part for part in parts if part)
    return "; ".join(dict.fromkeys(found))[:2000]


def _arxiv_year(text, identifiers):
    base = lambda value: re.sub(r"v\d+$", "", value.lower())
    strong = {base(value) for value, source in identifiers.items() if source in ("pdf_info", "xmp")}
    own = strong or {base(value) for value in identifiers}
    if len(own) != 1:
        return None
    dates = set()
    for match in ARXIV.finditer(text):
        if base(match.group(1)) not in own:
            continue
        stamp = re.match(r"\s*(?:\[[^\]\n]{1,30}\]\s*)?\d{1,2}\s+"
                         r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
                         r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+((?:19|20)\d{2})\b",
                         text[match.end():match.end() + 100], re.I)
        if stamp:
            year = int(stamp.group(1))
            prefix = int(match.group(1).rsplit("/", 1)[-1][:2])
            initial_year = (1900 if prefix >= 91 else 2000) + prefix
            # A later revision's stamp is not the original paper's year.
            if year == initial_year:
                dates.add(year)
    return next(iter(dates)) if len(dates) == 1 else None


def extract_metadata(path: Path, filename: str) -> dict:
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF cannot be imported")
    pages = len(reader.pages)
    if not pages:
        raise ValueError("PDF has no pages")
    info = reader.metadata
    title = str(info.title or "").strip() if info else ""
    authors = str(info.author or "").strip() if info else ""
    title = title if useful(title) else ""
    authors = authors if useful(authors) else ""
    sources = {"title": "pdf_info" if title else "filename", "authors": "pdf_info" if authors else None}
    identifier_sources = {"doi": {}, "arxiv": {}}

    def identifiers(value, source, kind=None, bare=False):
        value = unquote(str(value or ""))
        if kind in (None, "doi"):
            for match in DOI.finditer(value):
                identifier_sources["doi"].setdefault(match.group().rstrip(".,;)").lower(), source)
        if kind in (None, "arxiv"):
            values = [match.group(1) for match in ARXIV.finditer(value)]
            values += re.findall(rf"10\.48550/arxiv\.({ARXIV_ID})", value, re.I)
            if bare and re.fullmatch(ARXIV_ID, value.strip(), re.I):
                values.append(value.strip())
            for arxiv in values:
                identifier_sources["arxiv"].setdefault(arxiv, source)

    info_get = getattr(info, "get", lambda key: None)
    for key in ("/DOI", "/doi"):
        identifiers(info_get(key), "pdf_info", "doi")
    for key in ("/arXivID", "/ArXivID", "/arxiv_id"):
        identifiers(info_get(key), "pdf_info", "arxiv", bare=True)
    try:
        xmp = reader.xmp_metadata
        if xmp:
            xmp_ids = getattr(xmp, "dc_identifier", None) or []
            for value in [xmp_ids] if isinstance(xmp_ids, str) else xmp_ids:
                identifiers(value, "xmp", bare=True)
            if not title and xmp.dc_title:
                title = next(iter(xmp.dc_title.values()), "").strip()
                if useful(title):
                    sources["title"] = "xmp"
                else:
                    title = ""
            if not authors and xmp.dc_creator:
                authors = ", ".join(xmp.dc_creator)
                sources["authors"] = "xmp"
    except Exception:
        pass
    identifiers(Path(filename).stem, "filename", bare=True)
    candidates = {"doi": [], "arxiv": [], "title": [], "year": []}
    text_status = "unavailable"
    header_text = ""
    year = None
    try:
        page = reader.pages[0]
        contents = page.get_contents()
        # Extract only small content streams; decoding itself can still be expensive.
        if contents is None or len(contents.get_data()) <= 2_000_000:
            spans = []
            def visit(text, cm, tm, font, size):
                if text.strip() and len(spans) < 2000:
                    scale = math.hypot(tm[2] * cm[0] + tm[3] * cm[2], tm[2] * cm[1] + tm[3] * cm[3])
                    if scale and size:
                        spans.append((tm[4] * cm[0] + tm[5] * cm[2] + cm[4],
                                      tm[4] * cm[1] + tm[5] * cm[3] + cm[5], abs(size) * scale, text))
            text = page.extract_text(visitor_text=visit)[:100_000]
            lines = _header(text)
            header_text = "\n".join(lines)[:4000]
            prominent = _prominent_title(spans, page)
            title_lines = ([prominent] if prominent else []) + [line for line in lines
                           if _title_line(line) and len(line.split()) >= 3 and not PERSON.fullmatch(line)]
            title_lines = list(dict.fromkeys(title_lines))
            if not title and GENERIC.fullmatch(Path(filename).stem) and prominent:
                title, sources["title"] = prominent, "first_page_typography"
            identifiers(text, "first_page")
            if not authors:
                authors = _header_authors(lines, title or prominent)
                if authors:
                    sources["authors"] = "first_page_authors"
            year = _arxiv_year(text, identifier_sources["arxiv"])
            if year:
                sources["year"] = "arxiv_submission_date"
            candidates = {
                "doi": [], "arxiv": [],
                "title": title_lines[:3],
                "year": sorted({int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", text[:10000])}),
            }
            text_status = "extracted"
        else:
            text_status = "too_large"
    except Exception:
        text_status = "failed"
    for kind in ("doi", "arxiv"):
        candidates[kind] = sorted(identifier_sources[kind])
    if not title and GENERIC.fullmatch(Path(filename).stem) and len(candidates["arxiv"]) == 1:
        title, sources["title"] = f"arXiv:{candidates['arxiv'][0]}", "arxiv_id"
    return {"title": title or Path(filename).stem, "authors": authors, "year": year,
            "doi": None, "pages": pages, "sources": sources, "candidates": candidates,
            "first_page_text_status": text_status, "parser_version": PARSER_VERSION,
            "extraction_status": "partial" if text_status != "extracted" else "complete",
            "header_text": header_text, "identifier_sources": identifier_sources,
            "candidate_sources": {"doi": "first_page", "arxiv": "first_page",
                                  "title": "first_page_lines", "year": "first_page"}}
