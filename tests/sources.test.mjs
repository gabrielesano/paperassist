import test from "node:test";
import assert from "node:assert/strict";
import { proxyUrl } from "../src/paperassist/web/sources.js";

const doi = "https://doi.org/10.1000/abc";

test("a template with {url} receives the encoded target", () => {
    assert.equal(
        proxyUrl("https://proxy.example.edu/login?url={url}&x=1", doi),
        "https://proxy.example.edu/login?url=https%3A%2F%2Fdoi.org%2F10.1000%2Fabc&x=1",
    );
});

test("a prefix without {url} gets the raw target appended", () => {
    assert.equal(
        proxyUrl("https://login.proxy.example.edu/login?url=", doi),
        `https://login.proxy.example.edu/login?url=${doi}`,
    );
});

test("only web schemes become links", () => {
    assert.equal(proxyUrl("", doi), null);
    assert.equal(proxyUrl("javascript:alert(1)//", doi), null);
    assert.equal(proxyUrl("https://proxy.example.edu/?url=", "javascript:alert(1)"), null);
    assert.equal(proxyUrl("https://proxy.example.edu/?url=", "not a url"), null);
});
