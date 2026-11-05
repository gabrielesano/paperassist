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

const { loadInterfacePreferences, saveInterfacePreferences } = await import('../src/paperassist/web/preferences.js');
function storage(values) {
    const entries = new Map(Object.entries(values));
    return { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) };
}

test('legacy interface values migrate only into missing backend fields', async () => {
    globalThis.localStorage = storage({ 'paperassist-ui-zoom': '1.25', 'paperassist-quick-view-layout': 'side',
        'paperassist-outline-expanded': 'false', 'paperassist-details-expanded': 'true' });
    const requests = [];
    const saved = await loadInterfacePreferences(async (path, options) => {
        requests.push([path, options]);
        if (!options) return { ui_zoom: .9, details_expanded: false };
        return { ui_zoom: .9, details_expanded: false, ...JSON.parse(options.body) };
    });
    assert.deepEqual(JSON.parse(requests[1][1].body), { quick_view_layout: 'side', outline_expanded: false });
    assert.equal(requests[1][0], '/api/preferences?if_missing=1');
    assert.deepEqual(saved, { ui_zoom: .9, details_expanded: false, quick_view_layout: 'side', outline_expanded: false });
    delete globalThis.localStorage;
});

test('an existing backend prevents repeated migration and browser writes', async () => {
    globalThis.localStorage = storage({ 'paperassist-ui-zoom': '1.5' });
    const calls = [];
    const api = async (path, options) => { calls.push([path, options]); return { ui_zoom: 1 }; };
    await loadInterfacePreferences(api);
    assert.equal(calls.length, 1);
    await saveInterfacePreferences(api, { ui_zoom: 1.25 });
    assert.equal(localStorage.getItem('paperassist-ui-zoom'), '1.5');
    delete globalThis.localStorage;
});

test('only an old server without the endpoint falls back to browser state', async () => {
    globalThis.localStorage = storage({ 'paperassist-ui-zoom': '1.1' });
    const oldServer = async () => { throw Object.assign(new Error('missing'), { status: 404 }); };
    assert.deepEqual(await loadInterfacePreferences(oldServer), { ui_zoom: 1.1 });
    await saveInterfacePreferences(oldServer, { ui_zoom: 1.25, details_expanded: false });
    assert.equal(localStorage.getItem('paperassist-ui-zoom'), '1.25');
    assert.equal(localStorage.getItem('paperassist-details-expanded'), 'false');
    const failedServer = async () => { throw Object.assign(new Error('failed'), { status: 500 }); };
    await assert.rejects(loadInterfacePreferences(failedServer), /failed/);
    await assert.rejects(saveInterfacePreferences(failedServer, { ui_zoom: 1 }), /failed/);
    assert.equal(localStorage.getItem('paperassist-ui-zoom'), '1.25');
    delete globalThis.localStorage;
});
