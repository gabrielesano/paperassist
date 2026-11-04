import test from "node:test";
import assert from "node:assert/strict";
import { setupSplitResize, splitSize } from "../src/paperassist/web/split-resize.js";

class Handle extends EventTarget {
    attributes = {};
    captured = null;
    setAttribute(name, value) { this.attributes[name] = value; }
    setPointerCapture(id) { this.captured = id; }
    hasPointerCapture(id) { return this.captured === id; }
    releasePointerCapture() { this.captured = null; }
}
function send(target, type, values = {}) {
    const event = new Event(type, { cancelable: true });
    Object.assign(event, values);
    target.dispatchEvent(event);
    return event;
}

test("split resizing clamps pointer movement and supports keyboard controls", () => {
    const handle = new Handle();
    const pane = {
        offsetHeight: 400,
        parentElement: { offsetHeight: 1000, getBoundingClientRect: () => ({ bottom: 1000, height: 1000 }) },
        style: {},
    };
    const controller = new AbortController();
    setupSplitResize(handle, pane, controller.signal);
    assert.equal(splitSize(100, 1000), 20);
    assert.equal(splitSize(900, 1000), 75);
    assert.equal(send(handle, "pointerdown", { button: 0, pointerId: 7 }).defaultPrevented, true);
    send(handle, "pointermove", { pointerId: 7, clientY: 100 });
    assert.equal(pane.style.flexBasis, "75%");
    send(handle, "pointermove", { pointerId: 7, clientY: 990 });
    assert.equal(pane.style.flexBasis, "20%");
    send(handle, "pointerup", { pointerId: 7 });
    assert.equal(handle.captured, null);
    send(handle, "keydown", { key: "ArrowUp" });
    assert.equal(pane.style.flexBasis, "45%");
    send(handle, "keydown", { key: "Home" });
    assert.equal(handle.attributes["aria-valuenow"], "20");
    send(handle, "keydown", { key: "End" });
    assert.equal(pane.style.flexBasis, "75%");
    send(handle, "pointerdown", { button: 0, pointerId: 8 });
    controller.abort();
    assert.equal(handle.captured, null);
    send(handle, "pointermove", { pointerId: 8, clientY: 500 });
    assert.equal(pane.style.flexBasis, "75%");
});

test("a vertical separator resizes by width and each orientation keeps its own size", () => {
    const handle = new Handle();
    const pane = {
        offsetWidth: 500,
        offsetHeight: 400,
        parentElement: {
            offsetWidth: 1000,
            offsetHeight: 1000,
            getBoundingClientRect: () => ({ right: 1200, width: 1000, bottom: 1000, height: 1000 }),
        },
        style: {},
    };
    const controller = new AbortController();
    const setOrientation = setupSplitResize(handle, pane, controller.signal);
    send(handle, "keydown", { key: "ArrowUp" });
    assert.equal(pane.style.flexBasis, "45%");
    setOrientation("vertical");
    assert.equal(handle.attributes["aria-orientation"], "vertical");
    assert.equal(pane.style.flexBasis, "50%");
    assert.equal(handle.attributes["aria-valuenow"], "50");
    send(handle, "pointerdown", { button: 0, pointerId: 3 });
    send(handle, "pointermove", { pointerId: 3, clientX: 1000, clientY: 0 });
    assert.equal(pane.style.flexBasis, "20%");
    send(handle, "pointermove", { pointerId: 3, clientX: 100, clientY: 0 });
    assert.equal(pane.style.flexBasis, "75%");
    send(handle, "pointerup", { pointerId: 3 });
    send(handle, "keydown", { key: "ArrowRight" });
    assert.equal(pane.style.flexBasis, "45%");
    send(handle, "keydown", { key: "ArrowLeft" });
    assert.equal(pane.style.flexBasis, "55%");
    send(handle, "keydown", { key: "Home" });
    assert.equal(pane.style.flexBasis, "20%");
    setOrientation("vertical");
    assert.equal(pane.style.flexBasis, "20%");
    setOrientation("horizontal");
    assert.equal(handle.attributes["aria-orientation"], "horizontal");
    assert.equal(pane.style.flexBasis, "45%");
    controller.abort();
});
