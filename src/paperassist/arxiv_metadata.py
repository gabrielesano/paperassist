"""Metadata-only arXiv lookup, corroborated against the PDF's own evidence."""

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

from . import retrieval
from .citation import first_surname, same_surname

ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?", re.I)
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
STRONG_SOURCES = {"pdf_info", "xmp"}


def _base(identifier):
    return re.sub(r"v\d+$", "", identifier.lower())


def _record(identifier, title, authors, published, doi="", method="atom"):
    identifier = identifier.rsplit("/abs/", 1)[-1].strip()
    if not ID.fullmatch(identifier) or not title or not authors or not re.match(r"(?:19|20)\d{2}", published):
        return None
    authors = [" ".join(author.split()) for author in authors if author.strip()]
    if not authors:
        return None
    doi = doi.strip().lower()
    doi = doi if re.fullmatch(r"10\.\d{4,9}/\S+", doi) else None
    return {"provider": "arxiv", "arxiv_id": identifier, "title": " ".join(title.split()),
            "authors": ", ".join(authors), "first_author": authors[0].split()[-1],
            "year": int(published[:4]), "doi": doi,
            "evidence": {"arxiv_id": identifier, "method": method}}


def _atom(data):
    feed = ET.fromstring(data)
    total = feed.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
    records = []
    for entry in feed.findall("atom:entry", NS):
        record = _record(entry.findtext("atom:id", "", NS), entry.findtext("atom:title", "", NS),
                         [author.findtext("atom:name", "", NS) for author in entry.findall("atom:author", NS)],
                         entry.findtext("atom:published", "", NS), entry.findtext("arxiv:doi", "", NS))
        if record:
            records.append(record)
    return records, int(total) if total and total.isdigit() else len(records)


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
        data = retrieval._get("https://arxiv.org/abs/" + quote(identifier, safe="/"), cfg, fresh=fresh)
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
    if record and _base(record["arxiv_id"]) != _base(identifier):
        raise ValueError("arXiv returned a different identifier")
    return record


def _titles(document):
    metadata = document.get("metadata", {})
    titles = list(metadata.get("candidates", {}).get("title", []))
    if metadata.get("sources", {}).get("title") != "filename":
        titles.insert(0, document.get("title") or "")
    return [title for title in dict.fromkeys(titles) if len(title.split()) >= 3]


def _title_agrees(record, document, titles):
    return retrieval.title_agrees(record["title"], titles, document.get("metadata", {}).get("header_text", ""))


def _author_agrees(record, document):
    if document.get("authors"):
        return same_surname(record["first_author"], first_surname(document["authors"]))
    return retrieval.surname_in_header(record["first_author"], document.get("metadata", {}).get("header_text", ""), record["title"])


def _identifiers(values, sources):
    groups = {}
    sources = {value.lower(): source for value, source in sources.items()}
    for value in values:
        if ID.fullmatch(value):
            groups.setdefault(_base(value), set()).add(value.lower())
    selected = []
    for base, values in sorted(groups.items()):
        versions = {value for value in values if re.search(r"v\d+$", value)}
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
    if not cfg.enabled("arxiv") or document.get("metadata_status") == "corrected" or cancel.is_set():
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
        words = sorted({word for word in retrieval._words(titles[0]) if len(word) > 3 and word.isascii()},
                       key=lambda word: (-len(word), word))[:6]
        if len(words) < 2:
            return None
        surname = re.sub(r'[^\w -]', "", author)
        params = {"search_query": " AND ".join([f'au:"{surname}"', *(f"ti:{word}" for word in words)]),
                  "max_results": 10}
    try:
        records, total = _atom(retrieval._get("https://export.arxiv.org/api/query?" + urlencode(params), cfg, fresh=fresh))
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
    requested = {_base(identifier) for identifier in identifiers}
    strong = {_base(identifier) for identifier, source in sources.items() if source in STRONG_SOURCES}
    matches = {}
    for record in records:
        identifier = _base(record["arxiv_id"])
        if identifiers and identifier not in requested:
            continue
        title = _title_agrees(record, document, titles)
        author = _author_agrees(record, document)
        author_available = bool(document.get("authors") or metadata.get("header_text"))
        corroborated = title and (author or (identifiers and not author_available))
        requested_versions = [value for value in identifiers if _base(value) == identifier]
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
