import { mergeHighlightLines } from './highlight-lines.js';

const focused = new WeakMap();

export function clientRectToPdf(rect, pageRect, viewport) {
    const sx = viewport.width / pageRect.width;
    const sy = viewport.height / pageRect.height;
    const a = viewport.convertToPdfPoint(
        (rect.left - pageRect.left) * sx, (rect.top - pageRect.top) * sy,
    );
    const b = viewport.convertToPdfPoint(
        (rect.right - pageRect.left) * sx, (rect.bottom - pageRect.top) * sy,
    );
    return [
        Math.min(a[0], b[0]), Math.min(a[1], b[1]),
        Math.max(a[0], b[0]), Math.max(a[1], b[1]),
    ];
}

export function pdfRectToViewport(rect, viewport) {
    const [x1, y1] = viewport.convertToViewportPoint(rect[0], rect[1]);
    const [x2, y2] = viewport.convertToViewportPoint(rect[2], rect[3]);
    return [
        Math.min(x1, x2), Math.min(y1, y2),
        Math.abs(x2 - x1), Math.abs(y2 - y1),
    ];
}

// One band per text line: drop rects that PDF.js text-node and element rects duplicate,
// then join the word boxes of each baseline. Boxes are {x, y, width, height} on screen.
function lineBands(boxes, viewport) {
    const inside = (a, b) => a.x >= b.x - 0.5 && a.y >= b.y - 0.5 &&
        a.x + a.width <= b.x + b.width + 0.5 && a.y + a.height <= b.y + b.height + 0.5;
    const distinct = boxes.filter((box, i) => !boxes.some((other, j) =>
        j !== i && inside(box, other) && (!inside(other, box) || j < i)));
    return mergeHighlightLines(distinct, Math.abs(viewport.rotation || 0) % 180 === 90);
}

function pdfLineBands(rects, viewport) {
    const boxes = rects
        .filter(rect => Array.isArray(rect) && rect.length === 4 && rect.every(Number.isFinite))
        .map(rect => {
            const [x, y, width, height] = pdfRectToViewport(rect, viewport);
            return { x, y, width, height };
        });
    return lineBands(boxes, viewport);
}

function appendBands(overlay, rects, viewport, outline) {
    for (const { x, y, width, height } of pdfLineBands(rects, viewport)) {
        const mark = document.createElement('div');
        Object.assign(mark.style, {
            position: 'absolute', left: `${x}px`, top: `${y}px`,
            width: `${width}px`, height: `${height}px`, outline,
        });
        overlay.append(mark);
    }
}

function textLayer(node, reader) {
    const element = node?.nodeType === 1 ? node : node?.parentElement;
    const layer = element?.closest('.textLayer');
    return layer && reader.container.contains(layer) ? layer : null;
}

export function captureCommentAnchor(reader) {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount !== 1 ||
        !textLayer(selection.anchorNode, reader) || !textLayer(selection.focusNode, reader)) {
        return null;
    }
    const quote = selection.toString().trim();
    if (!quote) return null;
    const range = selection.getRangeAt(0);
    const rects = [...range.getClientRects()]
        .filter(rect => rect.width > 0 && rect.height > 0);
    const pages = [];
    for (const page of reader.container.querySelectorAll('.page')) {
        const layer = page.querySelector('.textLayer');
        const number = Number(page.dataset.pageNumber);
        const view = reader.viewer.getPageView(number - 1);
        if (!layer || !view?.viewport || !range.intersectsNode(layer)) continue;
        const bounds = layer.getBoundingClientRect();
        if (!bounds.width || !bounds.height) continue;
        const clipped = rects.map(rect => ({
            left: Math.max(rect.left, bounds.left),
            top: Math.max(rect.top, bounds.top),
            right: Math.min(rect.right, bounds.right),
            bottom: Math.min(rect.bottom, bounds.bottom),
        })).filter(rect => rect.right > rect.left && rect.bottom > rect.top)
            .map(rect => ({
                x: rect.left, y: rect.top,
                width: rect.right - rect.left, height: rect.bottom - rect.top,
            }));
        const selected = lineBands(clipped, view.viewport).map(box => clientRectToPdf({
            left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height,
        }, bounds, view.viewport));
        if (selected.length) pages.push({ page: number, rects: selected });
    }
    return pages.length ? { quote, page: pages[0].page, anchor: { version: 1, pages } } : null;
}

function draw(reader, note) {
    reader.container.querySelectorAll('.comment-anchor-focus')
        .forEach(element => element.remove());
    const color = /^#[0-9a-f]{6}$/i.test(note.color || '') ? note.color : '#aa700f';
    for (const item of note.anchor?.version === 1 ? note.anchor.pages || [] : []) {
        const view = reader.viewer.getPageView(item.page - 1);
        if (!view?.div || !view.viewport) continue;
        const overlay = document.createElement('div');
        overlay.className = 'comment-anchor-focus';
        Object.assign(overlay.style, {
            position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '5',
        });
        appendBands(overlay, item.rects || [], view.viewport, `1px solid ${color}`);
        view.div.append(overlay);
    }
}

