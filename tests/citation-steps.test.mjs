import assert from "node:assert/strict";
import test from "node:test";
import {
    buildTextIndex,
    findMarkers,
    matchRects,
    mergeLinks,
    nearestItem,
    nextCitationIndex,
} from "../src/paperassist/web/citation-steps.js";

// A text item as PDF.js reports it: 10-point upright text starting at (x, y).
const item = (str, x, y, width = str.length * 5) => ({ str, transform: [10, 0, 0, 10, x, y], width, height: 10 });

test("markers match across spaces, line breaks and dash styles", () => {
    const index = buildTextIndex([item("see Bingert &", 0, 100), item("Peter 2011;", 0, 88), item("[3, 5–7]", 0, 76)]);
    const [first, second] = findMarkers(index, ["Bingert &\nPeter 2011", "[3, 5-7]"]);
    assert.ok(first && second);
    assert.equal(index.text.slice(first.start, first.end), "bingert&peter2011");
    assert.equal(index.text.slice(second.start, second.end), "[3,5-7]");
});

test("ligatures and soft hyphens do not break a match", () => {
    const index = buildTextIndex([item("Schmitt et al. 2o­12", 0, 0), item("ﬁrst Lu 1991", 0, 0)]);
    assert.ok(findMarkers(index, ["Lu 1991"])[0]);
    assert.equal(findMarkers(index, ["Schmitt et al. 2o12"])[0].start, 0);
    assert.ok(findMarkers(index, ["first"])[0]);
});

test("repeated markers on a page are matched in order", () => {
    const index = buildTextIndex([item("Hood 2009 and Hood 2009 then Hood 2009", 0, 0)]);
    const found = findMarkers(index, ["Hood 2009", "Hood 2009", "Hood 2009", "Hood 2009"]);
    const starts = found.map((match) => match?.start);
    assert.equal(starts[0] < starts[1] && starts[1] < starts[2], true);
    assert.equal(starts[3], undefined, "a fourth occurrence that is not in the text stays unmatched");
});

test("a marker missing from the page gives null", () => {
    const index = buildTextIndex([item("nothing here", 0, 0)]);
    assert.deepEqual(findMarkers(index, ["Hood 2009", ""]), [null, null]);
});

test("a match inside one item gets a box for just those characters", () => {
    const items = [item("abc Hood 2009 xyz", 100, 200)];
    const index = buildTextIndex(items);
    const match = findMarkers(index, ["Hood 2009"])[0];
    const [rect, ...more] = matchRects(items, index, match);
    assert.equal(more.length, 0);
    const size = items[0].str.length;
    assert.ok(Math.abs(rect[0] - (100 + (4 / size) * items[0].width)) < 1e-6);
    assert.ok(Math.abs(rect[2] - (100 + (13 / size) * items[0].width)) < 1e-6);
    assert.deepEqual([rect[1], rect[3]], [200, 210]);
});

test("a match over two lines gives one box per line", () => {
    const items = [item("as shown by Bingert &", 50, 300), item("Peter 2011 earlier", 50, 288)];
    const index = buildTextIndex(items);
    const rects = matchRects(items, index, findMarkers(index, ["Bingert & Peter 2011"])[0]);
    assert.equal(rects.length, 2);
    assert.ok(rects[0][1] > rects[1][1]);
});

test("pieces of one line merge into a single box", () => {
    const items = [item("Hood et", 0, 50), item(" al. ", 35, 50), item("2009", 60, 50)];
    const index = buildTextIndex(items);
    const rects = matchRects(items, index, findMarkers(index, ["Hood et al. 2009"])[0]);
    assert.equal(rects.length, 1);
});

test("rotated text keeps its box along the baseline", () => {
    const rotated = { str: "Hood 2009", transform: [0, 10, -10, 0, 100, 100], width: 45, height: 10 };
    const index = buildTextIndex([rotated]);
    const [rect] = matchRects([rotated], index, findMarkers(index, ["Hood 2009"])[0]);
    assert.deepEqual(rect, [90, 100, 100, 145]);
});

test("a link is ordered by the nearest text item, not by geometry", () => {
    const items = [item("left column", 50, 500), item("right column", 320, 700)];
    assert.equal(nearestItem(items, [330, 702, 360, 710]), 1);
    assert.equal(nearestItem(items, [60, 502, 90, 510]), 0);
});

test("adjacent links of one entry are one citation, separate ones are not", () => {
    const link = (rect, dest = "a", page = 1) => ({ page, dest, rects: [rect] });
    const split = mergeLinks([link([10, 100, 60, 110]), link([63, 100, 90, 110])]);
    assert.equal(split.length, 1);
    assert.equal(split[0].rects.length, 2);
    const wrapped = mergeLinks([link([200, 100, 300, 110]), link([50, 88, 80, 98])]);
    assert.equal(wrapped.length, 1);
    const apart = mergeLinks([link([10, 100, 60, 110]), link([200, 100, 230, 110])]);
    assert.equal(apart.length, 2);
    assert.equal(mergeLinks([link([10, 100, 60, 110]), link([63, 100, 90, 110], "b")]).length, 2);
    assert.equal(mergeLinks([link([10, 100, 60, 110]), link([63, 100, 90, 110], "a", 2)]).length, 2);
});

test("a series starts at the reading position, then follows the list and wraps", () => {
    const positions = [1.2, 2.5, 2.9, 7.1];
    assert.equal(nextCitationIndex(positions, undefined, 1), 0);
    assert.equal(nextCitationIndex(positions, undefined, 2.5), 1);
    assert.equal(nextCitationIndex(positions, undefined, 2.6), 2);
    assert.equal(nextCitationIndex(positions, undefined, 8), 0, "past the last citation wraps to the first");
    assert.equal(nextCitationIndex(positions, 1, 7), 2, "later clicks ignore the reading position");
    assert.equal(nextCitationIndex(positions, 3, 1), 0);
    assert.equal(nextCitationIndex([], undefined, 1), -1);
});
