import assert from "node:assert/strict";
import test from "node:test";
import { shortcutFor, shortcuts } from "../src/paperassist/web/shortcuts.js";

const press = (key, extra = {}) => ({ key, target: {}, ...extra });
const label = (event, context) => shortcutFor(event, context)?.label;

test("plain letters open their action", () => {
    assert.equal(label(press("n")), "Next page");
    assert.equal(label(press("K")), "Previous page");
    assert.equal(label(press("?", { shiftKey: true })), "Show keyboard shortcuts");
});

test("plain letters are ignored in fields, dialogs and with modifiers", () => {
    assert.equal(shortcutFor(press("n"), { typing: true }), null);
    assert.equal(shortcutFor(press("n"), { dialogOpen: true }), null);
    assert.equal(shortcutFor(press("n", { isComposing: true })), null);
    assert.equal(shortcutFor(press("n", { defaultPrevented: true })), null);
    for (const modifier of ["ctrlKey", "metaKey", "altKey"])
        assert.equal(shortcutFor(press("n", { [modifier]: true })), null);
});

test("Ctrl and Command shortcuts keep working while typing", () => {
    assert.equal(label(press("f", { ctrlKey: true }), { typing: true }), "Find in the document");
    assert.equal(label(press("=", { metaKey: true }), { typing: true }), "Zoom in");
    assert.equal(label(press("0", { ctrlKey: true })), "Fit the page width");
});

test("Undo is left to text fields, and redo accepts both layouts", () => {
    assert.equal(shortcutFor(press("z", { ctrlKey: true }), { typing: true }), null);
    assert.equal(label(press("z", { ctrlKey: true })), "Undo");
    assert.equal(label(press("Z", { ctrlKey: true, shiftKey: true })), "Redo");
    assert.equal(label(press("y", { ctrlKey: true })), "Redo");
});

test("Escape only leaves toolbar fields", () => {
    const inToolbar = { closest: (selector) => selector === ".toolbar input" };
    assert.ok(shortcutFor(press("Escape", { target: inToolbar }), { typing: true }));
    assert.equal(shortcutFor(press("Escape"), { typing: true }), null);
});

test("each active key combination has one meaning", () => {
    const seen = new Map();
    for (const entry of shortcuts.filter((item) => item.keys && item.match)) {
        for (const combo of entry.keys) {
            const id = combo.join("+");
            assert.equal(seen.get(id), undefined, `${id} is defined twice`);
            seen.set(id, entry.label);
        }
    }
});

test("every listed entry names its group, keys and description", () => {
    for (const entry of shortcuts.filter((item) => item.label)) {
        assert.ok(entry.group && entry.keys.length, entry.label);
        assert.ok(entry.keys.every((combo) => combo.length), entry.label);
    }
});
