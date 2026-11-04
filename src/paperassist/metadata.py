"""Conservative, deterministic PDF metadata extraction."""

import re
from pathlib import Path

from pypdf import PdfReader

DOI = re.compile(r"10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+", re.I)
PARSER_VERSION = 3
ARXIV = re.compile(r"\barxiv\s*:\s*((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)", re.I)


def useful(value: str) -> bool:
    value = value.strip()
    return bool(value and len(value) >= 4 and not re.fullmatch(
        r"(?:untitled|unknown|none|microsoft word.*|document\d*|[^ ]+\.(?:pdf|e?ps|dvi|tex|docx?)|\d{4}[A-Za-z&.]{3,}\d.*)", value, re.I))


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
    try:
        xmp = reader.xmp_metadata
        if xmp:
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
    candidates = {"doi": [], "arxiv": [], "title": [], "year": []}
    text_status = "unavailable"
    try:
        page = reader.pages[0]
        contents = page.get_contents()
        # Extract only small content streams; decoding itself can still be expensive.
        if contents is None or len(contents.get_data()) <= 2_000_000:
            text = page.extract_text()[:100_000]
            lines = [" ".join(line.split()) for line in text.splitlines()[:30]]
            title_lines = [line for line in lines if 12 <= len(line) <= 180
                           and len(line.split()) >= 3 and not re.search(
                               r"(?:arxiv|doi:|https?://|abstract|journal|copyright|©|typeset|latex|preprint|received|accepted|university|università|department|institute|\b20\d{2}\b)", line, re.I)
                           and not re.fullmatch(r"(?:[A-Z][a-z]+|[A-Z]\.)?(?:\s+[A-Z][a-z.]+){1,4}", line)
                           and sum(c.isalpha() for c in line) >= len(line) * 0.65]
            candidates = {
                "doi": sorted({m.group().rstrip(".,;)") for m in DOI.finditer(text)}),
                "arxiv": sorted({m.group(1) for m in ARXIV.finditer(text)}),
                "title": title_lines[:3],
                "year": sorted({int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", text[:10000])}),
            }
            text_status = "extracted"
        else:
            text_status = "too_large"
    except Exception:
        text_status = "failed"
    return {"title": title or Path(filename).stem, "authors": authors, "year": None,
            "doi": None, "pages": pages, "sources": sources, "candidates": candidates,
            "first_page_text_status": text_status, "parser_version": PARSER_VERSION,
            "extraction_status": "partial" if text_status != "extracted" else "complete",
            "candidate_sources": {"doi": "first_page", "arxiv": "first_page",
                                  "title": "first_page_lines", "year": "first_page"}}
