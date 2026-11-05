import assert from "node:assert/strict";
import test from "node:test";
import { ZOOM_CHOICES, parseZoom } from "../src/paperassist/web/preferences.js";

test("a stored interface size is accepted only when it is a listed choice", () => {
    for (const value of ZOOM_CHOICES) assert.equal(parseZoom(String(value)), value);
    assert.equal(parseZoom("1.25"), 1.25);
});

test("missing, malformed and unlisted sizes fall back to 100%", () => {
    for (const stored of [null, undefined, "", "abc", "0", "-1", "1.3", "3", "NaN", "Infinity"])
        assert.equal(parseZoom(stored), 1, String(stored));
});
