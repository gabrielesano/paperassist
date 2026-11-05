import assert from "node:assert/strict";
import test from "node:test";
import { installDesktopBridge } from "../src/paperassist/web/desktop.js";

test("close handler is available synchronously without a native RPC", async () => {
    const window = { get pywebview() { assert.fail("No native RPC needed"); } };
    const bridge = installDesktopBridge({ window, prepareClose: async () => true });
    assert.equal(bridge.allowsUnload(), false);
    assert.equal(await window.paperassistDesktop.requestClose(), true);
    assert.equal(bridge.allowsUnload(), true);
});

test("concurrent close requests share one decision and cancellation allows retry", async () => {
    let finish, preparations = 0;
    const window = {};
    const bridge = installDesktopBridge({ window, prepareClose: () => {
        preparations++;
        return new Promise((resolve) => { finish = resolve; });
    } });
    const result = window.paperassistDesktop.requestClose();
    assert.equal(window.paperassistDesktop.requestClose(), result);
    await Promise.resolve();
    assert.equal(preparations, 1);
    finish(false);
    assert.equal(await result, false);
    assert.equal(bridge.allowsUnload(), false);
    const retry = window.paperassistDesktop.requestClose();
    await Promise.resolve();
    assert.equal(preparations, 2);
    finish(true);
    assert.equal(await retry, true);
    assert.equal(bridge.allowsUnload(), true);
});

test("close preparation errors report and keep unload protection", async () => {
    const window = {}, errors = [];
    const bridge = installDesktopBridge({
        window,
        prepareClose: async () => { throw new Error("Save failed"); },
        onError: (error) => errors.push(error.message),
    });
    assert.equal(await window.paperassistDesktop.requestClose(), false);
    assert.equal(bridge.allowsUnload(), false);
    assert.deepEqual(errors, ["Save failed"]);
});
