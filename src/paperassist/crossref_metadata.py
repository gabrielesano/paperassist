"""Conservative, bounded Crossref identification for locally imported PDFs."""

import json
import re
from urllib.error import HTTPError

from . import retrieval
from .citation import fold, same_surname
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
    return re.split(r"[,;]|\band\b", document.get("authors") or "", maxsplit=1)[0].strip()


def _name_words(value):
    return " ".join(re.findall(r"[^\W\d_]+", fold(value)))


def _author_agrees(document, record, titles):
    surname = record.get("first_author") or ""
    if author := _author(document):
        return same_surname(surname, author)
    header = document.get("metadata", {}).get("header_text", "")[:4000]
    # A surname in the title is title evidence, not an independent author match.
    header = _name_words(header)
    for title in titles:
        header = header.replace(_name_words(title), " ")
    name = _name_words(surname)
    return bool(name) and f" {name} " in f" {header} "


def _agrees(document, record, titles):
    title = record.get("title") or ""
    if not _distinct(title) or not any(retrieval.same_title(candidate, title) for candidate in titles):
        return False
    year_source = document.get("metadata", {}).get("sources", {}).get("year")
    if document.get("year") and year_source != "arxiv_submission_date" and record.get("year") != document["year"]:
        return False
    return _author_agrees(document, record, titles)


def lookup(document, cfg, cancel, fresh=True):
    """Return one corroborated record; a title search alone never verifies identity."""
    if not cfg.enabled("crossref") or document.get("metadata_status") == "corrected" or cancel.is_set():
        return None
    titles = _titles(document)
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
        if explicit or _agrees(document, record, titles):
            matches[doi.lower()] = record
    if matches or explicit or cancel.is_set():
        return next(iter(matches.values())) if len(matches) == 1 else None
    if not titles or not (_author(document) or document.get("metadata", {}).get("header_text")):
        return None
    query = {"query.bibliographic": titles[0][:300], "select": retrieval.CROSSREF_FIELDS, "rows": 5}
    if author := _author(document):
        query["query.author"] = author[:100]
    url = retrieval._crossref_url(cfg, "works", **query)
    try:
        items = json.loads(retrieval._get(url, cfg, fresh=fresh))["message"]["items"]
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    for item in items[:5]:
        if cancel.is_set():
            return None
        if not item.get("DOI"):
            continue
        record = retrieval._crossref_record(item)
        if _agrees(document, record, titles):
            matches[record["doi"]] = record
    return next(iter(matches.values())) if len(matches) == 1 else None
