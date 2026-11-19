"""The outline column: bookmarks of the main paper via PDFium, or a page list when it has none."""
from contextlib import closing

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from .engine import Pdf


def read_outline(source):
    """[(title, level, page index, y)] with y in displayed points from the page top; [] without bookmarks."""
    try:
        if isinstance(source, Pdf):
            return source.outline()
        with closing(Pdf(source)) as pdf:
            return pdf.outline()
    except Exception:  # a damaged outline must not stop the paper from being read
        return []


class OutlinePanel(QTreeWidget):
    navigate = Signal(int, float)  # page index, y in points

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setAnimated(False)
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
