"""Local metadata recovery with optional, corroborated online enrichment."""

from . import arxiv_metadata, crossref_metadata, ocr
from .library import manual_fields
from .metadata import extract_ocr_metadata, useful

OCR_MODES = ("off", "auto", "force")
# A title plus authors needs roughly 100 characters; a born-digital first page has far more
# before its abstract, while a scan carries none or only a stamp such as a repository footer.
SCANT_HEADER = 150


def _lookup(document, cfg, cancel, progress):
    candidates = document["metadata"].get("candidates", {})
    arxiv_first = candidates.get("arxiv") and not document.get("doi")
    providers = [("arxiv", arxiv_metadata.lookup), ("crossref", crossref_metadata.lookup)]
    if not arxiv_first:
        providers.reverse()
    errors = []
    for name, lookup in providers:
        if cancel.is_set():
            break
        if not cfg.enabled(name):
            continue
        progress(name)
        try:
            record = lookup(document, cfg, cancel, fresh=True)
            if record:
                return record, errors
        except Exception as error:
            errors.append(f"{'arXiv' if name == 'arxiv' else 'Crossref'}: {error}")
    return None, errors


def _kept(document):
    """Name the corrections that survived, e.g. 'your corrected title and DOI were kept'."""
    names = [{"doi": "DOI"}.get(key, key) for key in manual_fields(document)]
    if not names:
        return ""
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"your corrected {listed} {'was' if len(names) == 1 else 'were'} kept"


def _local_message(document):
    manual = manual_fields(document)
    missing = [key for key in ("title", "authors", "year") if not document.get(key) and key not in manual]
    if not useful(document["title"]) or document["metadata"].get("sources", {}).get("title") == "arxiv_id":
        missing = list(dict.fromkeys(["title", *missing]))
    return "Local extraction: missing " + ", ".join(missing) + "." if missing else "Local metadata extracted."


def _wants_ocr(document, mode):
    """Auto mode spends OCR time only where native extraction cannot have worked."""
    status = document["metadata_status"]
    # Nothing is left for OCR to fill once the user has typed both fields it recovers.
    if mode == "off" or {"title", "authors"} <= set(manual_fields(document)):
        return False
    if mode == "force":
        return True
    metadata = document["metadata"]
    insufficient = (not useful(document["title"]) or not document["authors"].strip()
                    or metadata.get("sources", {}).get("title") in ("filename", "arxiv_id"))
    scant = (metadata.get("first_page_text_status") != "extracted"
             or len(metadata.get("header_text", "")) < SCANT_HEADER)
    return status != "verified" and insufficient and scant


def _ocr_message(report, document, before):
    gained = [key for key in ("title", "authors") if document[key] != before[key]]
    if gained:
        return f"Recovered {' and '.join(gained)} with offline OCR; review {'them' if len(gained) > 1 else 'it'}.", True
    if report["status"] == "completed":
        return "Offline OCR found no new title or authors.", False
    if report["status"] == "empty":
        return "Offline OCR found no readable text.", False
    return report["message"][:300].strip(), False


def _run_ocr(library, document, cancel, progress):
    report = ocr.recover(library.get_path(document["id"], annotated=False), library.data_dir / "cache" / "ocr",
                         document["sha256"], cancel, language="eng", progress=progress)
    if report["status"] == "cancelled":
        return report, document
    extracted = extract_ocr_metadata(report) if report["status"] in ("completed", "empty") else {}
    updated = library.apply_ocr_metadata(document["id"], extracted, report)
    return report, updated


def refresh(library, document_ids, cfg, online, job, cancel, ocr_mode="auto"):
    result = {"documents": [], "preserved": 0, "local": 0, "verified": 0, "ocr": 0, "errors": [], "cancelled": False}
    total = len(document_ids)
    job["progress"] = {"completed": 0, "total": total}
    for index, document_id in enumerate(document_ids):
        if cancel.is_set():
            break
        document = None
        try:
            before = library.get_document(document_id)
            document = library.refresh_metadata(document_id)
            ocr_note = ""
            if _wants_ocr(document, ocr_mode) and not cancel.is_set():
                reread = document
                job["progress"] = {"completed": index, "total": total, "stage": "ocr"}
                report, document = _run_ocr(library, document, cancel, lambda step: job.update(progress={
                    "completed": index, "total": total, "stage": "ocr", "page": step["page"], "attempt": step["attempt"]}))
                ocr_note, recovered = _ocr_message(report, document, reread)
                result["ocr"] += recovered
            improved = any(document[key] != before[key] for key in ("title", "authors", "year", "doi"))
            result["local"] += improved
            status, message = "local", " ".join(filter(None, [_local_message(document), ocr_note]))
            kept = _kept(document)
            if not online:
                message += " Online lookup is off; extracted values remain reviewable."
            elif not any(cfg.enabled(name) for name in ("arxiv", "crossref")):
                message += " Online metadata sources are disabled."
            else:
                record, errors = _lookup(document, cfg, cancel, lambda provider: job.update(progress={
                    "completed": index, "total": total, "provider": provider}))
                if record and not cancel.is_set():
                    document = library.apply_verified(document_id, record, refresh=True)
                    result["verified"] += document["metadata_status"] == "verified"
                    status = "verified"
                    provider = "arXiv" if record["provider"].lower() == "arxiv" else "Crossref"
                    message = f"Verified using {provider}" + (f"; {kept}." if kept else ".")
                elif errors and not cancel.is_set():
                    status, message = "failed", "Local data kept. " + "; ".join(errors)
                    result["errors"].append({"document_id": document_id, "error": "; ".join(errors)})
                else:
                    message += " No unique online record could be corroborated; review the extracted values."
            if cancel.is_set():
                status, message = "cancelled", "Local data kept; online lookup was cancelled."
            if status != "verified" and kept:
                message += f" {kept[0].upper() + kept[1:]}."
            result["preserved"] += bool(manual_fields(document))
            document = library.record_metadata_refresh(document_id, status, message)
        except Exception as error:
            result["errors"].append({"document_id": document_id, "error": str(error)})
            try:
                library.record_metadata_refresh(document_id, "failed", f"Metadata refresh failed: {error}")
            except Exception:
                pass  # A missing document cannot hold a refresh outcome.
        if document is not None:
            result["documents"].append(document)
        job["progress"] = {"completed": index + 1, "total": total}
    result["cancelled"] = cancel.is_set()
    return result
