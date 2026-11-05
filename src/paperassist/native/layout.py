"""Page geometry and the pixmap cache of one continuous view, without any widget code.

Content coordinates are logical pixels from the top-left of the scrolled content;
page coordinates are PDF points from the top-left of a page.
"""
from collections import OrderedDict

MARGIN, GAP = 12, 10


class PageLayout:
    def __init__(self, sizes, zoom, viewport_width):
        self.sizes, self.zoom = sizes, zoom
        self.tops, y = [], MARGIN
        for _, height in sizes:
            self.tops.append(y)
            y += height * zoom + GAP
        self.height = y - GAP + MARGIN
        self.width = max(viewport_width, max(w for w, _ in sizes) * zoom + 2 * MARGIN)

    def rect(self, page):
        """(x, y, width, height) of a page in content coordinates; pages are centered."""
        w, h = self.sizes[page]
        return (self.width - w * self.zoom) / 2, self.tops[page], w * self.zoom, h * self.zoom

    def visible(self, top, bottom):
        return [i for i, (_, h) in enumerate(self.sizes) if self.tops[i] < bottom and self.tops[i] + h * self.zoom > top]

    def page_at(self, y):
        """The page owning a content y; gaps belong to the page below them."""
        return max((i for i, top in enumerate(self.tops) if top - GAP / 2 <= y), default=0)

    def to_page(self, x, y):
        page = self.page_at(y)
        left, top, _, _ = self.rect(page)
        return page, (x - left) / self.zoom, (y - top) / self.zoom

    def to_content(self, page, x, y):
        left, top, _, _ = self.rect(page)
        return left + x * self.zoom, top + y * self.zoom

    def anchor(self, top):
        """(page, fraction of that page above the viewport top), to keep the position through a zoom."""
        page = self.page_at(top - GAP / 2)  # the page whose top is at or above the viewport top
        return page, (top - self.tops[page]) / (self.sizes[page][1] * self.zoom)

    def top_of(self, page, fraction):
        return self.tops[page] + fraction * self.sizes[page][1] * self.zoom


class PageCache:
    """Pixmaps by page with a byte budget; the least recently used go first.

    A fit-width page at 1.5x HiDPI is about 22 MB, so a count limit would be wrong
    for big zooms. The newest entry always stays, even when it alone exceeds the budget.
    """

    def __init__(self, budget):
        self.budget, self.entries = budget, OrderedDict()

    def __contains__(self, page):
        return page in self.entries

    def __getitem__(self, page):
        self.entries.move_to_end(page)
        return self.entries[page][0]

    def get(self, page):
        return self.entries[page][0] if page in self.entries else None

    def put(self, page, value, size):
        self.entries.pop(page, None)
        self.entries[page] = (value, size)
        while len(self.entries) > 1 and self.total() > self.budget:
            self.entries.popitem(last=False)

    def total(self):
        return sum(size for _, size in self.entries.values())

    def clear(self):
        self.entries.clear()
