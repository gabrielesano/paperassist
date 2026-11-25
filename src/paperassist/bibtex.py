"""BibTeX formatting and export from verified work metadata."""

import re
import unicodedata

from .citation import first_surname, journal_name

_FIELDS = ("title", "authors", "year", "doi")
_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_LATEX_PATTERN = re.compile(r"[\\{}&%$#_~^]")
_INITIALS_ONLY = re.compile(
    r"^(?:[A-Z]\.(?:\s*-?\s*[A-Z]\.)*|[A-Z]{1,3})(?:\s+(?:Jr\.?|Sr\.?|II|III|IV))?$"
)
_ET_AL = re.compile(r"(?:,?\s+|\s+)et\s+al\.?$", re.I)


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in BibTeX text fields without double-escaping."""
    return _LATEX_PATTERN.sub(lambda match: _LATEX_ESCAPES[match.group(0)], text)


def _manual_fields(document: dict) -> list[str]:
    metadata = document.get("metadata") or {}
    if "manual" in metadata:
        return metadata["manual"]
    if document.get("metadata_status") != "corrected":
        return []
    verified = metadata.get("verified") or {}
    return [
        key for key in _FIELDS
        if document.get(key) not in (None, "")
        and document[key] not in (metadata.get(key) if key != "doi" else None, verified.get(key))
    ]


def _is_verified_document(document: dict) -> bool:
    if not isinstance(document, dict):
        return False
    verified = (document.get("metadata") or {}).get("verified") or {}
    if not isinstance(verified, dict):
        verified = {}
    has_verified = any(verified.get(key) for key in ("provider", "doi", "arxiv_id", "bibcode", "publication"))
    return document.get("metadata_status") == "verified" or has_verified


def _split_authors(authors: str) -> list[str]:
    cleaned = " ".join(str(authors or "").split())
    if not cleaned:
        return []
    has_et_al = bool(_ET_AL.search(cleaned))
    if has_et_al:
        cleaned = _ET_AL.sub("", cleaned).strip(" ,;")
    if ";" in cleaned:
        segments = [part.strip() for part in cleaned.split(";") if part.strip()]
    else:
        segments = [part.strip() for part in re.split(r",?\s+(?:&|\band\b)\s+", cleaned, flags=re.I) if part.strip()]
    result: list[str] = []
    for segment in segments:
        if "," not in segment:
            result.append(segment)
            continue
        parts = [part.strip() for part in segment.split(",") if part.strip()]
        if len(parts) >= 2 and len(parts) % 2 == 0 and all(_INITIALS_ONLY.fullmatch(parts[i]) for i in range(1, len(parts), 2)):
            result.extend(f"{parts[i]}, {parts[i + 1]}" for i in range(0, len(parts), 2))
        else:
            result.extend(parts)
    if has_et_al:
        result.append("others")
    return result


def _ascii_alnum(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", ascii_only)


def _key_suffix(index: int) -> str:
    chars = []
    n = index
    while True:
        chars.append(chr(ord("a") + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "".join(reversed(chars))


def _citation_key(surname: str, year: str | int | None, used_keys: set[str] | None) -> str:
    stem = _ascii_alnum(surname) or "Paper"
    year_part = re.sub(r"\D", "", str(year or ""))[:4]
    base = f"{stem}{year_part}"
    if used_keys is None:
        return base
    taken = {existing.casefold() for existing in used_keys}
    if base.casefold() not in taken:
        used_keys.add(base)
        return base
    index = 0
    while True:
        candidate = f"{base}{_key_suffix(index)}"
        if candidate.casefold() not in taken:
            used_keys.add(candidate)
            return candidate
        index += 1


def _publication_journal(pub) -> str:
    if not pub or not pub[0]:
        return ""
    code = str(pub[0])
    try:
        return journal_name(code)
    except KeyError:
        return code


def format_document_bibtex(document: dict, *, used_keys: set[str] | None = None) -> str | None:
    """Format a verified library document as a BibTeX entry, or None when unverified."""
    if not _is_verified_document(document):
        return None
    verified = (document.get("metadata") or {}).get("verified") or {}
    if not isinstance(verified, dict):
        verified = {}
    manual = set(_manual_fields(document))
    pub = verified.get("publication")
    if not isinstance(pub, (list, tuple)):
        pub = None

    title = (document.get("title") or "") if "title" in manual else (document.get("title") or verified.get("title") or "")
    authors_raw = (document.get("authors") or "") if "authors" in manual else (document.get("authors") or verified.get("authors") or "")
    year = document.get("year") if "year" in manual else (
        document.get("year") or verified.get("year") or (pub[3] if pub and len(pub) >= 4 else None)
    )
    doi = document.get("doi") if "doi" in manual else (document.get("doi") or verified.get("doi"))

    journal = str(verified.get("journal") or "").strip() or _publication_journal(pub)
    volume = str(pub[1]).strip() if pub and len(pub) >= 2 and pub[1] else str(verified.get("volume") or "").strip()
    pages = str(pub[2]).strip() if pub and len(pub) >= 3 and pub[2] else str(verified.get("pages") or verified.get("page") or "").strip()
    arxiv_id = str(verified.get("arxiv_id") or "").strip()
    if not arxiv_id and doi and str(doi).lower().startswith("10.48550/arxiv."):
        arxiv_id = str(doi)[len("10.48550/arxiv."):].strip()
    bibcode = str(verified.get("bibcode") or "").strip()

    surname = first_surname(authors_raw) if "authors" in manual else (verified.get("first_author") or first_surname(authors_raw))
    key = _citation_key(surname, year, used_keys)
    entry_type = "article" if (journal or pub) else "misc"

    fields: list[tuple[str, str]] = []
    author_list = _split_authors(authors_raw)
    if author_list:
        formatted_authors = " and ".join("others" if item == "others" else escape_latex(item) for item in author_list)
        fields.append(("author", formatted_authors))
    if title:
        fields.append(("title", escape_latex(" ".join(str(title).split()))))
    if journal:
        fields.append(("journal", escape_latex(journal)))
    if year:
        fields.append(("year", str(year).strip()))
    if volume:
        fields.append(("volume", escape_latex(volume)))
    if pages:
        fields.append(("pages", escape_latex(pages)))
    if doi:
        fields.append(("doi", str(doi).strip()))
    if arxiv_id:
        fields.append(("eprint", arxiv_id))
        fields.append(("archivePrefix", "arXiv"))
    if bibcode:
        fields.append(("bibcode", bibcode))

    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@{entry_type}{{{key},\n{body}\n}}" if body else f"@{entry_type}{{{key}}}"


def format_reference_bibtex(ref: dict, linked_document: dict | None = None) -> str | None:
    """Format BibTeX from a verified linked document or a reference's verified identity."""
    if isinstance(linked_document, dict):
        formatted = format_document_bibtex(linked_document)
        if formatted:
            return formatted
    if not isinstance(ref, dict):
        return None
    identity = ref.get("identity")
    if not isinstance(identity, dict):
        return None
    if not any(identity.get(key) for key in ("provider", "doi", "arxiv_id", "bibcode", "publication")):
        return None
    synthetic = {
        "title": identity.get("title") or ref.get("title") or "",
        "authors": identity.get("authors") or ref.get("authors") or "",
        "year": identity.get("year") or ref.get("year"),
        "doi": identity.get("doi") or ref.get("doi"),
        "metadata_status": "verified",
        "metadata": {"verified": identity},
    }
    return format_document_bibtex(synthetic)


def export_documents_bibtex(documents: list[dict]) -> tuple[str, int, int]:
    """Export verified documents to BibTeX; return (text, exported_count, skipped_unverified_count)."""
    used_keys: set[str] = set()
    entries: list[str] = []
    skipped = 0
    for document in documents:
        formatted = format_document_bibtex(document, used_keys=used_keys)
        if formatted:
            entries.append(formatted)
        else:
            skipped += 1
    return "\n\n".join(entries), len(entries), skipped
