"""Where a document comes from and where its edits go."""
import os
import shutil
from pathlib import Path
from typing import Protocol

from .engine import Pdf


def display_title(document):
    return document.get("label") or document.get("title") or document.get("filename") or "Untitled paper"


class DocumentSource(Protocol):
    key: str               # identity of the exact PDF; two views with the same key share one save authority
    title: str             # short name for tabs and dialogs
    path: Path             # location of the saved PDF
    document_id: int | None

    def open(self) -> Pdf: ...

    def save(self, pdf: Pdf) -> None:
        """Write the edits of pdf so that open() and path show them; raise on failure."""


class FileSource:
    document_id: int | None = None

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.key, self.title = str(self.path), self.path.name

    def open(self):
        return Pdf(self.path.read_bytes())

    def save(self, pdf):
        """Replace the file atomically: a crash leaves the old version or the new one, never half."""
        temporary = self.path.with_name(f".{self.path.name}.saving")
        try:
            with open(temporary, "wb") as out:
                pdf.write(out)
                out.flush()
                os.fsync(out.fileno())  # the bytes must be on disk before the name points to them
            shutil.copymode(self.path, temporary)  # a replaced file keeps the permissions the user gave it
            os.replace(temporary, self.path)
            _sync_directory(self.path.parent)
        finally:
            temporary.unlink(missing_ok=True)


class LibrarySource:
    """A library document as a DocumentSource: reads and saves its newest revision."""

    def __init__(self, library, document):
        self.library, self.document_id = library, document["id"]
        self.title = display_title(document)
        self.revision = document["revision"]
        # The document, not a revision, is the identity: every view of it shares one save authority.
        self.key = f"library:{self.document_id}"

    @property
    def path(self):
        return self.library.get_path(self.document_id)

    def open(self):
        """Read the newest revision; a view that reloads after another view saved gets that one."""
        data, revision = self.library.read_pdf(self.document_id)
        pdf = Pdf(data)
        self.revision = revision
        return pdf

    def save(self, pdf):
        try:
            self.revision = self.library.save_pdf(self.document_id, pdf.data, self.revision)["revision"]
        except ValueError as error:
            if str(error) == "Revision conflict":
                raise RuntimeError("This paper was saved again since you opened it, so your changes were not "
                                   "written. They stay open here; reopen the paper to see the newer version.") from None
            raise RuntimeError(f"The library rejected the PDF: {error}") from None


def _sync_directory(directory):
    """Make the rename itself durable. Windows cannot open directories, and there os.replace is enough."""
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
