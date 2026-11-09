"""Conservative, bounded Crossref identification for locally imported PDFs."""

import json
import re
from urllib.error import HTTPError

from . import citation, retrieval
from .citation import first_surname, same_surname
from .metadata import useful


def _distinct(title):
    words = retrieval._words(title)
    return useful(title) and len(set(words)) >= 3 and sum(map(len, words)) >= 18


def _titles(document):
    metadata = document.get("metadata", {})
    title = document.get("title") or ""
    if metadata.get("sources", {}).get("title") == "arxiv_id":
        title = ""
    values = [title, *metadata.get("candidates", {}).get("title", [])]
    return list(dict.fromkeys(value for value in values if _distinct(value)))[:5]


def _author(document):
    """The first listed author as printed, for the search query."""
    return re.split(r"[,;]|\band\b", re.sub(r"\bet\s+al\b\.?", "", document.get("authors") or "", flags=re.I), maxsplit=1)[0].strip()


def _author_agrees(document, record, header):
    surname = record.get("first_author") or ""
    if document.get("authors"):
        return same_surname(surname, first_surname(document["authors"]))
    return retrieval.surname_in_header(surname, header, record.get("title") or "")


def _agrees(document, record, titles, printed):
    title = record.get("title") or ""
    metadata = document.get("metadata", {})
    header = metadata.get("header_text", "")[:4000]
    by_title = _distinct(title) and retrieval.title_agrees(title, titles, header)
    # Journal, volume and first page identify one paper; the first author still has to agree.
    by_reference = (record.get("publication") or [])[:3] in [value[:3] for value in printed]
    if not (by_title or by_reference):
        return False
    year_source = metadata.get("sources", {}).get("year")
    if document.get("year") and year_source != "arxiv_submission_date" and record.get("year") != document["year"]:
        return False
    return _author_agrees(document, record, header)


def _queries(document, titles, printed):
    """Bounded searches: by title, then by the journal reference the PDF prints."""
    queries = []
    author = {"query.author": _author(document)[:100]} if _author(document) else {}
    if titles:
        queries.append({"query.bibliographic": titles[0][:300], **author})
    for journal, volume, page, year in printed[:2]:
        name = citation.JOURNALS[citation.journal_name(journal)][-1]
        queries.append({"query.bibliographic": f"{name} {volume} {page}", **author,
                        "filter": f"from-pub-date:{int(year) - 1},until-pub-date:{int(year) + 1}"})
    return queries[:2]


def lookup(document, cfg, cancel, fresh=True):
    """Return one corroborated record; a title search alone never verifies identity."""
    if not cfg.enabled("crossref") or document.get("metadata_status") == "corrected" or cancel.is_set():
        return None
    titles = _titles(document)
    printed = document.get("metadata", {}).get("candidates", {}).get("publication", [])
    explicit = document.get("doi")
    candidates = document.get("metadata", {}).get("candidates", {}).get("doi", [])
    dois = [explicit] if explicit else sorted({value.lower() for value in candidates})
    matches = {}
    for doi in dois if len(dois) <= 5 else []:
        if cancel.is_set():
            return None
        try:
            record = retrieval.crossref_metadata(doi, cfg, fresh=fresh)
        except HTTPError as error:
            if error.code == 404:
                continue
            raise
        if cancel.is_set():
            return None
        if (record.get("doi") or "").lower() != doi.lower():
            raise ValueError("Crossref returned a different DOI")
        if explicit or _agrees(document, record, titles, printed):
            matches[doi.lower()] = record
    if matches or explicit or cancel.is_set():
        return next(iter(matches.values())) if len(matches) == 1 else None
    if not (document.get("authors") or document.get("metadata", {}).get("header_text")):
        return None
    for query in _queries(document, titles, printed):
        url = retrieval._crossref_url(cfg, "works", **query, select=retrieval.CROSSREF_FIELDS, rows=5)
        try:
            items = json.loads(retrieval._get(url, cfg, fresh=fresh))["message"]["items"]
        except HTTPError as error:
            if error.code == 404:
                continue
            raise
        for item in items[:5]:
            if cancel.is_set():
                return None
            if not item.get("DOI"):
                continue
            record = retrieval._crossref_record(item)
            if _agrees(document, record, titles, printed):
                matches[record["doi"]] = record
        if matches:
            break
    return next(iter(matches.values())) if len(matches) == 1 else None
