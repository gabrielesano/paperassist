"""Local PDF storage, metadata and notes."""

import hashlib
from contextlib import contextmanager
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from .citation import pdf_name
from .metadata import PARSER_VERSION, extract_metadata, useful

MAX_LABEL = 80
RESERVED_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)
# Names that say nothing about the paper; the document id is a better label than these.
GENERIC_NAME = re.compile(r"(?:download|document|file|untitled)[\d_()-]*|[\d_-]+|[0-9a-f]{16,}", re.I)


def safe_filename(name: str) -> str:
    """Portable PDF file name (Linux, macOS, Windows), or '' when nothing meaningful is left."""
    name = re.sub(r"\.pdf$", "", unicodedata.normalize("NFC", name), flags=re.I)
    name = "".join("\0" if c in '<>:"/\\|?*' or c.isspace() or unicodedata.category(c) in ("Cc", "Cf", "Cs") else c
                   for c in name)
    stem = re.sub(r"\0+", "_", name)[:120]
    while len(stem.encode()) > 200:  # file systems limit bytes, not characters
        stem = stem[:-1]
    # Leading dots would hide the file; Windows drops trailing dots and spaces.
    stem = stem.strip("._ ")
    if not stem or GENERIC_NAME.fullmatch(stem):
        return ""
    return f"_{stem}.pdf" if RESERVED_NAME.fullmatch(stem) else f"{stem}.pdf"


FIELDS = ("title", "authors", "year", "doi")


def manual_fields(document) -> list[str]:
    """The fields the user typed; no automatic source may replace them."""
    metadata = document.get("metadata") or {}
    if "manual" in metadata:
        return metadata["manual"]
    if document.get("metadata_status") != "corrected":
        return []
    # A paper corrected before fields were recorded: a value is the user's unless stored automatic
    # evidence explains it, because guessing "automatic" would let a refresh overwrite a real correction.
    verified = metadata.get("verified") or {}
    return [key for key in FIELDS if document.get(key) not in (None, "")
            and document[key] not in (metadata.get(key) if key != "doi" else None, verified.get(key))]


def _recorded(document, metadata):
    """Persist an inferred legacy list, because later rereads replace the evidence it was inferred from."""
    if "manual" in metadata or document["metadata_status"] != "corrected":
        return metadata
    return {**metadata, "manual": manual_fields(document)}


def _surname(authors: str) -> str:
    words = re.split(r"\s*(?:[;,]|\band\b)\s*", authors.strip())[0].split()
    return words[-1] if words else ""


def _ocr_candidates(metadata):
    """Keep OCR evidence across a native reparse without making its identifiers authoritative."""
    ocr = metadata.get("ocr") or {}
    candidates = {key: list(values) for key, values in metadata.get("candidates", {}).items()}
    for key, values in ocr.get("candidates", {}).items():
        candidates[key] = list(dict.fromkeys([*candidates.get(key, []), *values]))
    identifiers = {key: dict(values) for key, values in metadata.get("identifier_sources", {}).items()}
    for key, values in ocr.get("identifier_sources", {}).items():
        identifiers[key] = {**values, **identifiers.get(key, {})}
    return {**metadata, "candidates": candidates, "identifier_sources": identifiers}


def _local_metadata(document, extracted):
    metadata = _ocr_candidates({**document["metadata"], **extracted})
    values = {key: extracted.get(key) for key in ("title", "authors", "year")}
    values["year"] = values["year"] or document["year"]
    previous = document["metadata"].get("sources", {})
    metadata["sources"] = dict(metadata.get("sources", {}))
    for key in ("title", "authors"):
        if str(previous.get(key, "")).startswith("ocr") and (
                not values[key] or key == "title" and metadata["sources"].get(key) in ("filename", "arxiv_id")):
            values[key] = document[key]
            metadata["sources"][key] = previous[key]
    if values["year"] and not extracted.get("year") and previous.get("year"):
        metadata["sources"]["year"] = previous["year"]
    metadata = _recorded(document, metadata)
    if document["metadata_status"] == "verified":  # the provider's values win over a reread
        values = {key: document[key] for key in values}
        metadata["sources"] = dict(previous)
    for key in metadata.get("manual", []):
        if key in values:
            values[key] = document[key]
        metadata["sources"][key] = "manual"
    if any(str(source).startswith("ocr") for source in metadata["sources"].values()):
        metadata["header_text"] = (metadata.get("ocr") or {}).get("header_text") or metadata.get("header_text", "")
    return values, metadata


