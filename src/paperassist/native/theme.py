"""Warm-paper visual tokens, HiDPI vector icons, QSS, and wrapping FlowLayout."""
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLayout

INK = "#273b39"
MUTED = "#5c6b65"
TEAL = "#276c65"
TEAL_HOVER = "#1f5953"
TEAL_SOFT = "#dbeae3"
SURFACE = "#eef1eb"
PAPER = "#f7f7f1"
PANE_BAR = "#f4f6f0"
WELCOME_BG = "#edf1ea"
DIALOG_BG = "#fbfcf7"
CANVAS = "#dce2dc"
LINE = "#d7ded7"
INPUT_BORDER = "#ccd7cd"
ERROR_INK = "#8a3a1c"

# AA-contrast status pairs: (3px left accent border, pale row background, status text color).
TONE_LOCAL = (TEAL, "#eaf3ef", "#235c55")
TONE_ACTION = ("#b7791f", "#fbf1dd", "#855510")
TONE_CONFLICT = ("#b0482a", "#f9e8e1", "#7a3216")
TONE_NEUTRAL = (LINE, PAPER, "#566661")


def reference_palette(ref):
    """Return (border_hex, fill_hex, text_hex) for a bibliography entry."""
    if ref.get("local_conflict") or ref.get("local_invalid"):
        return TONE_CONFLICT
    if ref.get("local_document_id"):
        return TONE_LOCAL
    retrieval = ref.get("retrieval") or {}
    status = retrieval.get("status") if retrieval.get("version") == 2 else None
    if ref.get("local_missing") or ref.get("legacy") or status in ("ambiguous", "blocked", "failed"):
        return TONE_ACTION
    return TONE_NEUTRAL


def brand_icon(size=22):
    """The 22x22 deep-teal badge with a white Georgia 'P' mark."""
    dpr = 2.0
    px = round(size * dpr)
    pixmap = QPixmap(px, px)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(TEAL))
    painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 4.0, 4.0)
    font = QFont("Georgia")
    font.setStyleHint(QFont.Serif)
    font.setPixelSize(round(size * 0.66))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRectF(0, -0.5, size, size), Qt.AlignCenter, "P")
    painter.end()
    return QIcon(pixmap)


def icon(name, color=INK, size=16):
    """Render a crisp 1.5px stroke icon in memory without external SVG assets."""
    dpr = 2.0
    px = round(size * dpr)
    pixmap = QPixmap(px, px)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    s = float(size)

    if name == "panels":
        painter.drawRoundedRect(QRectF(2.0, 2.5, s - 4.0, s - 5.0), 1.8, 1.8)
        painter.drawLine(QRectF(6.2, 2.5, 0, s - 5.0).topLeft(), QRectF(6.2, 2.5, 0, s - 5.0).bottomLeft())
    elif name == "find":
        painter.drawEllipse(QRectF(2.5, 2.5, 8.2, 8.2))
        painter.drawLine(QPoint(10, 10), QPoint(13, 13))
    elif name == "chevron_left":
        path = QPainterPath(QPoint(10, 4))
        path.lineTo(6, s / 2)
        path.lineTo(10, s - 4)
        painter.drawPath(path)
    elif name == "chevron_right":
        path = QPainterPath(QPoint(6, 4))
        path.lineTo(10, s / 2)
        path.lineTo(6, s - 4)
        painter.drawPath(path)
    elif name == "up":
        path = QPainterPath(QPoint(4, 9))
        path.lineTo(s / 2, 5)
        path.lineTo(s - 4, 9)
        painter.drawPath(path)
        painter.drawLine(QPoint(round(s / 2), 5), QPoint(round(s / 2), 12))
    elif name == "down":
        path = QPainterPath(QPoint(4, 7))
        path.lineTo(s / 2, 11)
        path.lineTo(s - 4, 7)
        painter.drawPath(path)
        painter.drawLine(QPoint(round(s / 2), 4), QPoint(round(s / 2), 11))
    elif name == "minus":
        painter.drawLine(QPoint(4, round(s / 2)), QPoint(round(s) - 4, round(s / 2)))
    elif name == "plus":
        mid = round(s / 2)
        painter.drawLine(QPoint(4, mid), QPoint(round(s) - 4, mid))
        painter.drawLine(QPoint(mid, 4), QPoint(mid, round(s) - 4))
    elif name == "close":
        painter.drawLine(QPoint(4, 4), QPoint(round(s) - 4, round(s) - 4))
        painter.drawLine(QPoint(round(s) - 4, 4), QPoint(4, round(s) - 4))
    elif name == "undo":
        path = QPainterPath(QPoint(6, 6))
        path.lineTo(3, 6)
        path.lineTo(3, 3)
        painter.drawPath(path)
        arc = QPainterPath(QPoint(3.5, 6))
        arc.cubicTo(6, 3, 12.5, 3.5, 12.5, 8.5)
        arc.cubicTo(12.5, 12, 9, 13, 5.5, 12.5)
        painter.drawPath(arc)
    elif name == "redo":
        path = QPainterPath(QPoint(10, 6))
        path.lineTo(13, 6)
        path.lineTo(13, 3)
        painter.drawPath(path)
        arc = QPainterPath(QPoint(12.5, 6))
        arc.cubicTo(10, 3, 3.5, 3.5, 3.5, 8.5)
        arc.cubicTo(3.5, 12, 7, 13, 10.5, 12.5)
        painter.drawPath(arc)
    elif name == "trash":
        painter.drawLine(QPoint(3, 4), QPoint(13, 4))
        painter.drawLine(QPoint(6, 2), QPoint(10, 2))
        painter.drawRoundedRect(QRectF(4.5, 4.2, 7.0, 9.3), 1.0, 1.0)
    painter.end()
    return QIcon(pixmap)


