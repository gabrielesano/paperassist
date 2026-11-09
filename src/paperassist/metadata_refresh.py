"""Local metadata recovery with optional, corroborated online enrichment."""

from . import arxiv_metadata, crossref_metadata
from .metadata import useful


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


def _local_message(document):
    missing = [key for key in ("title", "authors", "year") if not document.get(key)]
    if not useful(document["title"]) or document["metadata"].get("sources", {}).get("title") == "arxiv_id":
        missing = list(dict.fromkeys(["title", *missing]))
    return "Local extraction: missing " + ", ".join(missing) + "." if missing else "Local metadata extracted."


def refresh(library, document_ids, cfg, online, job, cancel):
    result = {"documents": [], "preserved": 0, "local": 0, "verified": 0, "errors": [], "cancelled": False}
    total = len(document_ids)
    job["progress"] = {"completed": 0, "total": total}
    for index, document_id in enumerate(document_ids):
        if cancel.is_set():
            break
        document = None
        try:
            before = library.get_document(document_id)
            document = library.refresh_metadata(document_id)
            improved = any(document[key] != before[key] for key in ("title", "authors", "year", "doi"))
            result["local"] += improved
            status, message = "local", _local_message(document)
            if document["metadata_status"] == "corrected":
                message = "Manual corrections preserved."
            elif not online:
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
                    message = f"Verified using {provider}."
                elif errors and not cancel.is_set():
                    status, message = "failed", "Local data kept. " + "; ".join(errors)
                    result["errors"].append({"document_id": document_id, "error": "; ".join(errors)})
                else:
                    message += " No unique online record could be corroborated; review the extracted values."
            if cancel.is_set() and document["metadata_status"] != "corrected":
                status, message = "cancelled", "Local data kept; online lookup was cancelled."
            if document["metadata_status"] == "corrected":
                result["preserved"] += 1
                status, message = "preserved", "Manual corrections preserved."
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