def _store_local(db, document, extracted):
    """Write a reread. Fields the user typed, and a verified paper's fields, keep their values."""
    values, metadata = _local_metadata(document, extracted)
    db.execute("UPDATE documents SET extracted = ? WHERE id = ?", (json.dumps(metadata), document["id"]))
    if document["metadata_status"] != "verified":
        status = "corrected" if metadata.get("manual") else "needs_review"
        db.execute("UPDATE documents SET title = ?, authors = ?, year = ?, metadata_status = ? WHERE id = ?",
                   (values["title"], values["authors"], values["year"], status, document["id"]))


def pdf_files(paths, errors: list[dict] | None = None) -> list[Path]:
    """The PDFs under these files and folders: no symlinks, any case of .pdf."""
    found = []
    for path in map(Path, paths):
        try:
            if path.is_symlink():
                continue
            if path.is_dir():
                with os.scandir(path) as entries:
                    found += pdf_files(sorted(Path(entry.path) for entry in entries), errors)
            elif path.is_file() and path.suffix.lower() == ".pdf":
                found.append(path)
            elif not path.exists():
                raise FileNotFoundError(path)
        except OSError as exc:
            if errors is not None:
                errors.append({"path": str(path), "error": str(exc)})
    return found


class Library:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.originals = self.data_dir / "originals"
        self.working = self.data_dir / "working"
        self.originals.mkdir(exist_ok=True)
        self.working.mkdir(exist_ok=True)
        self.db_path = self.data_dir / "library.sqlite3"
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL, source_path TEXT, size INTEGER NOT NULL,
                    title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '', year INTEGER,
                    doi TEXT, pages INTEGER NOT NULL, metadata_status TEXT NOT NULL,
                    extracted TEXT NOT NULL, work_id INTEGER, revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    stored_name TEXT, label TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS works (
                    id INTEGER PRIMARY KEY, preferred_file_id INTEGER REFERENCES documents(id)
                );
                CREATE TABLE IF NOT EXISTS work_identifiers (
                    work_id INTEGER NOT NULL REFERENCES works(id),
                    scheme TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(scheme, value)
                );
                CREATE TABLE IF NOT EXISTS notes (
                    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
                    content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_views (
                    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
                    content TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
            if "stored_name" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN stored_name TEXT")
            if "label" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN label TEXT NOT NULL DEFAULT ''")
        self._reconcile()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _document(row):
        result = dict(row)
        result["metadata"] = json.loads(result.pop("extracted"))
        return result

    @staticmethod
    def _stored_name(doc) -> str:
        # Libraries created before readable names keep <sha256>.pdf until the next open.
        return doc["stored_name"] or f'{doc["sha256"]}.pdf'

    @staticmethod
    def _base_name(doc) -> str:
        """The name shown in the file manager: the identified paper, else the name the user gave."""
        verified = doc["metadata"].get("verified")
        if doc["year"] and (doc["metadata_status"] == "verified" or (verified and doc["metadata_status"] == "corrected")):
            # A user correction wins over the provider's author, but keeps the identified journal reference.
            surname = "" if "authors" in manual_fields(doc) else (verified or {}).get("first_author")
            try:
                name = safe_filename(pdf_name(surname or _surname(doc["authors"]), doc["year"], (verified or {}).get("publication")))
            except (KeyError, IndexError, TypeError):
                name = ""
            if name:
                return name
        return safe_filename(doc["filename"]) or f'Untitled_{doc["id"]}.pdf'

    def _free_name(self, db, doc, base: str) -> str:
        """Add _2, _3, ... until no other file matches, ignoring case as macOS and Windows do."""
        own = self._stored_name(doc)
        used = {(row[1] or row[0] + ".pdf").lower() for row in
                db.execute("SELECT sha256, stored_name FROM documents WHERE id != ?", (doc["id"],))}
        used |= {name.lower() for name in os.listdir(self.originals) if name != own}
        name, number = base, 1
        while name.lower() in used:
            number += 1
            name = f"{base[:-4]}_{number}.pdf"
        return name

    def _rename(self, document_id: int) -> None:
        """Move the original to its current readable name; a locked file just keeps the old one until the next try."""
        with self._lock:
            with self._connect() as db:
                doc = self._document(db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
                base = self._base_name(doc)
                current = self._stored_name(doc)
                # A numbered variant (Name_2.pdf) of the wanted name is already right; do not churn.
                if re.fullmatch(re.escape(base[:-4]) + r"(?:_\d+)?\.pdf", current, re.I):
                    return
                new = self._free_name(db, doc, base)
            old, target = self.originals / current, self.originals / new
            if not old.is_file():
                return
            try:
                os.replace(old, target)  # `new` is free, so nothing is overwritten
            except OSError:
                return
            try:
                with self._connect() as db:
                    db.execute("UPDATE documents SET stored_name = ? WHERE id = ?", (new, document_id))
            except sqlite3.Error:
                os.replace(target, old)
                raise

    def _reconcile(self) -> None:
        """Finish an interrupted rename, then give legacy <sha256>.pdf originals readable names."""
        with self._connect() as db:
            rows = db.execute("SELECT id, sha256, stored_name FROM documents").fetchall()
        files = {entry.name for entry in os.scandir(self.originals) if entry.is_file() and entry.name.lower().endswith(".pdf")}
        names = {row["id"]: row["stored_name"] or row["sha256"] + ".pdf" for row in rows}
        strays = sorted(files - set(names.values()))
        # A crash between the file move and the database update leaves a missing file and a stray one.
        # Hashing is the only safe way to tell which stray belongs to which row, so do it only then.
        for row in rows:
            if strays and names[row["id"]] not in files:
                for name in strays:
                    with open(self.originals / name, "rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() == row["sha256"]:
                            with self._connect() as db:
                                db.execute("UPDATE documents SET stored_name = ? WHERE id = ?",
                                           (None if name == row["sha256"] + ".pdf" else name, row["id"]))
                            strays.remove(name)
                            break
        with self._connect() as db:
            legacy = [row[0] for row in db.execute("SELECT id FROM documents WHERE stored_name IS NULL")]
        for document_id in legacy:
            self._rename(document_id)

    def list_documents(self, query: str = "") -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM documents ORDER BY title COLLATE NOCASE, id").fetchall()
        needle = query.casefold().strip()
        documents = []
        for row in rows:
            doc = self._document(row)
            fields = [str(doc.get(key) or "") for key in
                      ("title", "label", "authors", "year", "doi", "filename", "metadata_status")]
            if doc["metadata_status"] in ("needs_review", "incomplete"):
                fields.append("Metadata needs review")
            if not needle or needle in " ".join(fields).casefold():
                documents.append(doc)
        return documents

    def get_document(self, document_id: int) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._document(row)

    def import_paths(self, paths: list[str | Path], progress=None, cancel=None) -> dict:
        result = {"documents": [], "imported": 0, "duplicates": 0, "errors": [], "cancelled": False}
        found = pdf_files(paths, result["errors"])
        for index, path in enumerate(found):
            if cancel is not None and cancel.is_set():
                result["cancelled"] = True
                break
            try:
                with path.open("rb") as stream:
                    doc = self._import_stream(stream, path.name, str(path.resolve()))
                result["documents"].append(doc)
                result["duplicates" if doc["duplicate"] else "imported"] += 1
            except Exception as exc:
                result["errors"].append({"path": str(path), "error": str(exc)})
            if progress:
                progress(index + 1, len(found))
        return result

    def import_stream(self, stream, filename: str) -> dict:
        return self._import_stream(stream, filename, None)

    def open_file(self, path: str | Path) -> dict:
        """Reuse this library's original/current revision, or import a managed copy of an external file."""
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != '.pdf':
            raise ValueError('Choose a PDF file, not a folder or another file type.')
        owner = path.parent.parent
        managed = path.parent.name in ('originals', 'working') and (owner / 'library.sqlite3').is_file()
        if managed:
            if owner != self.data_dir.resolve():
                raise ValueError('This PDF belongs to another PaperAssist library. Open that library to use it.')
            for document in self.list_documents():
                original = self.originals / self._stored_name(document)
                current = self.working / f"{document['id']}-{document['revision']}.pdf"
                if path == original.resolve():
                    with path.open('rb') as source:
                        if hashlib.file_digest(source, 'sha256').hexdigest() != document['sha256']:
                            raise ValueError('The managed original has changed. Restore its original bytes before opening it.')
                    return document
                if document['revision'] and path == current.resolve():
                    return document
            raise ValueError('This managed PDF is missing from the catalog or is an older revision. Open the paper from Library.')
        with path.open('rb') as stream:
            return self._import_stream(stream, path.name, str(path))

    def _import_stream(self, stream, filename, source_path):
        filename = Path(filename.replace("\\", "/")).name or "document.pdf"
        with self._lock:
            fd, temp = tempfile.mkstemp(dir=self.originals, suffix=".part")
            temp = Path(temp)
            try:
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(fd, "wb") as output:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        output.write(chunk)
                sha = digest.hexdigest()
                with self._connect() as db:
                    row = db.execute("SELECT * FROM documents WHERE sha256 = ?", (sha,)).fetchone()
                    if row:
                        doc = self._document(row)
                        dest = self.originals / self._stored_name(doc)
                        if not dest.exists():  # repair an original that was deleted from disk
                            name = row["stored_name"] or self._free_name(db, doc, self._base_name(doc))
                            dest = self.originals / name
                            os.replace(temp, dest)
                            db.execute("UPDATE documents SET stored_name = ? WHERE id = ?", (name, row["id"]))
                        result = doc
                        if result["metadata"].get("parser_version", 0) < PARSER_VERSION:
                            _store_local(db, doc, extract_metadata(dest, doc["filename"]))
                        result = self._document(db.execute("SELECT * FROM documents WHERE id = ?", (row["id"],)).fetchone())
                        result["duplicate"] = True
                        return result
                    with temp.open("rb") as check:
                        if not check.read(1024).lstrip().startswith(b"%PDF-"):
                            raise ValueError("File is not a PDF")
                    metadata = extract_metadata(temp, filename)
                    cursor = db.execute("""INSERT INTO documents
                        (sha256, filename, source_path, size, title, authors, year, doi, pages,
                         metadata_status, extracted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (sha, filename, source_path, size, metadata["title"], metadata["authors"],
                         metadata.get("year"), None, metadata["pages"], "needs_review", json.dumps(metadata)))
                    document_id = cursor.lastrowid
                    # The row exists first because an unnamed file is called Untitled_<id>.
                    doc = self._document(db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
                    name = self._free_name(db, doc, self._base_name(doc))
                    os.replace(temp, self.originals / name)  # `name` is free, so nothing is overwritten
                    db.execute("UPDATE documents SET stored_name = ? WHERE id = ?", (name, document_id))
                result = self.get_document(document_id)
                result["duplicate"] = False
                return result
            finally:
                temp.unlink(missing_ok=True)

    def get_path(self, document_id: int, annotated: bool = True) -> Path:
        doc = self.get_document(document_id)
        if annotated and doc["revision"]:
            working = self.working / f'{document_id}-{doc["revision"]}.pdf'
            if not working.is_file():
                raise FileNotFoundError(f"Missing saved PDF for document {document_id}, revision {doc['revision']}")
            return working
        original = self.originals / self._stored_name(doc)
        if not original.is_file():
            raise FileNotFoundError(f"Missing original PDF for document {document_id}")
        return original

    def save_pdf(self, document_id: int, payload: bytes, expected_revision: int) -> dict:
        with self._lock:
            doc = self.get_document(document_id)
            if doc["revision"] != expected_revision:
                raise ValueError("Revision conflict")
            fd, temp = tempfile.mkstemp(dir=self.working, suffix=".part")
            temp = Path(temp)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(payload)
                if not payload.lstrip().startswith(b"%PDF-"):
                    raise ValueError("File is not a PDF")
                reader = PdfReader(temp)
                if reader.is_encrypted or not len(reader.pages):
                    raise ValueError("Invalid PDF")
                os.replace(temp, self.working / f"{document_id}-{expected_revision + 1}.pdf")
                with self._connect() as db:
                    db.execute("UPDATE documents SET revision = revision + 1 WHERE id = ?", (document_id,))
                return self.get_document(document_id)
            finally:
                temp.unlink(missing_ok=True)

    def _save_fields(self, doc, values: dict, status: str, metadata: dict, filename: str | None = None) -> None:
        """Write fields, their DOI-to-work link, the status and the metadata JSON in one transaction."""
        values = dict(values)
        with self._connect() as db:
            if "doi" in values:
                doi, work_id = values["doi"], None
                if doi:
                    row = db.execute("SELECT work_id FROM work_identifiers WHERE scheme = 'doi' AND value = ?", (doi,)).fetchone()
                    if row:
                        work_id = row[0]
                    else:
                        work_id = db.execute("INSERT INTO works (preferred_file_id) VALUES (?)", (doc["id"],)).lastrowid
                        db.execute("INSERT INTO work_identifiers VALUES (?, 'doi', ?)", (work_id, doi))
                values["work_id"] = work_id
            values.update(metadata_status=status, extracted=json.dumps(metadata))
            if filename:
                values["filename"] = filename
            db.execute("UPDATE documents SET " + ", ".join(f"{key} = ?" for key in values) + " WHERE id = ?",
                       (*values.values(), doc["id"]))
            if values.get("work_id"):
                db.execute("UPDATE works SET preferred_file_id = ? WHERE id = ? AND preferred_file_id IS NULL",
                           (doc["id"], values["work_id"]))
            old_work = doc["work_id"]
            if "work_id" in values and old_work and old_work != values["work_id"]:
                db.execute("""UPDATE works SET preferred_file_id = (SELECT id FROM documents
                    WHERE work_id = ? ORDER BY id LIMIT 1)
                    WHERE id = ? AND preferred_file_id = ?""", (old_work, old_work, doc["id"]))

    def update_metadata(self, document_id: int, values: dict) -> dict:
        """Record the user's corrections. Only fields that really change become manual, so
        saving a form untouched does not freeze values a refresh could still improve."""
        allowed = {key: values[key] for key in FIELDS if key in values}
        if "year" in allowed:
            allowed["year"] = int(allowed["year"]) if allowed["year"] not in (None, "") else None
        for key in ("title", "authors"):
            if key in allowed:
                allowed[key] = str(allowed[key] or "").strip()
        if "title" in allowed and not allowed["title"]:
            raise ValueError("Title cannot be empty")
        if "doi" in allowed:
            allowed["doi"] = self._normalize_doi(allowed["doi"])
        with self._lock:
            doc = self.get_document(document_id)
            changed = {key: value for key, value in allowed.items() if (value or None) != (doc[key] or None)}
            if changed:
                metadata = {**doc["metadata"], "manual": list(dict.fromkeys([*manual_fields(doc), *changed])),
                            "sources": {**doc["metadata"].get("sources", {}), **dict.fromkeys(changed, "manual")}}
                self._save_fields(doc, changed, "corrected", metadata)
                self._rename(doc["id"])
        return self.get_document(document_id)

    def set_label(self, document_id: int, label) -> dict:
        """Set the name shown on this PDF's tab. It is a nickname, so it leaves the
        bibliographic metadata, its status and the stored file name alone."""
        if not isinstance(label, str):
            raise ValueError("Label must be a string")
        label = " ".join(label.split())
        if len(label) > MAX_LABEL:
            raise ValueError(f"Label cannot be longer than {MAX_LABEL} characters")
        with self._lock:
            self.get_document(document_id)
            with self._connect() as db:
                db.execute("UPDATE documents SET label = ? WHERE id = ?", (label, document_id))
        return self.get_document(document_id)

    def refresh_metadata(self, document_id: int) -> dict:
        """Reread the immutable PDF, keeping confirmed values and manual corrections."""
        doc = self.get_document(document_id)
        extracted = extract_metadata(self.get_path(document_id, annotated=False), doc["filename"])
        with self._lock:
            # A correction made while extraction was running takes precedence.
            current = self.get_document(document_id)
            with self._connect() as db:
                _store_local(db, current, extracted)
        return self.get_document(document_id)

    def apply_ocr_metadata(self, document_id: int, extracted: dict, report: dict) -> dict:
        """Fill provisional gaps from OCR; the PDF, confirmed fields and corrections stay authoritative."""
        with self._lock:
            document = self.get_document(document_id)
            metadata = dict(document["metadata"])
            outcome = {key: report[key] for key in ("status", "message", "cache_key", "cache_hit", "language",
                       "engine_version", "config") if key in report}
            if extracted:
                outcome.update({key: extracted.get(key, {}) for key in ("candidates", "identifier_sources", "evidence")})
                outcome["header_text"] = extracted.get("header_text", "")
            else:
                previous = metadata.get("ocr") or {}
                outcome = {**previous, **outcome}
            metadata["ocr"] = outcome
            metadata = _ocr_candidates(metadata)
            metadata = _recorded(document, metadata)
            manual = metadata.get("manual", [])
            values = {}
            if document["metadata_status"] != "verified":
                sources = metadata["sources"] = dict(metadata.get("sources", {}))
                if extracted.get("title") and "title" not in manual and (
                        not useful(document["title"]) or sources.get("title") in ("filename", "arxiv_id")):
                    values["title"] = extracted["title"]
                if extracted.get("authors") and "authors" not in manual and not document["authors"].strip():
                    values["authors"] = extracted["authors"]
                if extracted.get("year") and "year" not in manual and not document["year"]:
                    values["year"] = extracted["year"]
                for key in values:
                    sources[key] = extracted.get("sources", {}).get(key, "ocr")
                if values:
                    metadata["header_text"] = extracted.get("header_text", "")
            with self._connect() as db:
                db.execute("UPDATE documents SET extracted = ? WHERE id = ?", (json.dumps(metadata), document_id))
                if values:
                    db.execute("UPDATE documents SET " + ", ".join(f"{key} = ?" for key in values) +
                               " WHERE id = ?", (*values.values(), document_id))
        return self.get_document(document_id)

    def record_metadata_refresh(self, document_id: int, status: str, message: str) -> dict:
        """Keep the outcome visible so a local reread is not mistaken for online verification."""
        with self._lock:
            document = self.get_document(document_id)
            metadata = _recorded(document, {**document["metadata"], "refresh": {"status": status, "message": message}})
            with self._connect() as db:
                db.execute("UPDATE documents SET extracted = ? WHERE id = ?", (json.dumps(metadata), document_id))
        return self.get_document(document_id)

    def apply_verified(self, document_id: int, record: dict, filename: str | None = None, *, refresh=False) -> dict:
        """Apply an identified record to the fields the user has not typed. Only an explicit
        refresh replaces a confirmed year or DOI."""
        with self._lock:
            doc = self.get_document(document_id)
            metadata = _recorded(doc, doc["metadata"])
            manual = metadata.get("manual", [])
            # Extracted PDF properties are only candidates (e.g. "rdbpf_6.eps"), so a record
            # verified against the citation replaces them.
            values = {key: record[key] for key in FIELDS if record.get(key) and key not in manual
                      and (refresh or not (key in ("year", "doi") and doc[key]))}
            metadata = {**metadata, "sources": {**metadata.get("sources", {}), **dict.fromkeys(values, record["provider"])},
                        "verified": {key: record.get(key) for key in ("provider", "doi", "arxiv_id", "publication", "first_author", "evidence")}}
            rename = Path(filename).name if filename and doc["filename"] == "download.pdf" else None
            self._save_fields(doc, values, "verified", metadata, rename)
            self._rename(document_id)
            return self.get_document(document_id)

    @staticmethod
    def _normalize_doi(value):
        value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or "").strip(), flags=re.I).strip().lower()
        if value and not re.fullmatch(r"10\.\d{4,9}/\S+", value):
            raise ValueError("Invalid DOI")
        return value or None

    def find_by_doi(self, doi: str) -> list[dict]:
        doi = self._normalize_doi(doi)
        with self._connect() as db:
            rows = db.execute("""SELECT documents.* FROM documents JOIN work_identifiers
                ON documents.work_id = work_identifiers.work_id
                WHERE work_identifiers.scheme = 'doi' AND work_identifiers.value = ?
                ORDER BY documents.id""", (doi,)).fetchall()
        return [self._document(row) for row in rows]

    def get_notes(self, document_id: int) -> list:
        self.get_document(document_id)
        with self._connect() as db:
            row = db.execute("SELECT content FROM notes WHERE document_id = ?", (document_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def get_view(self, document_id: int) -> dict | None:
        self.get_document(document_id)
        with self._connect() as db:
            row = db.execute("SELECT content FROM document_views WHERE document_id = ?", (document_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_view(self, document_id: int, view: dict, only_if_missing=False) -> dict:
        doc = self.get_document(document_id)
        if not isinstance(view, dict) or set(view) != {'page', 'scale'}:
            raise ValueError('View must contain page and scale')
        page, scale = view['page'], view['scale']
        if type(page) is not int or not 1 <= page <= doc['pages']:
            raise ValueError('Page must be a page number in this PDF')
        if scale not in ('auto', 'page-width', 'page-fit', 'page-actual'):
            try:
                number = float(scale)
            except (TypeError, ValueError):
                raise ValueError('Invalid zoom scale') from None
            if isinstance(scale, bool) or not math.isfinite(number) or not 0 < number <= 100:
                raise ValueError('Invalid zoom scale')
        with self._lock, self._connect() as db:
            conflict = 'DO NOTHING' if only_if_missing else 'DO UPDATE SET content = excluded.content'
            db.execute(f"INSERT INTO document_views VALUES (?, ?) ON CONFLICT(document_id) {conflict}",
                       (document_id, json.dumps(view)))
            row = db.execute("SELECT content FROM document_views WHERE document_id = ?", (document_id,)).fetchone()
        return json.loads(row[0])

    def save_notes(self, document_id: int, notes: list) -> list:
        if not isinstance(notes, list):
            raise ValueError("Notes must be a list")
        with self._lock:
            self.get_document(document_id)
            with self._connect() as db:
                db.execute("INSERT INTO notes VALUES (?, ?) ON CONFLICT(document_id) DO UPDATE SET content = excluded.content", (document_id, json.dumps(notes)))
        return notes
