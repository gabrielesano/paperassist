"""Citation matching and geometry. Rectangles use the engine's displayed points."""
import math
import re
import unicodedata
from collections import defaultdict

from .engine import Pdf, Rect

IGNORED = set("\u00ad\u2010\u2011\u2012\u2013\u2014\u2015\u2212-`¨¯´¸ˆ˘˙˚˛˜˝\ufffe\x02")


def bibliography_destination(name):
    """Recognize explicit bibliography targets without treating figure or affiliation links as citations."""
    return bool(name and re.search(r"(?:^|[._:-])(?:bib|ref|cite)(?:[._:\d-]|$)|bib\d+$", name, re.I))


def visible_chars(text):
    for at, original in enumerate(text):
        for char in unicodedata.normalize("NFKD", original).lower():
            if char.isspace() or unicodedata.category(char).startswith("M") or char in IGNORED:
                continue
            yield ("i" if char == "ı" else char), at


def normalize(text):
    return "".join(char for char, _ in visible_chars(text))


def build_text_index(items):
    text, owner = [], []
    for number, item in enumerate(items):
        if not isinstance(item.get("str"), str):
            continue
        for char, at in visible_chars(item["str"]):
            text.append(char)
            owner.append((number, at))
    return {"text": "".join(text), "owner": owner}


def find_markers(index, markers):
    used, found = defaultdict(int), []
    for marker in markers:
        needle = normalize(marker or "")
        at, start = -1, 0
        if needle:
            for _ in range(used[needle] + 1):
                at = index["text"].find(needle, start)
                if at < 0:
                    break
                start = at + len(needle)
            used[needle] += 1
        found.append({"start": at, "end": at + len(needle)} if at >= 0 else None)
    return found


def item_rect(item, start=0, end=1):
    if "rect" in item:
        x0, y0, x1, y1 = item["rect"]
        return Rect(x0 + (x1 - x0) * start, y0, x0 + (x1 - x0) * end, y1)
    a, b, c, d, e, f = item["transform"]
    along, up = math.hypot(a, b) or 1, math.hypot(c, d) or 1
    corners = []
    for part in (start, end):
        x, y = e + a / along * item["width"] * part, f + b / along * item["width"] * part
        corners.extend(((x, y), (x + c / up * item["height"], y + d / up * item["height"])))
    return Rect(min(x for x, _ in corners), min(y for _, y in corners),
                max(x for x, _ in corners), max(y for _, y in corners))


def same_line(a, b):
    return abs(a[1] - b[1]) < (a[3] - a[1]) * 0.5


def match_rects(items, index, match):
    first, first_char = index["owner"][match["start"]]
    last, last_char = index["owner"][match["end"] - 1]
    rects = []
    for number in range(first, last + 1):
        item = items[number]
        if not isinstance(item.get("str"), str) or not item["str"].strip():
            continue
        size = len(item["str"])
        rect = item_rect(item, (first_char if number == first else 0) / size,
                         (last_char + 1 if number == last else size) / size)
        if rects and same_line(rects[-1], rect):
            rects[-1] = rects[-1].union(rect)
        else:
            rects.append(rect)
    return rects


def nearest_item(items, rect):
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    def distance(pair):
        _, item = pair
        x0, y0, x1, y1 = item_rect(item)
        return math.hypot(max(x0 - cx, 0, cx - x1), max(y0 - cy, 0, cy - y1))
    return min(((i, item) for i, item in enumerate(items) if item.get("str", "").strip()),
               key=distance, default=(0, None))[0]


def merge_links(links, displayed=False):
    merged = []
    for link in links:
        previous = merged[-1] if merged else None
        last = previous["rects"][-1] if previous else None
        rect = link["rects"][0]
        height = max(rect[3] - rect[1], last[3] - last[1] if last else 0, 8)
        gap = (rect[1] - last[3] if displayed else last[1] - rect[3]) if previous else math.inf
        text = previous.get("text", "").strip() if previous else ""
        complete = complete_citation(text)
        # Geometry alone cannot distinguish a wrapped citation from a new citation on the next line.
        wrapped = -height * .5 < gap <= (height * 2.2 if text else 2) and not complete
        touches = previous and previous["page"] == link["page"] and previous["dest"] == link["dest"] and (
            (abs(last[1] - rect[1]) < height * .5 and -3 <= rect[0] - last[2] < height * 2 and rect[0] > last[0]
             and (not text or text != link.get("text", "").strip())
             and not (complete and complete_citation(link.get("text", "")))) or
            (wrapped and rect[0] <= last[0] + height * 2.5))
        if touches:
            previous["rects"].extend(link["rects"])
            previous["text"] = (previous.get("text", "") + " " + link.get("text", "")).strip()
        else:
            merged.append({**link, "rects": list(link["rects"])})
    return merged


