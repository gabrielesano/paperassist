// Finds where a reference is cited in the open paper and shows one citation at a time.
// Everything shown is a DOM overlay on the page, so it never touches PDF.js annotations
// and cannot make the PDF modified.

// pypdf and PDF.js disagree on spaces, line breaks, dash styles and ligatures, so
// markers are compared only by their visible characters.
function* visibleChars(text) {
    for (let i = 0; i < text.length; i++) {
        for (const char of text[i].normalize("NFKC").toLowerCase()) {
            if (/[\s­]/.test(char)) continue;
            yield [/[‐-―−]/.test(char) ? "-" : char, i];
        }
    }
}

// `owner[k]` says which text item, and which character of it, produced text[k].
export function buildTextIndex(items) {
    let text = "";
    const owner = [];
    items.forEach((item, i) => {
        if (typeof item.str !== "string") return;
        for (const [char, at] of visibleChars(item.str)) {
            text += char;
            for (let k = 0; k < char.length; k++) owner.push([i, at]);
        }
    });
    return { text, owner };
}

// The same marker can appear several times on a page; the n-th occurrence listed by the
// server is matched to the n-th place it is found.
export function findMarkers(index, markers) {
    const used = new Map();
    return markers.map((marker) => {
        const needle = [...visibleChars(marker)].map(([char]) => char).join("");
        if (!needle) return null;
        const skip = used.get(needle) ?? 0;
        used.set(needle, skip + 1);
        let from = 0;
        let at = -1;
        for (let n = 0; n <= skip; n++) {
            at = index.text.indexOf(needle, from);
            if (at < 0) return null;
            from = at + needle.length;
        }
        return { start: at, end: at + needle.length };
    });
}

// Box of part of a text item, in PDF user space, for upright or rotated text.
function itemRect({ transform: [a, b, c, d, e, f], width, height }, from, to) {
    const along = Math.hypot(a, b) || 1;
    const up = Math.hypot(c, d) || 1;
    const corners = [from, to].flatMap((t) => {
        const x = e + (a / along) * width * t;
        const y = f + (b / along) * width * t;
        return [[x, y], [x + (c / up) * height, y + (d / up) * height]];
    });
    const xs = corners.map(([x]) => x);
    const ys = corners.map(([, y]) => y);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

const sameLine = (a, b) => Math.abs(a[1] - b[1]) < (a[3] - a[1]) * 0.5;
const union = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];

// One box per line the match covers.
export function matchRects(items, index, { start, end }) {
    const [first, firstChar] = index.owner[start];
    const [last, lastChar] = index.owner[end - 1];
    const rects = [];
    for (let i = first; i <= last; i++) {
        const item = items[i];
        if (typeof item.str !== "string" || !/\S/.test(item.str)) continue;
        const size = item.str.length;
        const rect = itemRect(item, (i === first ? firstChar : 0) / size, (i === last ? lastChar + 1 : size) / size);
        if (rects.length && sameLine(rects.at(-1), rect)) rects[rects.length - 1] = union(rects.at(-1), rect);
        else rects.push(rect);
    }
    return rects;
}

// Index of the text item closest to a box: publishers write text in reading order, so
// this orders links correctly in two-column layouts where top-to-bottom would not.
export function nearestItem(items, rect) {
    const cx = (rect[0] + rect[2]) / 2;
    const cy = (rect[1] + rect[3]) / 2;
    let best = Infinity;
    let found = 0;
    items.forEach((item, i) => {
        if (typeof item.str !== "string" || !/\S/.test(item.str)) return;
        const [x0, y0, x1, y1] = itemRect(item, 0, 1);
        const distance = Math.hypot(Math.max(x0 - cx, 0, cx - x1), Math.max(y0 - cy, 0, cy - y1));
        if (distance < best) [best, found] = [distance, i];
    });
    return found;
}

// Publishers often split one citation into several adjacent links (author and year, or a
// wrap across lines). Consecutive links to the same entry that touch are one citation.
export function mergeLinks(links) {
    const merged = [];
    for (const link of links) {
        const prev = merged.at(-1);
        const last = prev?.rects.at(-1);
        const rect = link.rects[0];
        const height = rect[3] - rect[1];
        const touches = prev && prev.page === link.page && prev.dest === link.dest && (
            (sameLine(last, rect) && rect[0] - last[2] < height && rect[0] > last[0]) ||
            (rect[3] < last[1] + height * 0.5 && last[1] - rect[3] < height * 2.2 && rect[0] < last[0]));
        if (touches) prev.rects.push(rect);
        else merged.push({ ...link, rects: [...link.rects] });
    }
    return merged;
}

// The first click of a series goes to the first citation at or after the reading
// position (wrapping to the first); later clicks follow the list and wrap at the end.
export function nextCitationIndex(positions, last, here) {
    if (!positions.length) return -1;
    if (last != null) return (last + 1) % positions.length;
    const after = positions.findIndex((position) => position >= here);
    return after < 0 ? 0 : after;
}

const caches = new WeakMap();
function cacheFor(reader) {
    if (!caches.has(reader)) caches.set(reader, { text: new Map(), links: null, refs: new WeakMap() });
    return caches.get(reader);
}
function pageText(cache, pdf, number) {
    if (!cache.text.has(number))
        cache.text.set(number, pdf.getPage(number).then((page) => page.getTextContent()).then((content) => content.items));
    return cache.text.get(number);
}

