import test from 'node:test';
import assert from 'node:assert/strict';
import {
    clientRectToPdf, pdfRectToViewport, revealCommentAnchor, clearCommentAnchor,
    setCommentIndicators, captureCommentAnchor,
} from '../src/paperassist/web/comments.js';

async function realPage() {
    const { getDocument } = await import('pdfjs-dist/legacy/build/pdf.mjs');
    const objects = [
        '<< /Type /Catalog /Pages 2 0 R >>',
        '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 400] >>',
    ];
    let pdf = '%PDF-1.4\n';
    const offsets = [0];
    objects.forEach((object, index) => {
        offsets.push(pdf.length);
        pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
    });
    const xref = pdf.length;
    pdf += `xref\n0 ${offsets.length}\n0000000000 65535 f \n`;
    for (const offset of offsets.slice(1)) {
        pdf += `${String(offset).padStart(10, '0')} 00000 n \n`;
    }
    pdf += `trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
    const document = await getDocument({ data: new TextEncoder().encode(pdf) }).promise;
    return document.getPage(1);
}

test('selection geometry survives zoom and page rotation with PDF.js viewports', async () => {
    const page = await realPage();
    const original = page.getViewport({ scale: 2 });
    const pdf = clientRectToPdf({ left: 30, top: 60, right: 110, bottom: 80 },
        { left: 10, top: 20, width: 400, height: 800 }, original);
    assert.deepEqual(pdf, [10, 370, 50, 380]);
    assert.deepEqual(pdfRectToViewport(pdf, page.getViewport({ scale: 1 })), [10, 20, 40, 10]);
    assert.deepEqual(
        pdfRectToViewport(pdf, page.getViewport({ scale: 1, rotation: 90 })),
        [370, 10, 10, 40],
    );
});

test('legacy page notes navigate without geometry and cleanup removes listeners', () => {
    const listeners = new Map();
    let destination;
    const reader = {
        container: { querySelectorAll: () => [] },
        bus: {
            on: (name, fn) => listeners.set(name, fn),
            off: name => listeners.delete(name),
        },
        viewer: { scrollPageIntoView: value => { destination = value; } },
    };
    globalThis.requestAnimationFrame = () => {};
    revealCommentAnchor(reader, { page: 7 });
    assert.equal(destination.pageNumber, 7);
    assert.equal(destination.destArray, undefined);
    assert.equal(listeners.size, 3);
    clearCommentAnchor(reader);
    assert.equal(listeners.size, 0);
});


test('saved outlines redraw and delete without affecting focus markers', () => {
    const listeners = new Map();
    const overlays = [];
    const page = {
        append(element) { overlays.push(element); },
    };
    const reader = {
        container: {
            querySelectorAll(selector) {
                return overlays.filter(element => `.${element.className}` === selector);
            },
        },
        viewer: {
            getPageView: () => ({
                div: page,
                viewport: { convertToViewportPoint: (x, y) => [x, y] },
            }),
        },
        bus: {
            on: (name, callback) => listeners.set(name, callback),
        },
    };
    globalThis.document = {
        createElement() {
            return {
                style: {}, children: [],
                append(child) { this.children.push(child); },
                remove() { overlays.splice(overlays.indexOf(this), 1); },
            };
        },
    };
    globalThis.requestAnimationFrame = callback => callback();
    const anchor = { version: 1, pages: [{ page: 1, rects: [[10, 20, 30, 40]] }] };
    setCommentIndicators(reader, [{ anchor }, { page: 1 }]);
    assert.equal(overlays.length, 1);
    assert.equal(overlays[0].children.length, 1);
    assert.equal(overlays[0].style.pointerEvents, 'none');
    assert.equal(overlays[0].children[0].style.outline, '1.5px dashed #d42020');
    listeners.get('scalechanging')();
    assert.equal(overlays.length, 1);
    setCommentIndicators(reader, []);
    assert.equal(overlays.length, 0);
});

// Mimics what browsers return for a multi-line range: word fragments plus a duplicate
// element rect per line, in screen pixels of a 200x400 page at scale 1.
const box = (left, top, right, bottom) => ({
    left, top, right, bottom, width: right - left, height: bottom - top,
});
const fragments = [
    box(60, 50, 100, 60), box(104, 50, 150, 60), box(60, 49, 150, 61), // line 1, starts mid-line
    box(10, 62, 90, 72), box(94, 62, 190, 72), box(10, 62, 190, 72), // line 2
    box(10, 74, 40, 84), box(44, 74, 120, 84), // line 3, ends mid-line
];

function captureWith(rects, viewport) {
    const layer = {
        getBoundingClientRect: () => box(0, 0, viewport.width, viewport.height),
        closest: () => layer,
    };
    const node = { nodeType: 1, closest: () => layer };
    const range = { getClientRects: () => rects, intersectsNode: () => true };
    globalThis.window = {
        getSelection: () => ({
            isCollapsed: false, rangeCount: 1, anchorNode: node, focusNode: node,
            toString: () => 'quote', getRangeAt: () => range,
        }),
    };
    const reader = {
        container: {
            contains: () => true,
            querySelectorAll: () => [{ dataset: { pageNumber: '1' }, querySelector: () => layer }],
        },
        viewer: { getPageView: () => ({ viewport }) },
    };
    return captureCommentAnchor(reader).anchor.pages[0].rects;
}

test('captured selection keeps one band per line from start to end', async () => {
    const viewport = (await realPage()).getViewport({ scale: 1 });
    const bands = captureWith(fragments, viewport)
        .map(rect => pdfRectToViewport(rect, viewport).map(Math.round))
        .sort((a, b) => a[1] - b[1]);
    assert.deepEqual(bands, [[60, 49, 90, 12], [10, 62, 180, 10], [10, 74, 110, 10]]);
});

test('captured selection never bridges the gutter between columns', async () => {
    const viewport = (await realPage()).getViewport({ scale: 1 });
    const bands = captureWith([
        box(10, 50, 60, 60), box(64, 50, 90, 60), box(140, 50, 190, 60),
    ], viewport).map(rect => pdfRectToViewport(rect, viewport).map(Math.round))
        .sort((a, b) => a[0] - b[0]);
    assert.deepEqual(bands, [[10, 50, 80, 10], [140, 50, 50, 10]]);
});

test('captured selection merges along the vertical axis on rotated pages', async () => {
    const viewport = (await realPage()).getViewport({ scale: 1, rotation: 90 });
    const bands = captureWith([
        box(50, 10, 60, 40), box(50, 44, 60, 90), box(50, 10, 60, 90),
        box(62, 10, 72, 90),
    ], viewport).map(rect => pdfRectToViewport(rect, viewport).map(Math.round))
        .sort((a, b) => a[0] - b[0]);
    assert.deepEqual(bands, [[50, 10, 10, 80], [62, 10, 10, 80]]);
});

test('legacy fragmented anchors are drawn as clean per-line bands', async () => {
    const viewport = (await realPage()).getViewport({ scale: 1 });
    const overlays = [];
    const reader = {
        container: {
            querySelectorAll: selector => overlays.filter(e => `.${e.className}` === selector),
        },
        viewer: { getPageView: () => ({ div: { append: e => overlays.push(e) }, viewport }) },
        bus: { on() {} },
    };
    globalThis.document = {
        createElement: () => ({
            style: {}, children: [],
            append(child) { this.children.push(child); },
            remove() { overlays.splice(overlays.indexOf(this), 1); },
        }),
    };
    globalThis.requestAnimationFrame = callback => callback();
    const legacy = fragments.map(rect => {
        const [x1, y1, x2, y2] = [rect.left, rect.top, rect.right, rect.bottom];
        return [x1, 400 - y2, x2, 400 - y1];
    });
    setCommentIndicators(reader, [{ anchor: { version: 1, pages: [{ page: 1, rects: legacy }] } }]);
    const marks = overlays[0].children.map(mark => mark.style)
        .map(({ left, top, width, height }) => [left, top, width, height].map(parseFloat))
        .sort((a, b) => a[1] - b[1]).map(mark => mark.map(Math.round));
    assert.deepEqual(marks, [[60, 49, 90, 12], [10, 62, 180, 10], [10, 74, 110, 10]]);
    assert.equal(overlays[0].children[0].style.outline, '1.5px dashed #d42020');
});