def complete_citation(text):
    return re.search(r"\b(?:19|20)\d{2}[a-z]?\s*[\])}.,;]*$|^\[?\d+[\d,; –-]*\]?$", text.strip())


def pdf_rect(frame, rect):
    points = [frame.to_pdf(x, y) for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
    return Rect(min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points))


def next_citation_index(positions, last, here):
    if not positions:
        return -1
    if last is not None:
        return (last + 1) % len(positions)
    return next((i for i, position in enumerate(positions) if position >= here), 0)


def citation_matches(refs, text, destination=None):
    """Only recognized markers consume links; uncertain candidates remain an explicit choice."""
    if destination is not None:
        named = [ref for ref in refs if destination in ref.get("destinations", [])]
        if named:
            return named
        if re.search(r"(?:^|[._:-])(?:fig(?:ure)?|sec(?:tion)?|eq(?:uation)?|table|tab)(?=[._:\d-]|$)",
                     destination, re.I):
            return []
    text = text.strip().rstrip(".,;")
    marker = re.fullmatch(r"\[([\d,;\s–—−-]+)\]", text)
    if marker:
        numbers = []
        for part in re.split("[,;]", marker[1]):
            match = re.fullmatch(r"\s*(\d+)(?:\s*[–—−-]\s*(\d+))?\s*", part)
            if not match:
                return []
            start, end = int(match[1]), int(match[2] or match[1])
            if end < start or end - start > 30:
                return []
            numbers.extend(range(start, end + 1))
        groups = [[ref for ref in refs if re.sub(r"[\[\].\s]", "", str(ref.get("label", ""))) == str(n)] for n in numbers]
        return list({ref["id"]: ref for group in groups for ref in group}.values()) if all(groups) else []
    author_year = re.fullmatch(r"\(?([^\W\d_][\w'’\-]+)(?:\s+et al\.?)?[,]?\s+\(?(19\d{2}|20\d{2})\)?\)?", text.strip())
    if not author_year:
        return []
    found = []
    for ref in refs:
        author = re.split(r"[,;]|\band\b", str(ref.get("authors") or ""), flags=re.I)[0].strip().split()
        if author and normalize(author[-1]) == normalize(author_year[1]) and str(ref.get("year")) == author_year[2]:
            found.append(ref)
    return found


