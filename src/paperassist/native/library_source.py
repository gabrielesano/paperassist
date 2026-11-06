"""A library document as a DocumentSource: reads and saves its newest revision."""
import io

from .engine import Pdf


def display_title(document):
    return document.get("label") or document.get("title") or document.get("filename") or "Untitled paper"


class LibrarySource:
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
        self.revision = self.library.get_document(self.document_id)["revision"]
        return Pdf(self.path.read_bytes())

    def save(self, pdf):
        payload = io.BytesIO()
        pdf.write(payload)
        try:
            self.revision = self.library.save_pdf(self.document_id, payload.getvalue(), self.revision)["revision"]
        except ValueError as error:
            if str(error) == "Revision conflict":
                raise RuntimeError("This paper was saved again since you opened it, so your changes were not "
                                   "written. They stay open here; reopen the paper to see the newer version.") from None
            raise RuntimeError(f"The library rejected the PDF: {error}") from None
