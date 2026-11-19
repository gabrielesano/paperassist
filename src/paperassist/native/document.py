"""One PDF seen from the UI process: text, links, search, highlights and saving.

Pixels come from the render process; this object does the cheap structural work and
is the only writer of the file, on explicit save. It works on the plain data of
engine.py: coordinates are what the user sees, in PDF points.
"""
from enum import Enum
import re

from .engine import Rect

_LINE_NUMBER = re.compile(r"^[0-9]{1,5}$")


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


def _chain_margin_candidates(ordered, step):
    seqs = []
    for item in ordered:
        _, val, _, y0, _, _ = item
        if step > 1 and val % step != 0:
            continue
        max_jump = 20 * step
        for seq in reversed(seqs):
            _, prev_val, _, prev_y0, _, _ = seq[-1]
            if val <= prev_val or y0 <= prev_y0 + 1:
                continue
            if val - prev_val <= max_jump:
                seq.append(item)
                break
        else:
            seqs.append([item])
    if not seqs:
        return []
    best = max(seqs, key=len)
    if len(best) < (3 if step == 10 else 4):
        return []
    pairs = list(zip(best, best[1:]))
    exact = sum(1 for a, b in pairs if b[1] - a[1] == step)
    if step == 10 and len(best) == 3:
        return best if exact == len(pairs) else []
    return best if exact * 2 >= len(pairs) else []


def _contiguous_span(words, from_left, gap=18.0):
    if from_left:
        intervals = sorted((w[0], w[2]) for w in words)
        start, end = intervals[0]
        for x0, x1 in intervals[1:]:
            if x0 > end + gap:
                break
            end = max(end, x1)
        return end - start
    intervals = sorted(((w[0], w[2]) for w in words), key=lambda pair: pair[1], reverse=True)
    start, end = intervals[0]
    for x0, x1 in intervals[1:]:
        if x1 < start - gap:
            break
        start = min(start, x0)
    return end - start


def _line_edges(prose):
    """Per-line (min_x0, max_x1) of prose words grouped by PDFium line index."""
    edges = {}
    for w in prose:
        line = w[5]
        if line in edges:
            x0, x1 = edges[line]
            edges[line] = (min(x0, w[0]), max(x1, w[2]))
        else:
            edges[line] = (w[0], w[2])
    return list(edges.values())


