import { strict as assert } from "node:assert";
import { test } from "node:test";
import { mergeHighlightLines } from "../src/paperassist/web/highlight-lines.js";

test("joins words on a line without joining separate lines", () => {
    const boxes = [
        { x: 0.1, y: 0.2, width: 0.1, height: 0.02 },
        { x: 0.23, y: 0.201, width: 0.12, height: 0.02 },
        { x: 0.1, y: 0.24, width: 0.08, height: 0.02 },
    ];
    const lines = mergeHighlightLines(boxes);
    assert.equal(lines.length, 2);
    assert.equal(lines[0].x, 0.1);
    assert.ok(Math.abs(lines[0].width - 0.25) < 1e-10);
    assert.equal(lines[1].width, 0.08);
});

test("joins words along the rotated line axis", () => {
    const lines = mergeHighlightLines(
        [
            { x: 0.3, y: 0.1, width: 0.02, height: 0.1 },
            { x: 0.3, y: 0.23, width: 0.02, height: 0.1 },
            { x: 0.34, y: 0.1, width: 0.02, height: 0.1 },
        ],
        true,
    );
    assert.equal(lines.length, 2);
    assert.ok(Math.abs(lines[0].height - 0.23) < 1e-10);
});

test("does not bridge a wide gutter between columns", () => {
    const lines = mergeHighlightLines([
        { x: 0.1, y: 0.2, width: 0.1, height: 0.02 },
        { x: 0.6, y: 0.2, width: 0.1, height: 0.02 },
    ]);
    assert.equal(lines.length, 2);
});
