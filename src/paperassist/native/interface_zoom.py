"""Interface font and spacing rules; PDF zoom stays independent."""
ZOOM_CHOICES = (0.8, 0.9, 1, 1.1, 1.25, 1.5)
STYLE_START = "\n/* paperassist-ui-zoom */\n"
STYLE_END = "\n/* /paperassist-ui-zoom */"


def parse_zoom(stored):
    """Accept only the six validated zoom factors so stale config values fall back to 100%."""
    return next((c for c in ZOOM_CHOICES if type(stored) in (int, float) and stored == c), 1)


def _strip_zoom_style(style):
    start = style.find(STYLE_START)
    if start < 0:
        return style
    end = style.find(STYLE_END, start)
    return style[:start] + (style[end + len(STYLE_END):] if end >= 0 else "")


def _zoom_rules(font, zoom):
    size = (f"{font.pointSizeF() * zoom:g}pt" if font.pointSizeF() > 0
            else f"{max(1, round((font.pixelSize() or 13) * zoom))}px")
    return f"""{STYLE_START}QToolBar, QToolBar QWidget, QTabBar, QMenu, QStatusBar, QStatusBar QWidget,
SideColumn, SideColumn QWidget, FindBar, FindBar QWidget, #pane-bar, #pane-bar QWidget,
QDialog, QDialog QWidget {{ font-size: {size}; }}
QToolBar {{ spacing: {round(2 * zoom)}px; }}
QToolButton {{ padding: {round(3 * zoom)}px {round(8 * zoom)}px; border-radius: {round(4 * zoom)}px; }}
QTabBar::tab {{ padding: {round(5 * zoom)}px {round(12 * zoom)}px; }}{STYLE_END}"""


def window_style(style, font, zoom):
    """Replace only our zoom block, preserving other window style rules."""
    base = _strip_zoom_style(style)
    return base if zoom == 1 else base + _zoom_rules(font, zoom)
