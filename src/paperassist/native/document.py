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
        self._undo, self._redo = [], []
        self._token = self._saved_token = self._sequence = 0
        self._annotations = {}

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

        A PDF may split a justified line into several 'lines', so words join the current
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
                    across_y = min(band.y1, rect.y1) - max(band.y0, rect.y0)
                    across_x = min(band.x1, rect.x1) - max(band.x0, rect.x0)
                    horizontal = across_y > 0.5 * min(band.height, rect.height) and (rect.x0 >= band.x1 - 2 or rect.x1 <= band.x0 + 2)
                    vertical = across_x > 0.5 * min(band.width, rect.width) and (rect.y0 >= band.y1 - 2 or rect.y1 <= band.y0 + 2)
                    if horizontal or vertical:
                        bands[-1] = (key, band.union(rect), texts + [w[4]])
                        continue
                bands.append(((page, w[5]), rect, [w[4]]))
        return [(key[0], rect, " ".join(texts)) for key, rect, texts in bands]

    def search(self, text):
        return self.pdf.search(text)

    def annotations(self, page):
        if page not in self._annotations:
            self._annotations[page] = self.pdf.annotations(page)
        return self._annotations[page]

    def annotation_at(self, page, x, y, tolerance=4):
        def near(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            fraction = max(0, min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy or 1)))
            return (x - a[0] - fraction * dx) ** 2 + (y - a[1] - fraction * dy) ** 2
        for item in reversed(self.annotations(page)):
            if item['subtype'] == 'highlight':
                if any(rect.contains(x, y) for rect in item['quads'] or [item['rect']]):
                    return item
            elif any(near(a, b) <= (item['width'] / 2 + tolerance) ** 2
                     for stroke in item['strokes'] for a, b in zip(stroke, stroke[1:])):
                return item
        return None

    def _change(self, pages, apply):
        before, token = len(self.pdf.data), self._token
        apply()
        self._sequence += 1
        self._token = self._sequence
        # Only offsets are retained: every previous revision is already a prefix of these bytes.
        self._undo.append((before, len(self.pdf.data), pages, token, self._token))
        self._redo.clear()
        self._changed()

    def _changed(self):
        self._annotations.clear()
        self.state = SaveState.SAVED if self._token == self._saved_token else SaveState.UNSAVED
        self.error = ''

    def add_highlight(self, page, rects, color=None):
        options = {} if color is None else {'color': color}
        self._change([page], lambda: self.pdf.add_highlight(page, rects, **options))
        self.pending.setdefault(page, []).extend(rects)

    def add_highlights(self, pages, color):
        def apply():
            for page, rects in pages.items():
                self.pdf.add_highlight(page, rects, color=color)
        self._change(list(pages), apply)

    def add_ink(self, page, strokes, color=(0, 0, 0), width=2):
        self._change([page], lambda: self.pdf.add_ink(page, strokes, color, width))

    def update_annotation(self, page, index, color=None, width=None):
        item = next(item for item in self.annotations(page) if item['index'] == index)
        if (color is None or all(abs(a - b) < 1e-6 for a, b in zip(color, item['color']))) and (width is None or width == item['width']):
            return False
        if width is not None and item['subtype'] != 'ink':
            return False
        self._change([page], lambda: self.pdf.update_annotation(page, index, color, width))
        return True

    def delete_annotation(self, page, index):
        self._change([page], lambda: self.pdf.delete_annotation(page, index))

    def undo(self):
        return self._step(self._undo, self._redo, 0, 3)

    def redo(self):
        return self._step(self._redo, self._undo, 1, 4)

    def _step(self, source, target, revision, token):
        if not source:
            return []
        entry = source[-1]
        self.pdf.restore_annotations(self.pdf.data[:entry[revision]], entry[2])
        source.pop()
        target.append(entry)
        self._token = entry[token]
        self.pending.clear()
        self._changed()
        return entry[2]

    def save(self, on_saving=lambda: None):
        """Explicit save only. A failure keeps the edits and the unsaved state for a retry."""
        self.state = SaveState.SAVING
        on_saving()
        try:
            self.source.save(self.pdf)
            self.reload(keep_history=True)
        except Exception as error:  # the disk, permissions or the PDF engine: all are shown to the user
            self.state, self.error = SaveState.FAILED, str(error) or type(error).__name__
            return False
        self._saved_token = self._token
        self.state, self.error = SaveState.SAVED, ""
        return True

    def reload(self, keep_history=False):
        """Read the saved file again."""
        previous = self.pdf.data
        replacement = self.source.open()
        self.pdf.close()
        self.pdf = replacement
        self._annotations.clear()
        if not keep_history or self.pdf.data != previous:
            self._undo.clear()
            self._redo.clear()
            self._token = self._saved_token = 0
        self._words.clear()
        self._links.clear()

    def close(self):
        self.pdf.close()