// Link annotations by destination name, read once per paper.
function linkIndex(cache, pdf) {
    cache.links ||= (async () => {
        const byName = new Map();
        for (let number = 1; number <= pdf.numPages; number++) {
            const page = await pdf.getPage(number);
            for (const note of await page.getAnnotations()) {
                if (note.subtype !== "Link" || typeof note.dest !== "string") continue;
                const [x0, y0, x1, y1] = note.rect;
                const links = byName.get(note.dest) || [];
                links.push({ page: number, dest: note.dest, rects: [[Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)]] });
                byName.set(note.dest, links);
            }
        }
        return byName;
    })();
    return cache.links;
}

async function bibliographyPage(pdf, names) {
    const pages = [];
    for (const name of names) {
        try {
            const target = await pdf.getDestination(name);
            pages.push(await pdf.getPageIndex(target[0]) + 1);
        } catch { /* a broken destination just cannot bound the search */ }
    }
    return pages.length ? Math.min(...pages) : Infinity;
}

async function locateLinked(cache, pdf, ref) {
    if (!ref.destinations?.length) return [];
    const index = await linkIndex(cache, pdf);
    const bibliography = await bibliographyPage(pdf, ref.destinations);
    const links = ref.destinations.flatMap((name) => index.get(name) || []).filter((link) => link.page < bibliography);
    const keyed = [];
    for (const link of links) {
        const items = await pageText(cache, pdf, link.page);
        keyed.push({ ...link, key: nearestItem(items, link.rects[0]) });
    }
    keyed.sort((a, b) => a.page - b.page || a.key - b.key || b.rects[0][1] - a.rects[0][1] || a.rects[0][0] - b.rects[0][0]);
    return mergeLinks(keyed);
}

async function locateByText(cache, pdf, ref) {
    const pages = new Map();
    for (const occurrence of ref.occurrences || []) {
        if (occurrence.page < 1 || occurrence.page > pdf.numPages) continue;
        pages.set(occurrence.page, [...(pages.get(occurrence.page) || []), occurrence.marker]);
    }
    const citations = [];
    for (const [page, markers] of [...pages].sort((a, b) => a[0] - b[0])) {
        const items = await pageText(cache, pdf, page);
        const index = buildTextIndex(items);
        const found = findMarkers(index, markers).map((match) => ({
            page,
            rects: match ? matchRects(items, index, match) : null,
            key: match ? match.start : Infinity,
        }));
        citations.push(...found.sort((a, b) => a.key - b.key));
    }
    return citations;
}

// Citations in reading order: `rects` are PDF user-space boxes, or null when only the
// page is known.
export function locateCitations(reader, ref) {
    const cache = cacheFor(reader);
    if (!cache.refs.has(ref))
        cache.refs.set(ref, (async () => {
            const linked = await locateLinked(cache, reader.pdf, ref);
            return linked.length ? linked : locateByText(cache, reader.pdf, ref);
        })());
    return cache.refs.get(ref);
}

// Screen-space box (CSS pixels from the page's top-left) of a citation at the current zoom.
function viewBoxes(view, citation) {
    return (citation.rects || []).map((rect) => {
        const [x0, y0] = view.viewport.convertToViewportPoint(rect[0], rect[1]);
        const [x1, y1] = view.viewport.convertToViewportPoint(rect[2], rect[3]);
        return [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
    });
}

// Pages are numbered with a fraction for the offset inside them, so positions compare as numbers.
export function readingPosition(reader) {
    const page = reader.viewer.currentPageNumber;
    const div = reader.viewer.getPageView(page - 1)?.div;
    return div ? page + (reader.container.scrollTop - div.offsetTop) / div.offsetHeight : page;
}
export function citationPosition(reader, citation) {
    const view = reader.viewer.getPageView(citation.page - 1);
    const boxes = view?.viewport ? viewBoxes(view, citation) : [];
    return citation.page + (boxes.length ? boxes[0][1] / view.viewport.height : 0);
}

// Centres the citation and outlines it briefly. Returns false when only the page is known.
export function revealCitation(reader, citation) {
    const view = reader.viewer.getPageView(citation.page - 1);
    if (!view?.div) return false;
    const { container } = reader;
    const boxes = viewBoxes(view, citation);
    const page = view.div;
    const top = page.offsetTop + page.clientTop;
    reader.element.querySelectorAll(".citation-flash").forEach((flash) => flash.remove());
    if (!boxes.length) {
        container.scrollTo({ top: Math.max(0, top - 8) });
        return false;
    }
    const all = boxes.reduce(union);
    container.scrollTo({
        top: Math.max(0, top + (all[1] + all[3]) / 2 - container.clientHeight / 2),
        left: Math.max(0, page.offsetLeft + (all[0] + all[2]) / 2 - container.clientWidth / 2),
    });
    const { width, height } = view.viewport;
    for (const [x0, y0, x1, y1] of boxes) {
        const flash = document.createElement("div");
        flash.className = "citation-flash";
        // Percentages follow the page when the zoom changes during the flash.
        Object.assign(flash.style, {
            left: `${(x0 / width) * 100}%`, top: `${(y0 / height) * 100}%`,
            width: `${((x1 - x0) / width) * 100}%`, height: `${((y1 - y0) / height) * 100}%`,
        });
        flash.setAttribute("aria-hidden", "true");
        flash.onanimationend = () => flash.remove();
        page.append(flash);
        // Without animations (reduced motion, hidden tab) the outline would otherwise stay.
        setTimeout(() => flash.remove(), 2200);
    }
    return true;
}
