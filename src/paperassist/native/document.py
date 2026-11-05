"""One PDF seen from the UI process: text, links, search, highlights and saving.

Pixels come from the render process; this object does the cheap structural work and
is the only writer of the file, on explicit save. It works on the plain data of
engine.py: coordinates are what the user sees, in PDF points.
"""
from enum import Enum

from .engine import Rect


class SaveState(Enum):
    SAVED = "Saved"
    UNSAVED = "Unsaved changes"
    SAVING = "Saving…"
    FAILED = "Save failed"


class PdfDocument:
    def __init__(self, source):
        self.source = source
        self.pdf = source.open()
        self.sizes = self.pdf.sizes
        self.state, self.error = SaveState.SAVED, ""
        self.pending = {}  # page -> highlighted rects the render process has not drawn yet
        self._words, self._links = {}, {}

    def __len__(self):
        return len(self.sizes)

    @property
    def modified(self):
        return self.state is not SaveState.SAVED

    def words(self, page):
        """(x0, y0, x1, y1, text, block) in the engine's reading order."""
        if page not in self._words:
            self._words[page] = self.pdf.words(page)
        return self._words[page]

    def links(self, page):
        if page not in self._links:
            self._links[page] = self.pdf.links(page)
        return self._links[page]

    def link_at(self, page, x, y):
        return next((link for link in self.links(page) if link.rect.contains(x, y)), None)

    def word_at(self, page, x, y):
        """Index of the word under the point, or the nearest one (lines weigh more than columns)."""
        def distance(w):
            return max(w[0] - x, 0, x - w[2]) ** 2 + 4 * max(w[1] - y, 0, y - w[3]) ** 2
        words = self.words(page)
        return min(range(len(words)), key=lambda i: distance(words[i]), default=None)

    def selection_lines(self, start, end):
        """[(page, rect, text)], one band per visual line, between two (page, word index) ends.

        MuPDF may split a justified line into several 'lines', so words join the current
        band by geometry: same block, mostly overlapping vertically, further to the right.
        """
        start, end = sorted([start, end])
        bands = []
        for page in range(start[0], end[0] + 1):
            words = self.words(page)
            first = start[1] if page == start[0] else 0
            last = end[1] if page == end[0] else len(words) - 1
            for w in words[first:last + 1]:
                rect = Rect(*w[:4])
                if bands and bands[-1][0] == (page, w[5]):
                    key, band, texts = bands[-1]
                    overlap = min(band.y1, rect.y1) - max(band.y0, rect.y0)
                    if overlap > 0.5 * min(band.height, rect.height) and rect.x0 >= band.x1 - 2:
                        bands[-1] = (key, band.union(rect), texts + [w[4]])
                        continue
                bands.append(((page, w[5]), rect, [w[4]]))
        return [(key[0], rect, " ".join(texts)) for key, rect, texts in bands]

    def search(self, text):
        return self.pdf.search(text)

    def add_highlight(self, page, rects):
        self.pdf.add_highlight(page, rects)
        self.pending.setdefault(page, []).extend(rects)
        self.state, self.error = SaveState.UNSAVED, ""

    def save(self, on_saving=lambda: None):
        """Explicit save only. A failure keeps the edits and the unsaved state for a retry."""
        self.state = SaveState.SAVING
        on_saving()
        try:
            self.source.save(self.pdf)
            self.reload()
        except Exception as error:  # the disk, permissions or MuPDF: all are shown to the user
            self.state, self.error = SaveState.FAILED, str(error) or type(error).__name__
            return False
        self.state, self.error = SaveState.SAVED, ""
        return True

    def reload(self):
        """Read the saved file again."""
        self.pdf.close()
        self.pdf = self.source.open()
        self._words.clear()
        self._links.clear()

    def close(self):
        self.pdf.close()
