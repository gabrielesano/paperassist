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


def same(a, b):
    """Equal annotation state; rewriting a PDF only keeps about seven digits of every number."""
    if isinstance(a, (list, tuple)):
        return isinstance(b, (list, tuple)) and len(a) == len(b) and all(map(same, a, b))
    return abs(a - b) < 1e-3 if isinstance(a, float) else a == b


class PdfDocument:
    def __init__(self, source):
        self.source = source
        self.pdf = source.open()
        self.sizes = self.pdf.sizes
        self.state, self.error = SaveState.SAVED, ""
        self.pending = {}  # page -> highlighted rects the render process has not drawn yet
        self._words, self._links = {}, {}
        self._undo, self._redo = [], []
        self._saved = {}  # page -> annotation state at the last save, for every page edited since
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
                    # A gap wider than two line heights is a column gutter, not a word space.
                    gap_x = max(rect.x0 - band.x1, band.x0 - rect.x1)
                    gap_y = max(rect.y0 - band.y1, band.y0 - rect.y1)
                    horizontal = across_y > 0.5 * min(band.height, rect.height) and -2 <= gap_x <= 2 * max(band.height, rect.height)
                    vertical = across_x > 0.5 * min(band.width, rect.width) and -2 <= gap_y <= 2 * max(band.width, rect.width)
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

    def annotation_at(self, page, x, y, tolerance=4, highlights=True):
        def near(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            fraction = max(0, min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy or 1)))
            return (x - a[0] - fraction * dx) ** 2 + (y - a[1] - fraction * dy) ** 2
        for item in reversed(self.annotations(page)):
            if not highlights and (item['subtype'] == 'highlight' or item['freehand']):
                continue  # hidden highlights must not be clickable
            if item['subtype'] == 'highlight':
                if any(rect.contains(x, y) for rect in item['quads'] or [item['rect']]):
                    return item
            elif any(near(a, b) <= (item['width'] / 2 + tolerance) ** 2
                     for stroke in item['strokes'] for a, b in zip(stroke, stroke[1:])):
                return item
        return None

    def _state(self, page):
        return [(a['subtype'], a['quads'], a['strokes'], a['color'], a['opacity'], a['width'], a['freehand'])
                for a in self.annotations(page)]

    def _remember_saved(self, pages):
        # A page that was not edited since the last save still shows its saved annotations.
        for page in pages:
            if page not in self._saved:
                self._saved[page] = self._state(page)

    def _change(self, pages, apply):
        self._remember_saved(pages)
        before = len(self.pdf.data)
        apply()
        # Only offsets are retained: every previous revision is already a prefix of these bytes.
        self._undo.append((before, len(self.pdf.data), pages))
        self._redo.clear()
        self._changed()

    def _changed(self):
        # Like the web reader's serialized hash: changing something back is not a change.
        self._annotations.clear()
        clean = all(same(self._state(page), saved) for page, saved in self._saved.items())
        self.state = SaveState.SAVED if clean else SaveState.UNSAVED
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

    def add_ink(self, page, strokes, color=(0, 0, 0), width=2, freehand=False):
        self._change([page], lambda: self.pdf.add_ink(page, strokes, color, width, freehand))

    def update_annotation(self, page, index, color=None, width=None):
        item = next(item for item in self.annotations(page) if item['index'] == index)
        if (color is None or all(abs(a - b) < 1e-6 for a, b in zip(color, item['color']))) and (width is None or width == item['width']):
            return False
        if width is not None and item['subtype'] != 'ink':
            return False
        self._change([page], lambda: self.pdf.update_annotation(page, index, color, width))
        return True

    def transform_annotation(self, page, index, rect):
        item = next(item for item in self.annotations(page) if item['index'] == index)
        if (item['subtype'] != 'ink' or item['rect'].width <= 0 or item['rect'].height <= 0
                or rect.width <= 0 or rect.height <= 0 or same(item['rect'], rect)):
            return False
        self._change([page], lambda: self.pdf.transform_annotation(page, index, rect))
        return True

    def delete_annotation(self, page, index):
        self._change([page], lambda: self.pdf.delete_annotation(page, index))

    def undo(self):
        return self._step(self._undo, self._redo, 0)

    def redo(self):
        return self._step(self._redo, self._undo, 1)

    def _step(self, source, target, revision):
        if not source:
            return []
        entry = source[-1]
        self._remember_saved(entry[2])
        self.pdf.restore_annotations(self.pdf.data[:entry[revision]], entry[2])
        source.pop()
        target.append(entry)
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
        self._saved.clear()
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
            self._saved.clear()
        self._words.clear()
        self._links.clear()

    def close(self):
        self.pdf.close()