export function clearCommentAnchor(reader) {
    const state = focused.get(reader);
    if (state) {
        reader.bus.off('pagerendered', state.refresh);
        reader.bus.off('scalechanging', state.refresh);
        reader.bus.off('rotationchanging', state.refresh);
        focused.delete(reader);
    }
    reader.container.querySelectorAll('.comment-anchor-focus')
        .forEach(element => element.remove());
}

export function revealCommentAnchor(reader, note, scroll = true) {
    clearCommentAnchor(reader);
    const page = note.anchor?.pages?.[0]?.page || note.page;
    if (!Number.isInteger(page) || page < 1) return;
    const refresh = () => requestAnimationFrame(() => {
        if (focused.get(reader)?.note === note) draw(reader, note);
    });
    focused.set(reader, { note, refresh });
    reader.bus.on('pagerendered', refresh);
    reader.bus.on('scalechanging', refresh);
    reader.bus.on('rotationchanging', refresh);
    const rect = note.anchor?.pages?.[0]?.rects?.[0];
    const destArray = Array.isArray(rect) && rect.length === 4 && rect.every(Number.isFinite)
        ? [null, { name: 'XYZ' }, rect[0], rect[3], null] : undefined;
    if (scroll) {
        reader.viewer.scrollPageIntoView({
            pageNumber: page, destArray, center: 'vertical', allowNegativeOffset: true,
        });
    }
    refresh();
}

const indicators = new WeakMap();

function drawIndicators(reader, notes) {
    reader.container.querySelectorAll('.comment-anchor-indicators')
        .forEach(element => element.remove());
    const overlays = new Map();
    for (const note of notes) {
        if (note.anchor?.version !== 1 || !Array.isArray(note.anchor.pages)) continue;
        for (const item of note.anchor.pages) {
            const view = reader.viewer.getPageView(item.page - 1);
            if (!view?.div || !view.viewport) continue;
            if (!overlays.has(item.page)) {
                const overlay = document.createElement('div');
                overlay.className = 'comment-anchor-indicators';
                Object.assign(overlay.style, {
                    position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '5',
                });
                overlays.set(item.page, overlay);
                view.div.append(overlay);
            }
            // Merge per note so two comments on neighboring lines never fuse.
            appendBands(overlays.get(item.page), item.rects || [], view.viewport,
                '1.5px dashed #d42020');
        }
    }
}

// Hit-test in viewport pixels against the same line bands the indicators draw, so the
// clickable area always matches the red outline at any zoom or rotation. The overlay stays
// pointer-transparent: capturing events would break dragging to select commented text.
export function commentAt(notes, page, viewport, [x, y]) {
    let best = null;
    notes.forEach(note => {
        if (note.anchor?.version !== 1 || !Array.isArray(note.anchor.pages)) return;
        for (const item of note.anchor.pages) {
            if (item.page !== page) continue;
            for (const band of pdfLineBands(item.rects || [], viewport)) {
                if (x < band.x || x > band.x + band.width ||
                    y < band.y || y > band.y + band.height) continue;
                // The smallest band is the most specific phrase; later notes win ties.
                const area = band.width * band.height;
                if (!best || area <= best.area) best = { note, area };
            }
        }
    });
    return best?.note ?? null;
}

export function commentAtClient(reader, event) {
    const element = event.target?.closest?.('.page');
    const state = indicators.get(reader);
    const view = element && reader.viewer.getPageView(Number(element.dataset.pageNumber) - 1);
    if (!state || !view?.viewport || view.div !== element) return null;
    // The overlay is positioned inside the page border, so measure from the padding box.
    const box = element.getBoundingClientRect();
    const sx = view.viewport.width / element.clientWidth;
    const sy = view.viewport.height / element.clientHeight;
    return commentAt(state.notes, Number(element.dataset.pageNumber), view.viewport, [
        (event.clientX - box.left - element.clientLeft) * sx,
        (event.clientY - box.top - element.clientTop) * sy,
    ]);
}

export function setCommentIndicators(reader, notes) {
    let state = indicators.get(reader);
    if (!state) {
        state = { notes: [], refresh: null };
        state.refresh = () => requestAnimationFrame(() => {
            if (indicators.get(reader) === state) drawIndicators(reader, state.notes);
        });
        indicators.set(reader, state);
        reader.bus.on('pagerendered', state.refresh);
        reader.bus.on('scalechanging', state.refresh);
        reader.bus.on('rotationchanging', state.refresh);
    }
    state.notes = notes;
    state.refresh();
}

export function clearCommentIndicators(reader) {
    const state = indicators.get(reader);
    if (!state) return;
    reader.bus.off('pagerendered', state.refresh);
    reader.bus.off('scalechanging', state.refresh);
    reader.bus.off('rotationchanging', state.refresh);
    indicators.delete(reader);
    reader.container.querySelectorAll('.comment-anchor-indicators')
        .forEach(element => element.remove());
}