def serif_heading_font(base_font, scale=1.55):
    """Editorial Georgia/serif heading derived from base_font so DPI stays consistent."""
    font = QFont("Georgia")
    font.setStyleHint(QFont.Serif)
    if base_font.pointSizeF() > 0:
        font.setPointSizeF(base_font.pointSizeF() * scale)
    else:
        font.setPixelSize(max(14, round((base_font.pixelSize() or 13) * scale)))
    return font


class FlowLayout(QLayout):
    """Wraps compact inline controls across rows while keeping each widget at its full sizeHint."""

    def __init__(self, parent=None, margin=0, h_spacing=10, v_spacing=5):
        super().__init__(parent)
        self._items = []
        self._h, self._v = h_spacing, v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        margins = self.contentsMargins()
        max_w = max((item.minimumSize().width() for item in self._items if not item.isEmpty()), default=0)
        max_h = max((item.minimumSize().height() for item in self._items if not item.isEmpty()), default=0)
        return QSize(max_w + margins.left() + margins.right(), max_h + margins.top() + margins.bottom())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > area.right() + 1 and line_h > 0:
                x = area.x()
                y += line_h + self._v
                next_x = x + hint.width() + self._h
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


APP_STYLE = f"""
QMainWindow {{
    background: {PAPER};
    color: {INK};
}}
QToolBar {{
    background: {SURFACE};
    color: {INK};
    border: 0;
    border-bottom: 1px solid {LINE};
    spacing: 2px;
    padding: 3px 6px;
}}
QToolBar#annotation-toolbar {{
    background: {PANE_BAR};
    padding: 2px 10px;
}}
QToolBar::separator {{
    background: {LINE};
    width: 1px;
    margin: 5px 4px;
}}
QToolButton {{
    color: {INK};
    background: transparent;
    padding: 4px 6px;
    border: 1px solid transparent;
    border-radius: 3px;
}}
QToolButton:hover {{
    background: #e2e7e0;
}}
QToolButton:checked {{
    background: {TEAL_SOFT};
    color: #184944;
    border-color: #9dc2b8;
    font-weight: 600;
}}
QToolButton:focus {{
    border-color: {TEAL};
}}
QToolButton:disabled {{
    color: #9ba7a2;
}}
QToolButton#brand-button {{
    font-weight: 700;
    padding: 4px 8px 4px 5px;
}}
QToolButton#save-button[dirty="true"] {{
    background: {TEAL};
    color: #ffffff;
    border-color: {TEAL};
    font-weight: 600;
}}
QToolButton#save-button[dirty="true"]:focus {{
    border-color: #ffffff;
}}
QToolButton#save-button[dirty="true"]:hover {{
    background: {TEAL_HOVER};
}}
QToolButton#qt_toolbar_ext_button {{
    padding: 2px 4px;
    border: 1px solid transparent;
}}
QToolButton#qt_toolbar_ext_button:focus {{
    border: 1px solid {TEAL};
}}
QToolButton#column-tab {{
    padding: 6px 8px;
    border: 0;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    color: {MUTED};
    font-weight: 700;
}}
QToolButton#column-tab:hover {{
    color: {INK};
    background: transparent;
}}
QToolButton#column-tab:checked {{
    color: {INK};
    background: transparent;
    border-bottom: 2px solid {TEAL};
}}
QToolButton#column-tab:focus {{
    border: 1px solid {TEAL};
    border-bottom: 2px solid transparent;
}}
QToolButton#column-tab:checked:focus {{
    border-bottom-color: {TEAL};
}}
QPushButton {{
    color: {INK};
    background: #ffffff;
    border: 1px solid {INPUT_BORDER};
    border-radius: 3px;
    padding: 5px 11px;
}}
QPushButton:hover {{
    background: #f2f5ef;
    border-color: #aebfb3;
}}
QPushButton:checked {{
    background: {TEAL_SOFT};
    border-color: {TEAL};
    color: #184944;
}}
QPushButton:focus {{
    border-color: {TEAL};
}}
QPushButton:disabled {{
    background: {SURFACE};
    color: #95a29d;
    border-color: {LINE};
}}
QPushButton[primary="true"] {{
    background: {TEAL};
    color: #ffffff;
    border-color: {TEAL};
    font-weight: 600;
}}
QPushButton[primary="true"]:focus {{
    border-color: #ffffff;
}}
QPushButton[primary="true"]:hover {{
    background: {TEAL_HOVER};
}}
QPushButton[primary="true"]:disabled {{
    background: #d7dedb;
    color: #596b65;
    border-color: #cbd4d0;
}}
QPushButton[link="true"] {{
    background: transparent;
    border: 1px solid transparent;
    padding: 2px 2px;
    color: {TEAL};
    font-weight: 600;
    text-align: left;
}}
QPushButton[link="true"]:hover {{
    color: {TEAL_HOVER};
    text-decoration: underline;
    background: transparent;
}}
QPushButton[link="true"]:focus {{
    border-color: {TEAL};
}}
QPushButton[link="true"]:disabled {{
    color: #95a29d;
    background: transparent;
}}
QLineEdit, QTextEdit, QComboBox, QSpinBox {{
    color: {INK};
    background: #ffffff;
    border: 1px solid {INPUT_BORDER};
    border-radius: 3px;
    padding: 4px 7px;
    selection-background-color: {TEAL_SOFT};
    selection-color: {INK};
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {TEAL};
}}
QLineEdit:read-only, QLineEdit:disabled {{
    background: {SURFACE};
    color: {MUTED};
}}
QComboBox::drop-down {{
    border: 0;
    width: 20px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    border: 0;
    background: transparent;
    width: 16px;
}}
QToolButton#tab-close-button {{
    padding: 1px;
    border: 0;
    border-radius: 3px;
}}
QToolButton#tab-close-button:focus {{
    border: 1px solid {TEAL};
}}
QToolButton#tab-close-button:hover {{
    background: #dce3dd;
}}
QScrollArea#comments-scroll, QScrollArea#comments-scroll > QWidget > QWidget {{
    background: {PAPER};
    border: 0;
}}
QTabWidget::pane {{
    border: 0;
    background: {CANVAS};
}}
QTabBar {{
    background: {SURFACE};
    border-bottom: 1px solid {LINE};
}}
QTabBar::tab {{
    background: {SURFACE};
    color: {MUTED};
    padding: 6px 12px;
    border: 0;
    border-right: 1px solid {LINE};
    border-top: 2px solid transparent;
}}
QTabBar::tab:hover {{
    color: {INK};
    background: #e6ebe4;
}}
QTabBar::tab:selected {{
    background: {PAPER};
    color: {INK};
    font-weight: 600;
    border-top: 2px solid {TEAL};
}}
QTabBar::tab:selected:focus {{
    border-bottom: 2px solid {TEAL};
}}
SideColumn {{
    background: {PAPER};
}}
QWidget#column-header {{
    background: {SURFACE};
    border-bottom: 1px solid {LINE};
}}
QLabel#column-title {{
    color: {MUTED};
    font-weight: 700;
}}
QSplitter::handle:horizontal {{
    background: {LINE};
    width: 1px;
}}
QTreeWidget, QListWidget {{
    background: {PAPER};
    color: {INK};
    border: 0;
}}
QTreeWidget#dialog-tree {{
    background: #ffffff;
    border: 1px solid {LINE};
    border-radius: 3px;
}}
QHeaderView::section {{
    background: {SURFACE};
    color: {MUTED};
    font-weight: 600;
    padding: 5px 8px;
    border: 0;
    border-bottom: 1px solid {LINE};
    border-right: 1px solid {LINE};
}}
QTreeWidget::item {{
    padding: 4px 4px;
}}
QTreeWidget::item:hover {{
    background: #e7eee8;
}}
QTreeWidget::item:selected {{
    background: {TEAL_SOFT};
    color: {INK};
}}
QTreeWidget::item:focus, QListWidget::item:focus {{
    border: 1px solid {TEAL};
}}
QScrollArea {{
    background: transparent;
    border: 0;
}}
QScrollArea#dialog-scroll {{
    background: #ffffff;
    border: 1px solid {LINE};
    border-radius: 3px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #c1cbc3;
    min-height: 28px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: #9aa99f;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 9px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #c1cbc3;
    min-width: 28px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QToolBar#pane-bar {{
    background: {PANE_BAR};
    border-bottom: 1px solid {LINE};
    padding: 2px 8px;
}}
QToolBar#pane-bar QToolButton {{
    color: {TEAL};
    font-weight: 600;
}}
FindBar {{
    background: {PANE_BAR};
    border-bottom: 1px solid {LINE};
}}
#pane {{
    border-top: 2px solid transparent;
}}
#pane[active="true"] {{
    border-top: 2px solid {TEAL};
}}
QWidget#welcome-screen {{
    background: {WELCOME_BG};
}}
QFrame#welcome-rule {{
    background: {TEAL};
    border: 0;
}}
QLabel#welcome-eyebrow {{
    color: {TEAL};
    font-weight: 700;
}}
QLabel#muted-note {{
    color: {MUTED};
}}
QWidget#expanded-card {{
    border: 1px solid #7e968d;
    border-radius: 2px;
}}
QWidget#comment-row {{
    border-bottom: 1px solid {LINE};
}}
QLabel#comment-quote {{
    color: {MUTED};
    border-left: 2px solid #90aca0;
    padding-left: 8px;
}}
QMenu {{
    background: {DIALOG_BG};
    color: {INK};
    border: 1px solid #c9d4ca;
    border-radius: 4px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 20px 6px 10px;
    border-radius: 3px;
}}
QMenu::item:selected {{
    background: #e8ece6;
    color: {INK};
}}
QMenu::separator {{
    height: 1px;
    background: {LINE};
    margin: 4px 2px;
}}
QStatusBar {{
    background: {SURFACE};
    color: {MUTED};
    border-top: 1px solid {LINE};
}}
QProgressBar {{
    background: #e1e6df;
    border: 0;
    border-radius: 3px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {TEAL};
    border-radius: 3px;
}}
QDialog {{
    background: {DIALOG_BG};
    color: {INK};
}}
QFrame#source-row, QFrame#acquire-row {{
    border-bottom: 1px solid {LINE};
}}
QFrame#preferences-library-confirm {{
    background: {SURFACE};
    border: 0;
    border-left: 3px solid {TEAL};
    border-radius: 3px;
}}
"""
