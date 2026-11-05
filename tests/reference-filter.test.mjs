import assert from "node:assert/strict";
import test from "node:test";
import { referenceMatcher } from "../src/paperassist/web/reference-filter.js";

const ref = {
    label: "Antolín 2012", authors: "Antolín, J. C.; Müller, K.", year: 2012,
    doi: "10.1000/ABC.123", title: "Dispersal of seeds", raw: "Antolín J. C. (2012). Dispersal of seeds.",
};

test("an empty query matches everything", () => {
    assert.ok(referenceMatcher("")(ref));
    assert.ok(referenceMatcher("   ")({}));
});

test("matching ignores case and accents in both directions", () => {
    assert.ok(referenceMatcher("antolin")(ref));
    assert.ok(referenceMatcher("ANTOLÍN")(ref));
    assert.ok(referenceMatcher("muller")(ref));
    assert.ok(referenceMatcher("müller")({ authors: "Muller" }));
});

test("every word must match, in any order", () => {
    assert.ok(referenceMatcher("antolin 2012")(ref));
    assert.ok(referenceMatcher("2012 seeds antolin")(ref));
    assert.ok(!referenceMatcher("antolin 2013")(ref));
});

test("year, DOI, title and raw text are searched", () => {
    assert.ok(referenceMatcher("10.1000/abc")(ref));
    assert.ok(referenceMatcher("dispersal")({ title: "Dispersal of seeds" }));
    assert.ok(referenceMatcher("dispersal")({ raw: "J. Ecol. Dispersal of seeds" }));
    assert.ok(!referenceMatcher("dispersal")({ label: "Smith 2001" }));
});
