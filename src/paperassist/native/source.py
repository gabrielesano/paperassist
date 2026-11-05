"""Where a document comes from and where its edits go.

The reader only talks to this interface, so the library can later hand it a working
copy of a stored PDF without the reader knowing about the library. The one
implementation today is a PDF file on disk.
"""
import os
import shutil
from pathlib import Path
from typing import Protocol

from .engine import Pdf


class DocumentSource(Protocol):
    key: str    # identity of the exact PDF; two views with the same key share one save authority
    title: str  # short name for tabs and dialogs
    path: Path  # last saved bytes, which the render process reads

    def open(self) -> Pdf: ...

    def save(self, pdf: Pdf) -> None:
        """Write the edits of pdf so that open() and path show them; raise on failure."""


class FileSource:
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


def _sync_directory(directory):
    """Make the rename itself durable. Windows cannot open directories, and there os.replace is enough."""
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
