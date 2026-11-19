"""Qt view helpers for stepping and revealing citations (matching lives in paperassist.citations)."""
from paperassist.citations import citation_parts


def reading_position(view):
    page, fraction = view.pages.anchor(view.verticalScrollBar().value())
    return page + 1 + fraction


def citation_position(view, citation):
    return citation["page"] + (citation["rects"][0].y0 / view.doc.sizes[citation["page"] - 1][1] if citation["rects"] else 0)


def reveal_citation(view, citation):
    page, rects = citation["page"] - 1, citation["rects"]
    view.clear_outlines()
    if not rects:
        view.goto(page)
        return False
    band = rects[0]
    for rect in rects[1:]:
        band = band.union(rect)
    x, y = view.pages.to_content(page, (band.x0 + band.x1) / 2, (band.y0 + band.y1) / 2)
    view.verticalScrollBar().setValue(round(y - view.viewport().height() / 2))
    view.horizontalScrollBar().setValue(round(x - view.viewport().width() / 2))
    view.outline_rects([(part_page - 1, rect) for part_page, part_rects in citation_parts(citation) for rect in part_rects])
    return True
