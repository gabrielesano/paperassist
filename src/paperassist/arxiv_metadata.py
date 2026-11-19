"""Metadata-only arXiv lookup, corroborated against the PDF's own evidence."""

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import quote

from . import http_client, retrieval
from .citation import ARXIV_ID, first_surname, normalize_doi, strip_arxiv_version

STRONG_SOURCES = {"pdf_info", "xmp"}


def _record(identifier, title, authors, published, doi="", method="atom"):
    identifier = identifier.rsplit("/abs/", 1)[-1].strip()
    if not ARXIV_ID.fullmatch(identifier) or not title or not authors or not re.match(r"(?:19|20)\d{2}", published):
        return None
    authors = [" ".join(author.split()) for author in authors if author.strip()]
    if not authors:
        return None
    return {"provider": "arxiv", "arxiv_id": identifier, "title": " ".join(title.split()),
            "authors": ", ".join(authors), "first_author": authors[0].split()[-1],
            "year": int(published[:4]), "doi": normalize_doi(doi, strict=False),
            "evidence": {"arxiv_id": identifier, "method": method}}


class _CitationMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        name = values.get("name", "").lower()
        if tag == "meta" and name.startswith("citation_"):
            self.values.setdefault(name, []).append(values.get("content", ""))


def _abstract(identifier, cfg, fresh):
    parser = _CitationMeta()
    try:
        data = http_client.get("https://arxiv.org/abs/" + quote(identifier, safe="/"), cfg, fresh=fresh)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    parser.feed(data.decode("utf-8", errors="replace"))
    values = parser.values
    first = lambda key: (values.get(key) or [""])[0]
    authors = []
    for author in values.get("citation_author", []):
        family, comma, given = author.partition(",")
        authors.append(f"{given.strip()} {family.strip()}" if comma else author)
    record = _record(first("citation_arxiv_id"), first("citation_title"), authors,
                     first("citation_date") or first("citation_online_date"), first("citation_doi"), "abstract_meta")
    if record and strip_arxiv_version(record["arxiv_id"]) != strip_arxiv_version(identifier):
        raise ValueError("arXiv returned a different identifier")
    return record


def _titles(document):
    metadata = document.get("metadata", {})
    titles = list(metadata.get("candidates", {}).get("title", []))
    if metadata.get("sources", {}).get("title") != "filename":
        titles.insert(0, document.get("title") or "")
    return [title for title in dict.fromkeys(titles) if len(title.split()) >= 3]


def _identifiers(values, sources):
    groups = {}
    sources = {value.lower(): source for value, source in sources.items()}
    for value in values:
        if ARXIV_ID.fullmatch(value):
            groups.setdefault(strip_arxiv_version(value), set()).add(value.lower())
    selected = []
    for base, versions_all in sorted(groups.items()):
        versions = {value for value in versions_all if re.search(r"v\d+$", value)}
        strong = {value for value in versions if sources.get(value) in STRONG_SOURCES}
        if len(strong) > 1:
            return None
        if strong:
            selected.append(next(iter(strong)))
        elif versions:
            selected.append(max(versions, key=lambda value: int(value.rsplit("v", 1)[1])))
        else:
            selected.append(base)
    return selected


def lookup(document, cfg, cancel, *, fresh=True):
    """Return one corroborated record, or None; propagate transport failures for reporting.

    Explicit PDF Info/XMP identifiers establish identity. First-page and filename
    identifiers need a matching title and available author evidence. A search
    without identifiers always needs both title and first-author agreement.
    """
    if not cfg.enabled("arxiv") or cancel.is_set():
        return None
    metadata = document.get("metadata", {})
    candidates = metadata.get("candidates", {})
    sources = metadata.get("identifier_sources", {}).get("arxiv", {})
    identifiers = _identifiers(candidates.get("arxiv", []), sources)
    if identifiers is None or len(identifiers) > 5:
        return None
    titles = _titles(document)
    if identifiers:
        params = {"id_list": ",".join(identifiers), "max_results": len(identifiers)}
    else:
        author = first_surname(document.get("authors"))
        if not titles or not author:
            return None
        words = sorted({word for word in retrieval.title_words(titles[0]) if len(word) > 3 and word.isascii()},
                       key=lambda word: (-len(word), word))[:6]
        if len(words) < 2:
            return None
        surname = re.sub(r'[^\w -]', "", author)
        params = {"search_query": " AND ".join([f'au:"{surname}"', *(f"ti:{word}" for word in words)]),
                  "max_results": 10}
    try:
        entries, total = retrieval.query_arxiv(params, cfg, fresh=fresh)
        records = [record for entry in entries if (record := _record(
            entry["id"], entry["title"], entry["authors"], entry["published"], entry["doi"]))]
    except (OSError, ET.ParseError):
        if not identifiers or len(identifiers) > 2:
            raise
        records = []
        for identifier in identifiers:
            if cancel.is_set():
                return None
            record = _abstract(identifier, cfg, fresh)
            if record:
                records.append(record)
        total = len(records)
    if cancel.is_set() or (not identifiers and total > 10):
        return None
    requested = {strip_arxiv_version(identifier) for identifier in identifiers}
    strong = {strip_arxiv_version(identifier) for identifier, source in sources.items() if source in STRONG_SOURCES}
    header = metadata.get("header_text", "")
    matches = {}
    for record in records:
        identifier = strip_arxiv_version(record["arxiv_id"])
        if identifiers and identifier not in requested:
            continue
        title = retrieval.title_agrees(record["title"], titles, header)
        author = retrieval.author_agrees(document, record, header)
        author_available = bool(document.get("authors") or header)
        corroborated = title and (author or (identifiers and not author_available))
        requested_versions = [value for value in identifiers if strip_arxiv_version(value) == identifier]
        exact = any(not re.search(r"v\d+$", value) or value == record["arxiv_id"].lower() for value in requested_versions)
        if requested_versions and not exact:
            unversioned_html = record["evidence"]["method"] == "abstract_meta" and not re.search(r"v\d+$", record["arxiv_id"])
            if not (unversioned_html and corroborated):
                continue
        doi = (document.get("doi") or "").lower()
        own_arxiv_doi = doi == "10.48550/arxiv." + identifier
        if doi and record.get("doi") and doi != record["doi"] and not own_arxiv_doi:
            continue
        if identifier in strong or corroborated:
            if not identifiers and document.get("year") and abs(record["year"] - document["year"]) > 1:
                continue
            matches[identifier] = record
    return next(iter(matches.values())) if len(matches) == 1 else None