class CitationIndex:
    """Per-document index of named links and cached word boxes for citation stepping."""
    def __init__(self, data):
        self._pdf = Pdf(data)
        self._pages = {}
        self._links_loaded = False
        self.links, self.destination_pages = [], {}
        self._bibliography = None

    def _load_links(self):
        if self._links_loaded:
            return
        self._links_loaded = True
        self.destination_pages = self._pdf.named_destinations()
        for page_number in range(len(self._pdf.sizes)):
            for link in self._pdf.links(page_number):
                if link.destination is not None:
                    self.links.append({"page": page_number + 1, "dest": link.destination, "rects": [link.rect]})
                    self.destination_pages[link.destination] = link.page

    def destination_at(self, page, rect):
        self._load_links()
        return next((link["dest"] for link in self.links if link["page"] == page + 1 and
                     all(abs(a - b) < 1 for a, b in zip(link["rects"][0], rect))), None)

    def _items(self, number):
        if number not in self._pages:
            self._pages[number] = [{"str": word[4], "rect": Rect(*word[:4]), "line": word[5]}
                                   for word in self._pdf.words(number - 1)]
        return self._pages[number]

    def prepare_bibliography(self, refs, heading_page):
        """Mask recognized entry rows, preserving body text and appendices on the same page."""
        self._load_links()
        self._bibliography = defaultdict(list)
        entries = ["".join(c for c in normalize(ref["raw"]) if c.isalnum()) for ref in refs]
        last = max((self.destination_pages[name] + 1 for ref in refs for name in ref.get("destinations", [])
                    if name in self.destination_pages), default=heading_page)
        for page in range(heading_page, last + 1):
            rows = defaultdict(list)
            for item in self._items(page):
                rows[item["line"]].append(item)
            for row in rows.values():
                text = " ".join(item["str"] for item in row)
                text = re.sub(r"^\s*(?:\[\d+\]|\d+\.)\s*", "", text)
                normalized = "".join(c for c in normalize(text) if c.isalnum())
                if len(normalized) >= 12 and any(normalized in raw for raw in entries):
                    rects = [item["rect"] for item in row]
                    self._bibliography[page].append(Rect(min(r.x0 for r in rects) - 3, min(r.y0 for r in rects) - 2,
                                                         max(r.x1 for r in rects) + 3, max(r.y1 for r in rects) + 2))
            # Analysis visits bibliography pages once; keep only pages used by citation locations.
            self._pages.pop(page, None)

    def _in_bibliography(self, page, rect):
        return self._bibliography is not None and any(
            band.contains((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
            for band in self._bibliography.get(page, []))

    def locate(self, ref):
        if ref.get("occurrence_geometry") == "pdf":
            return [{**occurrence, "rects": [self._pdf.frame(occurrence["page"] - 1).rect(*rect)
                                             for rect in occurrence.get("rects", [])]}
                    for occurrence in ref.get("occurrences", [])]
        names = ref.get("destinations") or []
        if names:
            self._load_links()
        bibliography = min((self.destination_pages[name] + 1 for name in names if name in self.destination_pages), default=math.inf)
        by_destination = defaultdict(list)
        seen = set()
        for link in self.links:
            if link["dest"] not in names or self._in_bibliography(link["page"], link["rects"][0]):
                continue
            if self._bibliography is None and link["page"] == bibliography:
                continue  # old callers without entry geometry cannot classify this page safely
            identity = (link["page"], tuple(link["rects"][0]))
            if identity in seen:
                continue
            seen.add(identity)
            items, rect = self._items(link["page"]), link["rects"][0]
            text = " ".join(item["str"] for item in items
                            if overlaps(item_rect(item), rect)
                            and min(item["rect"].height, rect.height) >= 2
                            and (min(item["rect"].y1, rect.y1) - max(item["rect"].y0, rect.y0))
                            > min(item["rect"].height, rect.height) * .5)
            native = pdf_rect(self._pdf.frame(link["page"] - 1), rect)
            by_destination[link["dest"]].append({**link, "rects": [native], "text": text,
                                                 "key": nearest_item(items, rect)})
        links = [event for group in by_destination.values()
                 for event in merge_links(sorted(group, key=lambda link: (link["page"], link["key"], link["rects"][0][0])))]
        for event in links:
            event["rects"] = [self._pdf.frame(event["page"] - 1).rect(*rect) for rect in event["rects"]]
        markers = defaultdict(list)
        for occurrence in ref.get("occurrences", []):
            if 1 <= occurrence.get("page", 0) <= len(self._pdf.sizes):
                markers[occurrence["page"]].append(occurrence)
        citations = []
        for page, values in sorted(markers.items()):
            text = self._items(page)
            index = build_text_index(text)
            found = []
            for occurrence, match in zip(values, find_markers(index, [o.get("marker", "") for o in values])):
                rects = match_rects(text, index, match) if match else []
                if rects and self._in_bibliography(page, rects[0]):
                    continue
                if any(link["page"] == page and any(overlaps(a, b) for a in rects for b in link["rects"])
                       for link in links):
                    continue
                found.append({**occurrence, "rects": rects,
                              "key": nearest_item(text, rects[0]) if rects else math.inf})
            citations.extend(sorted(found, key=lambda item: item["key"]))
        return sorted(links + citations, key=lambda item: (item["page"], item["key"]))

    def pdf_occurrences(self, ref):
        """Persist geometry in PDF coordinates so crop and rotation cannot move saved events."""
        return [{"page": event["page"], "marker": event.get("marker", event.get("text", "")),
                 "context": event.get("context", event.get("text", "")),
                 "rects": [list(pdf_rect(self._pdf.frame(event["page"] - 1), rect)) for rect in event["rects"]],
                 "source": "link" if event.get("dest") else "text"} for event in self.locate(ref)]

    def close(self):
        self._pages.clear()
        if not self._pdf.is_closed:
            self._pdf.close()


def overlaps(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


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
    view.outline_rects([(page, rect) for rect in rects])
    return True
