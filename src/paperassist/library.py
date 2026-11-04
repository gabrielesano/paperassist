"""Local PDF storage, metadata and notes."""

import hashlib
from contextlib import contextmanager
import json
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path

from pypdf import PdfReader

from .metadata import PARSER_VERSION, extract_metadata


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
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            """)

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

    def list_documents(self, query: str = "") -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM documents ORDER BY title COLLATE NOCASE, id").fetchall()
        needle = query.casefold().strip()
        return [doc for row in rows if (doc := self._document(row)) and
                (not needle or needle in " ".join(str(doc.get(k) or "") for k in
                 ("title", "authors", "year", "doi", "filename", "metadata_status")).casefold())]

    def get_document(self, document_id: int) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._document(row)

    def import_paths(self, paths: list[str | Path], progress=None, cancel=None) -> dict:
        result = {"documents": [], "imported": 0, "duplicates": 0, "errors": [], "cancelled": False}
        found = []

        def scan(path):
            try:
                if path.is_symlink():
                    return
                if path.is_dir():
                    for item in sorted(path.iterdir()):
                        scan(item)
                elif path.is_file() and path.suffix.lower() == ".pdf":
                    found.append(path)
                elif not path.exists():
                    raise FileNotFoundError(path)
            except OSError as exc:
                result["errors"].append({"path": str(path), "error": str(exc)})

        for name in paths:
            scan(Path(name))
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
                        dest = self.originals / f"{sha}.pdf"
                        if not dest.exists():
                            os.replace(temp, dest)
                        result = self._document(row)
                        if result["metadata"].get("parser_version", 0) < PARSER_VERSION:
                            metadata = extract_metadata(dest, filename)
                            db.execute("UPDATE documents SET extracted = ? WHERE id = ?", (json.dumps(metadata), row["id"]))
                            if row["metadata_status"] != "corrected":
                                db.execute("UPDATE documents SET title = ?, authors = ? WHERE id = ?",
                                           (metadata["title"], metadata["authors"], row["id"]))
                            result = self._document(db.execute("SELECT * FROM documents WHERE id = ?", (row["id"],)).fetchone())
                        result["duplicate"] = True
                        return result
                    with temp.open("rb") as check:
                        if not check.read(1024).lstrip().startswith(b"%PDF-"):
                            raise ValueError("File is not a PDF")
                    metadata = extract_metadata(temp, filename)
                    dest = self.originals / f"{sha}.pdf"
                    os.replace(temp, dest)
                    cursor = db.execute("""INSERT INTO documents
                        (sha256, filename, source_path, size, title, authors, year, doi, pages,
                         metadata_status, extracted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (sha, filename, source_path, size, metadata["title"], metadata["authors"],
                         None, None, metadata["pages"], "needs_review", json.dumps(metadata)))
                    document_id = cursor.lastrowid
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
        original = self.originals / f'{doc["sha256"]}.pdf'
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

    def update_metadata(self, document_id: int, values: dict, status: str = "corrected") -> dict:
        allowed = {key: values[key] for key in ("title", "authors", "year", "doi") if key in values}
        if "year" in allowed and allowed["year"] not in (None, ""):
            allowed["year"] = int(allowed["year"])
        elif "year" in allowed:
            allowed["year"] = None
        for key in ("title", "authors"):
            if key in allowed:
                allowed[key] = str(allowed[key] or "").strip()
        if "title" in allowed and not allowed["title"]:
            raise ValueError("Title cannot be empty")
        if "doi" in allowed:
            allowed["doi"] = self._normalize_doi(allowed["doi"])
        with self._lock:
            self.get_document(document_id)
            if allowed:
                with self._connect() as db:
                    old_work = self.get_document(document_id)["work_id"]
                    if "doi" in allowed:
                        doi = allowed["doi"]
                        work_id = None
                        if doi:
                            row = db.execute("SELECT work_id FROM work_identifiers WHERE scheme = 'doi' AND value = ?", (doi,)).fetchone()
                            if row:
                                work_id = row[0]
                            else:
                                work_id = db.execute("INSERT INTO works (preferred_file_id) VALUES (?)", (document_id,)).lastrowid
                                db.execute("INSERT INTO work_identifiers VALUES (?, 'doi', ?)", (work_id, doi))
                        allowed["work_id"] = work_id
                    db.execute("UPDATE documents SET " + ", ".join(f"{key} = ?" for key in allowed) +
                               ", metadata_status = ? WHERE id = ?", (*allowed.values(), status, document_id))
                    if "work_id" in allowed and old_work and old_work != allowed["work_id"]:
                        db.execute("""UPDATE works SET preferred_file_id = (SELECT id FROM documents
                            WHERE work_id = ? ORDER BY id LIMIT 1)
                            WHERE id = ? AND preferred_file_id = ?""", (old_work, old_work, document_id))
        return self.get_document(document_id)

    def apply_verified(self, document_id: int, record: dict, filename: str | None = None) -> dict:
        """Fill gaps from an identified record without overriding user corrections."""
        with self._lock:
            doc = self.get_document(document_id)
            if doc["metadata_status"] == "corrected":
                return doc
            sources = doc["metadata"].get("sources", {})
            # Extracted PDF properties are only candidates (e.g. "rdbpf_6.eps"), so a record
            # verified against the citation replaces them; a confirmed year or DOI is kept.
            values = {key: record[key] for key in ("title", "authors", "year", "doi")
                      if record.get(key) and not (key in ("year", "doi") and doc[key])}
            rename = bool(filename) and doc["filename"] == "download.pdf"
            if not values and not rename:
                return doc
            if values:
                self.update_metadata(document_id, values, status="verified")
            metadata = {**doc["metadata"], "sources": {**sources, **dict.fromkeys(values, record["provider"])},
                        "verified": {key: record.get(key) for key in ("provider", "doi", "publication")}}
            with self._connect() as db:
                db.execute("UPDATE documents SET filename = ?, extracted = ? WHERE id = ?",
                           (Path(filename).name if rename else doc["filename"], json.dumps(metadata), document_id))
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

    def save_notes(self, document_id: int, notes: list) -> list:
        if not isinstance(notes, list):
            raise ValueError("Notes must be a list")
        with self._lock:
            self.get_document(document_id)
            with self._connect() as db:
                db.execute("INSERT INTO notes VALUES (?, ?) ON CONFLICT(document_id) DO UPDATE SET content = excluded.content", (document_id, json.dumps(notes)))
        return notes