def _margin_or_gutter(band_x0, band_x1, prose, cluster_y0, cluster_y1, page_width):
    """True when a vertical digit band sits in an outer margin or a two-column gutter."""
    local = [w for w in prose if w[3] >= cluster_y0 - 2 and w[1] <= cluster_y1 + 2]
    if not local:
        return False
    crossing = sum(1 for w in local if w[0] < band_x1 + 1 and w[2] > band_x0 - 1)
    if crossing > max(1, len(local) // 40):
        return False
    left = [w for w in local if w[2] <= band_x0 + 1]
    right = [w for w in local if w[0] >= band_x1 - 1]
    edges = _line_edges(prose)
    if not left:
        starts = sorted(x0 for x0, _ in edges)
        return band_x1 < starts[int(len(starts) * 0.04)] - 1
    if not right:
        ends = sorted(x1 for _, x1 in edges)
        return band_x0 > ends[min(len(ends) - 1, int(len(ends) * 0.96))] + 1
    return (len(left) >= 4 and len(right) >= 4
            and _contiguous_span(left, False) > page_width * 0.15
            and _contiguous_span(right, True) > page_width * 0.15)


def detect_margin_line_numbers(words, page_width):
    """Word indices of margin or inter-column gutter line numbers on a single page."""
    prose = [w for w in words if any(ch.isalpha() for ch in w[4])]
    if len(prose) < 4:
        return set()
    candidates = []
    for index, w in enumerate(words):
        text = w[4].strip()
        if not _LINE_NUMBER.match(text):
            continue
        val = int(text)
        if val < 1:
            continue
        x0, y0, x1, y1 = w[:4]
        line_prose = [p for p in prose if min(p[3], y1) > max(p[1], y0)]
        if not line_prose or any(p[0] < x1 + 1 and p[2] > x0 - 1 for p in line_prose):
            continue
        candidates.append((index, val, x0, y0, x1, y1))
    if len(candidates) < 3:
        return set()

    # Cluster by x-alignment (right edge or left edge within 10 pt) so outer margins and gutters are independent.
    clusters = []
    for item in sorted(candidates, key=lambda c: (c[4], c[2])):
        for cluster in clusters:
            anchor = cluster[0]
            if abs(item[4] - anchor[4]) <= 10 or abs(item[2] - anchor[2]) <= 10:
                cluster.append(item)
                break
        else:
            clusters.append([item])

    ignored = set()
    for cluster in clusters:
        if len(cluster) < 3:
            continue
        band_x0 = min(c[2] for c in cluster)
        band_x1 = max(c[4] for c in cluster)
        cluster_y0 = min(c[3] for c in cluster)
        cluster_y1 = max(c[5] for c in cluster)
        if not _margin_or_gutter(band_x0, band_x1, prose, cluster_y0, cluster_y1, page_width):
            continue
        ordered = sorted(cluster, key=lambda c: (c[3], c[0]))
        chain = max((_chain_margin_candidates(ordered, step) for step in (10, 5, 1)), key=len)
        if chain:
            ignored.update(c[0] for c in chain)
    return ignored


class PdfDocument:
    def __init__(self, source):
        self.source = source
        self._pdf = source.open()
        self.sizes = self._pdf.sizes
        self.state, self.error = SaveState.SAVED, ""
        self._words, self._links, self._line_numbers = {}, {}, {}
        self._undo, self._redo = [], []
        self._saved = {}  # page -> annotation state at the last save, for every page edited since
        self._annotations = {}

    @property
    def pdf(self):
        if self._pdf is None:
            self._pdf = self.source.open()
        return self._pdf

    @pdf.setter
    def pdf(self, value):
        self._pdf = value

    def hibernate(self):
        """Release the UI-side PDF handle and text caches while an unmodified tab is hidden."""
        if not self.modified and not self._undo and not self._redo and self._pdf is not None:
            self._pdf.close()
            self._pdf = None
            self._words.clear()
            self._links.clear()
            self._line_numbers.clear()
            self._annotations.clear()

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

    def _line_number_indices(self, page):
        if page not in self._line_numbers:
            self._line_numbers[page] = detect_margin_line_numbers(self.words(page), self.sizes[page][0])
        return self._line_numbers[page]

    def word_at(self, page, x, y):
        """Index of the word under the point, or the nearest one (lines weigh more than columns)."""
        def distance(w):
            return max(w[0] - x, 0, x - w[2]) ** 2 + 4 * max(w[1] - y, 0, y - w[3]) ** 2
        words = self.words(page)
        ignored = self._line_number_indices(page)
        indices = [i for i in range(len(words)) if i not in ignored] or range(len(words))
        return min(indices, key=lambda i: distance(words[i]), default=None)

    def selection_lines(self, start, end):
        """[(page, rect, text)], one band per visual line, between two (page, word index) ends.

        A PDF may split a justified line into several 'lines', so words join the current
        band by geometry: same block, mostly overlapping vertically, further to the right.
        """
        start, end = sorted([start, end])
        bands = []
        for page in range(start[0], end[0] + 1):
            words = self.words(page)
            ignored = self._line_number_indices(page)
            first = start[1] if page == start[0] else 0
            last = end[1] if page == end[0] else len(words) - 1
            for idx in range(first, last + 1):
                if idx in ignored:
                    continue
                w = words[idx]
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
        unseen = [page for page in pages if page not in self._saved]
        missing = [page for page in unseen if page not in self._annotations]
        if missing:
            self._annotations.update(self.pdf.annotations_for_pages(missing))
        for page in unseen:
            self._saved[page] = self._state(page)

    def _change(self, pages, apply):
        self._remember_saved(pages)
        before = len(self.pdf.data)
        apply()
        # Only offsets are retained: every previous revision is already a prefix of these bytes.
        self._undo.append((before, len(self.pdf.data), pages))
        self._redo.clear()
        self._changed(pages)

    def _changed(self, pages):
        # Like the web reader's serialized hash: changing something back is not a change.
        self._annotations.clear()
        if len(pages) > 1:
            self._annotations.update(self.pdf.annotations_for_pages(self._saved))
        clean = all(same(self._state(page), saved) for page, saved in self._saved.items())
        self.state = SaveState.SAVED if clean else SaveState.UNSAVED
        self.error = ''

    def add_highlight(self, page, rects, color=None):
        options = {} if color is None else {'color': color}
        self._change([page], lambda: self.pdf.add_highlight(page, rects, **options))

    def add_highlights(self, pages, color):
        if pages:
            self._change(list(pages), lambda: self.pdf.add_highlights(pages, color))

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
        self._changed(entry[2])
        return entry[2]

    def save(self, on_saving=lambda: None):
        """Explicit save only. A failure keeps the edits and the unsaved state for a retry."""
        self.state = SaveState.SAVING
        on_saving()
        try:
            self.source.save(self.pdf)
        except Exception as error:  # the disk, permissions or the PDF engine: all are shown to the user
            self.state, self.error = SaveState.FAILED, str(error) or type(error).__name__
            return False
        self._saved.clear()
        self.state, self.error = SaveState.SAVED, ""
        return True

    def reload(self, keep_history=False):
        """Read the saved file again."""
        previous = self._pdf.data if self._pdf is not None else None
        replacement = self.source.open()
        if self._pdf is not None:
            self._pdf.close()
        self._pdf = replacement
        self._annotations.clear()
        if not keep_history or self._pdf.data != previous:
            self._undo.clear()
            self._redo.clear()
            self._saved.clear()
        self._words.clear()
        self._links.clear()
        self._line_numbers.clear()

    def close(self):
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None
