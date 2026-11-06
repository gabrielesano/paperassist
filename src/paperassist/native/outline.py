"""The outline column: bookmarks of the main paper, or a page list when it has none.

Bookmarks are read with pypdf, so the PDF engine stays behind engine.py.
"""
import io

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem


def read_outline(data):
    """[(title, level, page index, y)] with y in displayed points from the page top; [] without bookmarks."""
    from pypdf import PdfReader  # loaded on first use: most sessions never need it in this process
    entries = []
    try:
        reader = PdfReader(io.BytesIO(data))

        def walk(items, level):
            for item in items:
                if isinstance(item, list):
                    walk(item, level + 1)
                    continue
                page = reader.get_destination_page_number(item)
                if page is not None and 0 <= page < len(reader.pages):
                    entries.append((str(item.title), level, page, _top(reader.pages[page], item.top)))

        walk(reader.outline, 0)
    except Exception:  # a damaged outline must not stop the paper from being read
        return entries
    return entries


def _top(page, top):
    """Rotated pages keep to the page top: converting their coordinates is the engine's job."""
    if top is None or page.rotation % 360:
        return 0.0
    return max(0.0, float(page.cropbox.top) - float(top))


class OutlinePanel(QTreeWidget):
    navigate = Signal(int, float)  # page index, y in points

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setAccessibleName("Document outline")
        self.itemClicked.connect(self._clicked)

    def show_outline(self, entries, page_count):
        self.clear()
        if not entries:
            entries = [(f"Page {number + 1}", 0, number, 0.0) for number in range(page_count)]
        parents = []
        for title, level, page, y in entries:
            del parents[level:]
            item = QTreeWidgetItem(parents[-1] if parents else self, [title])
            item.setData(0, Qt.UserRole, (page, y))
            item.setToolTip(0, title)
            parents.append(item)
        self.expandAll()

    def _clicked(self, item):
        self.navigate.emit(*item.data(0, Qt.UserRole))
