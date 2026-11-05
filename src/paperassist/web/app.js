import { Reader, pdfjs } from "./reader.js";
import {
    captureCommentAnchor,
    revealCommentAnchor,
    clearCommentAnchor,
    commentAtClient,
    setCommentIndicators,
    clearCommentIndicators,
} from "./comments.js";
import { SOURCES, SOURCE_NAMES, createSourcesDialog, proxyUrl } from "./sources.js";
import { installShortcuts } from "./shortcuts.js";
import {
    citationPosition,
    locateCitations,
    nextCitationIndex,
    readingPosition,
    revealCitation,
} from "./citation-steps.js";

const $ = (selector, root = document) => root.querySelector(selector);
const LAYOUT_KEY = "paperassist-quick-view-layout";
const MIN_SIDE_WIDTH = 720;
function readLayoutPreference() {
    try {
        return localStorage.getItem(LAYOUT_KEY) === "side" ? "side" : "stacked";
    } catch {
        return "stacked";
    }
}
const state = {
    tool: "select-tool",
    token: "",
    tabs: [],
    current: null,
    active: null,
    detail: "references",
    references: [],
    notes: [],
    linkRef: null,
    expandedRef: null,
    searching: null,
    providers: null,
    proxy: "",
    inbox: "",
    folder: null,
    commentTool: false,
    editDoc: null,
    selectedComment: null,
    undo: null,
    layout: readLayoutPreference(),
};
const byId = (id) =>
    state.tabs.find(
        (tab) => tab.main.doc.id === id || tab.preview?.doc.id === id,
    );
const title = (doc) => doc.title || doc.filename || "Untitled paper";
const el = (tag, text, className) => {
    const node = document.createElement(tag);
    node.textContent = text ?? "";
    if (className) node.className = className;
    return node;
};
async function api(path, options = {}) {
    options.headers = { ...options.headers };
    if (options.method && options.method !== "GET")
        options.headers["X-PaperAssist-Token"] = state.token;
    const response = await fetch(path, options);
    if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
            const body = await response.json();
            message = body.error || body.message || message;
        } catch {}
        throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
}
async function job(id, update) {
    for (;;) {
        const result = await api(`/api/jobs/${id}`);
        update?.(result);
        if (result.status === "completed" || result.status === "cancelled")
            return result.result;
        if (result.status === "failed")
            throw new Error(result.error || "The operation was cancelled.");
        await new Promise((resolve) => setTimeout(resolve, 700));
    }
}
function report(error) {
    $("#save-status").hidden = false;
    $("#save-status").textContent = error;
}
function currentReader() {
    return state.active || state.current?.main;
}
function refreshToolbar() {
    const reader = currentReader();
    updateEditorOptions();
    for (const id of ["ink-tool", "highlight-tool"])
        $("#" + id).disabled = !reader || reader.readOnly ||
            !reader.editorReady || reader.modePending !== null;
    $("#select-tool").disabled = !!reader && reader.modePending !== null;
    // Commenting must leave the editor mode, which is impossible until the editors are ready.
    $("#comment-tool").disabled = !!reader && !reader.readOnly &&
        (!reader.editorReady || reader.modePending !== null);
    $("#page-input").value = reader?.viewer.currentPageNumber || 1;
    $("#page-input").max = reader?.pdf?.numPages || 1;
    $("#page-total").textContent = `/ ${reader?.pdf?.numPages || "–"}`;
    for (const pane of document.querySelectorAll(".pane"))
        pane.classList.toggle("active", pane === reader?.element);
    const status = $("#save-status");
    status.hidden = !reader || reader.readOnly;
    status.textContent = reader?.error
        ? "Save failed"
        : reader?.saving
          ? "Saving…"
          : reader?.dirty
            ? "Unsaved changes"
            : "Saved";
    status.title = reader?.error || "";
    $("#save-pdf").disabled = !reader || reader.readOnly || !reader.dirty || reader.saving;
    $("#zoom-label").textContent =
        reader && Number.isFinite(reader.viewer.currentScale)
            ? `${Math.round(reader.viewer.currentScale * 100)}%`
            : "Fit width";
}
const callbacks = {
    activate(reader) {
        const changed = state.active !== reader;
        if (state.active && changed) {
            clearCommentAnchor(state.active);
            state.selectedComment = null;
            clearUndo();
        }
        state.active = reader;
        refreshToolbar();
        if (changed && state.detail === "comments") showComments();
    },
    editorSelection(reader, selection) {
        reader.selection = selection;
        if (reader === currentReader()) {
            if (selection.hasSelection) {
                state.selectedComment = null;
                clearCommentAnchor(reader);
            }
            updateEditorOptions();
        }
    },
    loaded(reader) {
        loadCommentIndicators(reader);
        if (reader === state.current?.main) showOutline();
        refreshToolbar();
    },
    position(reader) {
        if (reader === currentReader()) refreshToolbar();
    },
    status() {
        refreshToolbar();
    },
    error: report,
    promote(reader) {
        promote(reader);
    },
    closePreview() {
        closePreview();
    },
    toggleLayout() {
        state.layout = state.layout === "side" ? "stacked" : "side";
        try {
            localStorage.setItem(LAYOUT_KEY, state.layout);
        } catch {}
        applyLayout();
    },
    citation(reader, text, destination) {
        if (reader !== state.current?.main) return false;
        if (destination !== null) {
            const matches = state.references.filter((ref) =>
                ref.destinations?.includes(destination),
            );
            if (matches.length !== 1) return false;
            openReference(matches[0]);
            return true;
        }
        let refs;
        const marker = text.match(/^\s*\[([\d,;\s–—-]+)\]\s*$/);
        if (marker) {
            const numbers = [];
            for (const part of marker[1].split(/[,;]/)) {
                const range = part.trim().match(/^(\d+)(?:\s*[–—-]\s*(\d+))?$/);
                if (!range) return false;
                const start = Number(range[1]),
                    end = Number(range[2] || range[1]);
                if (end < start || end - start > 30) return false;
                for (let n = start; n <= end; n++) numbers.push(n);
            }
            const matches = numbers.map((n) =>
                state.references.filter(
                    (ref) =>
                        String(ref.label || "").replace(/[\[\].\s]/g, "") ===
                        String(n),
                ),
            );
            if (!matches.length || matches.some((group) => group.length !== 1))
                return false;
            refs = matches.map((group) => group[0]);
        } else {
            const authorYear = text
                .trim()
                .match(
                    /^\(?([A-Z][A-Za-z'’-]+)(?:\s+et al\.?)?[,]?\s+\(?(19\d{2}|20\d{2})\)?\)?$/,
                );
            if (!authorYear) return false;
            refs = state.references.filter((ref) => {
                const author = String(ref.authors || "")
                    .split(/[,;]|\band\b/i)[0]
                    .trim()
                    .split(/\s+/)
                    .at(-1);
                return (
                    author?.toLowerCase() === authorYear[1].toLowerCase() &&
                    String(ref.year) === authorYear[2]
                );
            });
            if (refs.length !== 1) return false;
        }
        if (refs.length === 1) openReference(refs[0]);
        else {
            state.detail = "references";
            switchDetail();
            const choices = el("div", "", "citation-choices");
            choices.append(
                el("p", `Citation ${text}: choose a reference`, "status"),
            );
            for (const ref of refs) {
                const button = el(
                    "button",
                    `${ref.label} ${ref.title || ref.raw || "Reference"}`,
                    "outline-item",
                );
                button.onclick = () => openReference(ref);
                choices.append(button);
            }
            $("#detail-content").prepend(choices);
        }
        return true;
    },
};
function stored(doc) {
    try {
        return (
            JSON.parse(localStorage.getItem(`paperassist-view-${doc.id}`)) || {}
        );
    } catch {
        return {};
    }
}
// The saved preference stays; a reader too narrow for two columns shows the stacked layout.
function applyLayout() {
    const canSplitSide = $("#readers").clientWidth >= MIN_SIDE_WIDTH;
    const layout = state.layout === "side" && canSplitSide ? "side" : "stacked";
    for (const tab of state.tabs) {
        tab.stack.dataset.layout = layout;
        tab.preview?.setLayout(layout, canSplitSide);
    }
}
new ResizeObserver(applyLayout).observe($("#readers"));
function makeReader(doc, host, initial) {
    const reader = new Reader(
        doc,
        host,
        api,
        callbacks,
        initial || stored(doc),
    );
    reader.bus.on("annotationeditormodechanged", ({ mode }) => {
        if (reader.modePending !== null && reader.modePending !== mode) return;
        // Select only lends the mode to edit one saved annotation; the toolbar stays on Select.
        if (reader.borrowed) return;
        const tool = Object.entries(editorModes).find(
            ([, value]) => value === mode,
        )?.[0];
        if (tool) reader.tool = tool;
        if (reader === currentReader()) updateEditorOptions();
    });
    reader.open().catch((e) => report(`Could not open PDF: ${e.message}`));
    return reader;
}
function renderTabs() {
    const container = $("#tabs");
    container.replaceChildren();
    for (const tab of state.tabs) {
        const item = el(
            "div",
            "",
            `tab${tab === state.current ? " current" : ""}`,
        );
        const button = el("button", title(tab.main.doc), "tab-title");
        button.title = title(tab.main.doc);
        if (tab === state.current) button.setAttribute("aria-current", "page");
        button.onclick = () => selectTab(tab);
        const close = el("button", "×", "tab-close");
        close.setAttribute("aria-label", `Close ${title(tab.main.doc)}`);
        close.onclick = () => closeTab(tab);
        item.append(button, close);
        container.append(item);
    }
    $("#welcome").hidden = state.tabs.length > 0;
}
function selectTab(tab) {
    if (state.active && state.active !== tab.main) {
        clearCommentAnchor(state.active);
        state.selectedComment = null;
        clearUndo();
    }
    state.current = tab;
    state.active = tab.main;
    state.folder = null;
    for (const entry of state.tabs) entry.stack.hidden = entry !== tab;
    renderTabs();
    refreshToolbar();
    showOutline();
    loadReferences();
    if (state.detail === "comments") showComments();
}
async function openDocument(doc, initial) {
    const existing = byId(doc.id);
    if (existing) {
        selectTab(existing);
        if (existing.preview?.doc.id === doc.id)
            callbacks.activate(existing.preview);
        return;
    }
    const stack = el("div", "", "reader-stack");
    $("#readers").append(stack);
    const tab = { stack, main: makeReader(doc, stack, initial), preview: null };
    tab.main.setKind("main");
    state.tabs.push(tab);
    applyLayout();
    selectTab(tab);
    await loadReferences(true);
}
async function saveReader(reader) {
    if (!reader || reader.readOnly || !reader.dirty || reader.saving) return false;
    const saved = await reader.explicitSave();
    refreshToolbar();
    return saved;
}
function askUnsaved(reader) {
    const dialog = $("#unsaved-dialog");
    $("#unsaved-message").textContent = `Save changes to ${title(reader.doc)} before closing?`;
    return new Promise((resolve) => {
        const finish = (result) => {
            dialog.close();
            resolve(result);
        };
        $("#unsaved-save").onclick = () => finish("save");
        $("#unsaved-discard").onclick = () => finish("discard");
        $("#unsaved-cancel").onclick = () => finish("cancel");
        dialog.oncancel = (event) => {
            event.preventDefault();
            finish("cancel");
        };
        dialog.showModal();
    });
}
async function prepareClose(readers) {
    const decisions = new Map();
    for (const reader of readers.filter(Boolean)) {
        reader.commitEdits();
        if (reader.saving) {
            report("Wait for the PDF save to finish before closing.");
            return null;
        }
        if (!reader.dirty) continue;
        const choice = await askUnsaved(reader);
        if (choice === "cancel") return null;
        if (choice === "save" && !(await saveReader(reader))) return null;
        decisions.set(reader, choice === "discard");
    }
    return decisions;
}
async function closeReader(reader, discard = false) {
    if (!(await reader.close({ discard }))) return false;
    clearCommentAnchor(reader);
    clearCommentIndicators(reader);
    return true;
}
async function closeTab(tab) {
    const decisions = await prepareClose([tab.preview, tab.main]);
    if (!decisions) return;
    if (tab.preview && !(await closeReader(tab.preview, decisions.get(tab.preview)))) return;
    if (!(await closeReader(tab.main, decisions.get(tab.main)))) return;
    tab.stack.remove();
    state.tabs.splice(state.tabs.indexOf(tab), 1);
    if (state.current === tab) {
        state.current = null;
        state.active = null;
        if (state.tabs.length) selectTab(state.tabs.at(-1));
        else {
            $("#outline").textContent = "Open a paper to see its outline.";
            $("#detail-content").textContent =
                "Open a paper to explore its references.";
        }
    }
    renderTabs();
    refreshToolbar();
}
async function closePreview() {
    const tab = state.current;
    if (!tab?.preview) return;
    const decisions = await prepareClose([tab.preview]);
    if (!decisions || !(await closeReader(tab.preview, decisions.get(tab.preview)))) return;
    tab.preview = null;
    state.active = tab.main;
    refreshToolbar();
    if (state.detail === "comments") showComments();
}
async function preview(doc) {
    const tab = state.current;
    const owner = state.tabs
        .flatMap((entry) => [entry.main, entry.preview])
        .find(
            (reader) => reader && !reader.readOnly && reader.doc.id === doc.id,
        );
    if (owner && owner === tab.preview) {
        callbacks.activate(owner);
        return;
    }
    if (tab.preview) {
        const decisions = await prepareClose([tab.preview]);
        if (!decisions || !(await closeReader(tab.preview, decisions.get(tab.preview)))) return;
    }
    const initial = owner ? { ...stored(doc), readOnly: true } : undefined;
    tab.preview = makeReader(doc, tab.stack, initial);
    tab.preview.setKind("preview", owner?.dirty ? "Last saved version" : undefined);
    applyLayout();
    callbacks.activate(tab.preview);
}
async function promote(reader) {
    const tab = state.current;
    if (tab.preview !== reader) return;
    if (reader.readOnly) {
        const page = reader.viewer.currentPageNumber,
            scale = reader.viewer.currentScaleValue;
        const ownerTab =
            state.tabs.find(
                (entry) =>
                    entry !== tab &&
                    ((entry.main.doc.id === reader.doc.id &&
                        !entry.main.readOnly) ||
                        (entry.preview?.doc.id === reader.doc.id &&
                            !entry.preview.readOnly)),
            ) || (tab.main.doc.id === reader.doc.id ? tab : null);
        if (!ownerTab) {
            report(
                "The editable tab is no longer open. Close this preview and open it again.",
            );
            return;
        }
        await closeReader(reader);
        tab.preview = null;
        selectTab(ownerTab);
        if (ownerTab.preview?.doc.id === reader.doc.id)
            await promote(ownerTab.preview);
        const owner = state.current.main;
        owner.viewer.currentScaleValue = scale;
        owner.viewer.currentPageNumber = page;
        return;
    }
    tab.preview = null;
    const stack = el("div", "", "reader-stack");
    $("#readers").append(stack);
    stack.append(reader.element);
    reader.setKind("main");
    const newTab = { stack, main: reader, preview: null };
    state.tabs.push(newTab);
    applyLayout();
    selectTab(newTab);
    loadReferences(true);
}

async function showOutline() {
    const reader = state.current?.main,
        host = $("#outline");
    host.replaceChildren();
    if (!reader?.pdf) {
        host.textContent = "Loading outline…";
        return;
    }
    const outline = await reader.pdf.getOutline();
    if (!outline?.length) {
        for (let i = 1; i <= reader.pdf.numPages; i++) {
            const button = el("button", `Page ${i}`, "outline-item");
            button.onclick = () => (reader.viewer.currentPageNumber = i);
            host.append(button);
        }
        return;
    }
    function add(items, depth = 0) {
        for (const item of items) {
            const button = el("button", item.title, "outline-item");
            button.style.paddingLeft = `${depth * 12 + 3}px`;
            button.onclick = () =>
                item.dest && reader.links.goToDestination(item.dest);
            host.append(button);
            if (item.items) add(item.items, depth + 1);
        }
    }
    add(outline);
}
async function library() {
    state.linkRef = null;
    $("#library-dialog").showModal();
    await listLibrary();
    $("#search").focus();
}
async function listLibrary() {
    const host = $("#library-list");
    host.textContent = "Loading library…";
    try {
        const result = await api(
            `/api/documents?q=${encodeURIComponent($("#search").value)}`,
        );
        let docs = Array.isArray(result) ? result : result.documents;
        host.replaceChildren();
        if (state.linkRef) {
            docs = docs.filter((doc) => String(doc.id) !== String(state.current.main.doc.id));
            const candidates = state.linkRef.local_candidates || [];
            docs.sort(
                (a, b) =>
                    Number(candidates.some((c) => c.id === b.id)) -
                    Number(candidates.some((c) => c.id === a.id)),
            );
            host.append(
                el(
                    "p",
                    `Link ${state.linkRef.label || "reference"}: ${state.linkRef.title || state.linkRef.raw || "Unidentified reference"}. Verify the match before linking.`,
                    "library-heading",
                ),
            );
        }
        if (!docs.length) {
            host.textContent = "No papers found. Import PDFs to get started.";
            return;
        }
        for (const doc of docs) {
            const row = el("div", "", "library-row");
            const button = el("button", "");
            button.append(
                el("strong", title(doc)),
                el(
                    "small",
                    [
                        state.linkRef?.local_candidates?.some((candidate) => candidate.id === doc.id) ? "Suggested match — verify" : "",
                        doc.authors,
                        doc.year,
                        ["incomplete", "needs_review"].includes(
                            doc.metadata_status,
                        )
                            ? "Metadata needs review"
                            : "",
                    ]
                        .filter(Boolean)
                        .join(" · ") || doc.filename,
                ),
            );
            button.onclick = async () => {
                if (state.linkRef) {
                    try {
                        await api(
                            `/api/documents/${state.current.main.doc.id}/references/${state.linkRef.id}`,
                            {
                                method: "PATCH",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                    local_document_id: doc.id,
                                }),
                            },
                        );
                        state.linkRef = null;
                        $("#library-dialog").close();
                        await loadReferences();
                        await preview(doc);
                    } catch (e) {
                        $("#import-status").textContent = e.message;
                    }
                } else {
                    $("#library-dialog").close();
                    openDocument(doc);
                }
            };
            const edit = el("button", "Edit", "metadata-edit");
            edit.onclick = () => editMetadata(doc);
            row.append(button, edit);
            host.append(row);
        }
    } catch (error) {
        host.textContent = error.message;
    }
}
async function importFiles(files) {
    const pdfs = [...files].filter((file) => /\.pdf$/i.test(file.name));
    if (!pdfs.length) return;
    const status = $("#import-status");
    status.textContent = `Importing ${pdfs.length} PDFs (${(pdfs.reduce((n, f) => n + f.size, 0) / 1048576).toFixed(1)} MB selected)…`;
    const form = new FormData();
    pdfs.forEach((file) => form.append("files", file));
    try {
        const started = await api("/api/import", {
            method: "POST",
            body: form,
        });
        const cancel = $("#cancel-import");
        cancel.hidden = false;
        cancel.onclick = async () => {
            cancel.disabled = true;
            await api(`/api/jobs/${started.id}/cancel`, { method: "POST" });
        };
        const result = await job(
            started.id,
            (j) =>
                (status.textContent = j.progress
                    ? `Importing… ${j.progress.completed} / ${j.progress.total}`
                    : "Importing…"),
        );
        const files = result?.files || [];
        const added = files.filter(
            (item) => item.document && !item.document.duplicate,
        ).length;
        const duplicates = files.filter(
            (item) => item.document?.duplicate,
        ).length;
        const errors = files.filter((item) => item.error);
        const review = files.filter(
            (item) => item.document?.metadata_status === "needs_review",
        ).length;
        status.textContent = `${added} added · ${duplicates} duplicates · ${review} need metadata review · ${errors.length} errors${errors.length ? `: ${errors.map((item) => `${item.filename}: ${item.error}`).join("; ")}` : ""}`;
        await listLibrary();
    } catch (error) {
        status.textContent = `Import failed: ${error.message}`;
    } finally {
        $("#cancel-import").hidden = true;
        $("#cancel-import").disabled = false;
    }
}
async function loadReferences(propose = false) {
    const tab = state.current;
    if (!tab) return;
    try {
        const data = await api(`/api/documents/${tab.main.doc.id}/references`);
        if (state.current !== tab) return;
        state.references = (data.references || []).sort(
            (a, b) => (b.citation_count ?? -1) - (a.citation_count ?? -1),
        );
        state.referenceStatus = data.status;
        if (state.detail === "references") renderReferences();
        const dialog = $("#acquire-dialog");
        // Only opening a paper proposes the dialog; later reloads refresh it if the reader left it open.
        if (propose && !dialog.open) dialog.showModal();
        if (dialog.open) renderAcquire();
        if (propose && data.status === "not_analyzed") analyzeReferences();
    } catch (error) {
        if (state.detail === "references")
            $("#detail-content").textContent =
                `References unavailable: ${error.message}`;
    }
}
function count(ref) {
    return ref.citation_count == null
        ? "Count unavailable"
        : `${ref.citation_count} ${ref.count_status === "complete" ? "citations" : "detected"}`;
}
const hasCitations = (ref) => Boolean(ref.destinations?.length || ref.occurrences?.length);
// Stepping shows where the reader is in the series; otherwise the detected count.
function updateCount(node, ref) {
    const series = state.current?.citationSeries;
    const stepping = series?.refId === ref.id;
    node.textContent = stepping ? `${series.index + 1} of ${series.total}` : count(ref);
    node.setAttribute("aria-label",
        `Go to the next citation in the text${stepping || ref.citation_count != null ? `, ${node.textContent}` : ""}`);
}
function refreshCounts() {
    for (const node of document.querySelectorAll("button.ref-count")) {
        const ref = state.references.find((item) => String(item.id) === node.closest(".reference")?.dataset.referenceId);
        if (ref) updateCount(node, ref);
    }
}
const referenceRow = (ref) =>
    [...document.querySelectorAll(".reference")].find((row) => row.dataset.referenceId === String(ref.id));
let noteTimer;
function citationNote(ref, text) {
    const note = referenceRow(ref)?.querySelector(".citation-note");
    if (!note) return;
    note.textContent = text;
    clearTimeout(noteTimer);
    noteTimer = setTimeout(() => note.replaceChildren(), 3500);
}
// Only the main reader of the current tab moves; the quick view is never scrolled.
async function stepCitation(ref) {
    const tab = state.current;
    const reader = tab?.main;
    if (!reader?.pdf || !reader.viewer.pagesCount) return citationNote(ref, "The paper is still loading");
    if (tab.stepping) return;
    tab.stepping = true;
    try {
        const list = await locateCitations(reader, ref);
        if (state.current !== tab) return;
        if (!list.length) return citationNote(ref, "No citations found in the text");
        const series = tab.citationSeries?.refId === ref.id ? tab.citationSeries : null;
        const index = nextCitationIndex(
            list.map((citation) => citationPosition(reader, citation)), series?.index, readingPosition(reader));
        tab.citationSeries = { refId: ref.id, index, total: list.length };
        const exact = revealCitation(reader, list[index]);
        refreshCounts();
        if (!exact) citationNote(ref, `Exact position not found; showing page ${list[index].page}`);
    } catch (e) {
        console.error(e);
        citationNote(ref, "Citations could not be read from this PDF");
    } finally {
        tab.stepping = false;
    }
}
// A missing reference first opens its details; a click on the body of any card then steps.
function cardClick(ref, { focusCount = false } = {}) {
    if (ref.local_document_id || state.expandedRef === ref.id) return stepCitation(ref);
    openReference(ref);
    if (focusCount) referenceRow(ref)?.querySelector("button.ref-count")?.focus();
}
function providerSummary() {
    if (!state.providers) return "Checking sources…";
    const names = (list) => list.map((source) => source.name).join(", ");
    const enabled = SOURCES.filter((source) => state.providers[source.id]?.enabled);
    const ready = enabled.filter((source) => state.providers[source.id].configured);
    const missing = enabled.filter((source) => !state.providers[source.id].configured);
    const off = SOURCES.filter((source) => !enabled.includes(source));
    return [
        ready.length ? `Sources: ${names(ready)}.` : "No source is ready.",
        missing.length && `Not set up: ${names(missing)}.`,
        off.length && `Turned off: ${names(off)}.`,
    ].filter(Boolean).join(" ");
}
// Shared by the panel and the dialog so both offer the settings next to the summary.
function showSources(host) {
    const settings = el("button", "Sources…", "link-button");
    settings.onclick = () => openSources();
    host.replaceChildren(providerSummary(), " ", settings);
}
function providerName(name) {
    return SOURCE_NAMES[name] || (name === "local" ? "library" : name);
}
const RETRIEVAL_LABELS = {
    downloaded: "Downloaded", local: "In library", not_identified: "Not identified",
    ambiguous: "Several matches — choose manually", not_found: "No open-access PDF found",
    failed: "Search incomplete — try again", blocked: "PDF found — download it in your browser",
};
const ATTEMPT_LABELS = {
    found: "Found", downloaded: "Downloaded", not_found: "Not found",
    needs_identification: "Needs identification", ambiguous: "Several matches", error: "Error", blocked: "Blocked",
};
// Results without version 2 came from an older pipeline whose messages can contradict
// the current sources, so they count as not searched.
const currentRetrieval = (ref) => (ref.retrieval?.version === 2 ? ref.retrieval : null);
function referenceStatus(ref) {
    const retrieval = currentRetrieval(ref);
    if (ref.local_document_id && ref.local_conflict)
        return "Linked PDF looks like a different paper — verify";
    if (ref.local_document_id)
        return retrieval?.status === "downloaded" ? "Downloaded · In library — open" : "In library — open";
    if (ref.legacy) return "Previous reference — verify link";
    const location = ref.local_invalid ? "Invalid link: source paper selected" : ref.local_missing ? "Linked PDF missing from library" : ref.local_candidates?.length
        ? "Possible local match — verify" : "Not in library";
    return `${location} · ${retrieval?.status === "local" ? "Not searched" : RETRIEVAL_LABELS[retrieval?.status] || "Not searched"}`;
}
// The link comes from a third-party service, so only web schemes may become clickable.
function openPdfLink(ref, className = "") {
    const retrieval = currentRetrieval(ref);
    if (retrieval?.status !== "blocked") return null;
    let url;
    try { url = new URL(retrieval.pdf_url); } catch { return null; }
    if (!["http:", "https:"].includes(url.protocol)) return null;
    const link = el("a", "Open PDF", className);
    link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
    return link;
}
// Links to open through the library proxy; the reader's own browser session does the sign-in.
// The article page is offered where the row already asks for attention, not on every row.
function libraryLinks(ref, withArticle) {
    const targets = [];
    if (currentRetrieval(ref)?.status === "blocked")
        targets.push(["Open PDF via library", currentRetrieval(ref).pdf_url]);
    if (withArticle && ref.doi) targets.push(["Open via library", `https://doi.org/${ref.doi}`]);
    return targets.flatMap(([label, target]) => {
        const href = proxyUrl(state.proxy, target);
        if (!href) return [];
        const link = el("a", label);
        link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
        return [link];
    });
}
// One class per row, from the same facts as the status text, so the tint never disagrees with it.
function referenceTone(ref) {
    if (ref.local_document_id) return ref.local_conflict ? "has-conflict" : "is-local";
    const candidates = state.folder?.id === ref.id && state.folder.candidates?.length;
    return currentRetrieval(ref)?.status === "blocked" || candidates ? "needs-action" : "is-missing";
}
function retrievalSummary(ref) {
    if (currentRetrieval(ref)) return ref.retrieval.summary || "";
    // A linked paper needs no search, so the age of an old search is irrelevant there.
    return ref.retrieval && !ref.local_document_id ? "Earlier search used fewer sources." : "";
}
// Not-configured and skipped providers are availability, not results, so they stay out of the list.
function attemptList(ref) {
    const shown = (currentRetrieval(ref)?.attempts || []).filter((attempt) => !["skipped", "not_configured"].includes(attempt.status));
    if (!shown.length) return null;
    const list = el("ul", "", "reference-attempts");
    for (const attempt of shown)
        list.append(el("li", `${providerName(attempt.provider)}: ${attempt.message || ATTEMPT_LABELS[attempt.status] || attempt.status}`));
    return list;
}
// Shared by the panel and the dialog so a running or failed analysis is shown where it was started.
function emptyReferences(host) {
    const analysis = state.current.analysis;
    const text = analysis?.running ? "Analyzing references… You can continue reading."
        : analysis?.error ? `Analysis failed: ${analysis.error}`
        : state.referenceStatus === "not_analyzed" ? "No references have been analyzed yet."
        : "No references were found in this paper.";
    host.append(el("p", text, "empty"));
    if (analysis?.running) return;
    const button = el("button", analysis?.error ? "Try again" : "Analyze references", "panel-action");
    button.onclick = analyzeReferences;
    host.append(button);
}
function renderReferences() {
    const host = $("#detail-content");
    host.replaceChildren();
    const action = el("button", "Find or select references", "panel-action");
    action.onclick = () => { $("#acquire-dialog").showModal(); renderAcquire(); };
    const sources = el("p", "", "reference-meta");
    showSources(sources);
    host.append(action, sources);
    if (state.current?.acquisitionSummary)
        host.append(el("p", state.current.acquisitionSummary, "reference-meta"));
    if (!state.references.length) {
        emptyReferences(host);
        return;
    }
    for (const ref of state.references) {
        const row = el("div", "", `reference ${referenceTone(ref)}${state.expandedRef === ref.id ? " expanded" : ""}`);
        row.dataset.referenceId = ref.id;
        const top = el("div", "", "ref-top");
        let total = el("span", count(ref));
        if (hasCitations(ref)) {
            total = el("button", "", "ref-count");
            updateCount(total, ref);
            total.onclick = () => cardClick(ref, { focusCount: true });
        }
        top.append(el("span", ref.label || "Reference"), total);
        const name = el("button", ref.title || ref.raw || "Unidentified reference", "reference-title");
        name.onclick = () => (!ref.local_document_id && state.expandedRef === ref.id ? stepCitation(ref) : openReference(ref));
        // Buttons and links keep their own actions; a drag that selected card text is not a click.
        row.onclick = (event) => {
            const selection = document.getSelection();
            if (event.target.closest("button, a, input, select, textarea")) return;
            if (!selection.isCollapsed && row.contains(selection.anchorNode)) return;
            cardClick(ref);
        };
        row.append(top, name, el("div", [ref.authors, ref.year, ref.doi && `DOI ${ref.doi}`].filter(Boolean).join(" · "), "reference-meta"));
        row.append(el("div", state.searching?.id === ref.id ? state.searching.message : referenceStatus(ref), "reference-status"));
        if (ref.local_document_id && ref.local_conflict)
            row.append(el("div", ref.local_conflict, "reference-meta reference-conflict"));
        const summary = retrievalSummary(ref);
        if (summary) row.append(el("div", summary, "reference-meta"));
        const expanded = state.expandedRef === ref.id && !ref.local_document_id;
        const status = currentRetrieval(ref)?.status;
        const missing = !ref.local_document_id;
        if (missing && (status === "blocked" || status === "not_found" || expanded)) {
            const actions = el("div", "", "ref-actions");
            actions.append(
                ...[openPdfLink(ref), ...libraryLinks(ref, status === "blocked" || expanded)].filter(Boolean),
                folderButton(ref),
            );
            row.append(actions);
            const folder = folderResult(ref);
            if (folder) row.append(folder);
        }
        if (ref.local_document_id || ref.local_invalid || ref.local_missing) {
            const actions = el("div", "", "ref-actions");
            const change = el("button", "Change PDF");
            change.onclick = () => linkReference(ref);
            actions.append(change);
            if (ref.manual_link) {
                const reset = el("button", "Reset PDF link");
                reset.onclick = () => resetReferenceLink(ref);
                actions.append(reset);
            }
            row.append(actions);
        }
        if (expanded) {
            const attempts = attemptList(ref);
            if (attempts) row.append(attempts);
            const actions = el("div", "", "ref-actions");
            for (const candidate of ref.local_candidates || []) {
                const button = el("button", `Use ${candidate.title || "local PDF"}${candidate.year ? ` (${candidate.year})` : ""}`);
                button.onclick = async () => {
                    try {
                        await api(`/api/documents/${state.current.main.doc.id}/references/${ref.id}`, {
                            method: "PATCH", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ local_document_id: candidate.id }),
                        });
                        await loadReferences();
                        const linked = state.references.find((item) => item.id === ref.id);
                        if (linked) await openReference(linked);
                    } catch (error) { report(error.message); }
                };
                actions.append(button);
            }
            const find = el("button", "Find open access");
            find.disabled = !!state.searching || !!ref.legacy;
            find.onclick = () => runAcquisition([ref.id], ref.id);
            const link = el("button", "Link local PDF");
            link.onclick = () => linkReference(ref);
            actions.append(find, link);
            for (const [label, url] of Object.entries(ref.search_urls || {})) {
                if (!url) continue;
                const search = el("a", label.toLowerCase().includes("pdf") ? "Search PDF" : "Search web");
                search.href = url; search.target = "_blank"; search.rel = "noopener noreferrer";
                actions.append(search);
            }
            row.append(actions);
        }
        const note = el("div", "", "reference-meta citation-note");
        note.setAttribute("role", "status");
        row.append(note);
        host.append(row);
    }
}
function folderButton(ref) {
    const button = el("button", "");
    const glyph = el("span", "↻");
    glyph.setAttribute("aria-hidden", "true");
    button.append(glyph, " Check folder");
    button.setAttribute("aria-label", "Check the downloads folder for this paper");
    button.disabled = state.folder?.id === ref.id && state.folder.busy;
    button.onclick = () => checkFolder(ref);
    return button;
}
const formatSize = (bytes) => bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
function folderResult(ref) {
    if (state.folder?.id !== ref.id) return null;
    const box = el("div", "", "folder-result");
    const message = el("p", state.folder.message, "reference-meta");
    message.setAttribute("role", "status");
    box.append(message);
    for (const file of state.folder.candidates || []) {
        const line = el("div", "", "folder-candidate");
        const when = new Date(file.modified).toLocaleDateString("en", { dateStyle: "medium" });
        line.append(el("span", `${file.name} · ${formatSize(file.size)} · ${when}`));
        const link = el("button", "Link this PDF", "link-button");
        link.onclick = () => linkFolderFile(ref, file.name);
        line.append(link);
        box.append(line);
    }
    return box;
}
// A match found in the downloads folder is opened like a successful search.
async function showLinked(refId) {
    state.folder = null;
    await loadReferences();
    const linked = state.references.find((item) => item.id === refId);
    if (linked?.local_document_id) await openReference(linked);
}
async function checkFolder(ref) {
    if (!state.inbox) return openSources("inbox");
    const tab = state.current;
    state.folder = { id: ref.id, message: "Checking folder…", busy: true };
    renderReferences();
    try {
        const started = await api(`/api/documents/${tab.main.doc.id}/references/${ref.id}/check-folder`, { method: "POST" });
        const result = await job(started.id);
        if (state.current !== tab) return;
        if (result.status === "linked") return await showLinked(ref.id);
        state.folder = { id: ref.id, message: result.summary, candidates: result.candidates };
    } catch (error) {
        if (state.current === tab) state.folder = { id: ref.id, message: `Folder check failed: ${error.message}` };
    }
    if (state.current === tab) renderReferences();
}
async function linkFolderFile(ref, name) {
    const tab = state.current;
    state.folder = { id: ref.id, message: "Linking…", busy: true };
    renderReferences();
    try {
        await api(`/api/documents/${tab.main.doc.id}/references/${ref.id}/link-file`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
        });
        if (state.current === tab) await showLinked(ref.id);
    } catch (error) {
        if (state.current === tab) {
            state.folder = { id: ref.id, message: `Could not link: ${error.message}` };
            renderReferences();
        }
    }
}
async function openReference(ref) {
    if (!ref.local_document_id) {
        state.expandedRef = ref.id;
        state.detail = "references";
        setPanel("details", true);
        switchDetail();
        referenceRow(ref)?.scrollIntoView({ block: "nearest" });
        return;
    }
    try {
        const data = await api("/api/documents");
        const doc = (Array.isArray(data) ? data : data.documents).find((d) => d.id === ref.local_document_id);
        if (doc) await preview(doc);
        else report("The linked PDF is not in the library.");
    } catch (error) { report(error.message); }
}

async function analyzeReferences() {
    const tab = state.current;
    if (tab.analysis?.running) return;
    tab.analysis = { running: true };
    refreshReferenceViews();
    try {
        const started = await api(`/api/documents/${tab.main.doc.id}/references/analyze`, { method: "POST" });
        await job(started.id);
        tab.analysis = null;
    } catch (error) {
        tab.analysis = { error: error.message };
    }
    // Another tab reloads its own references when selected.
    if (state.current === tab) await loadReferences();
}
function refreshReferenceViews() {
    if (state.detail === "references") renderReferences();
    if ($("#acquire-dialog").open) renderAcquire();
}
async function resetReferenceLink(ref) {
    try {
        await api(`/api/documents/${state.current.main.doc.id}/references/${ref.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ local_document_id: null }),
        });
        await loadReferences();
    } catch (error) {
        report(`Could not reset PDF link: ${error.message}`);
    }
}
async function linkReference(ref) {
    state.linkRef = ref;
    $("#search").value = "";
    $("#library-dialog").showModal();
    await listLibrary();
    $("#search").focus();
}
function editMetadata(doc) {
    state.editDoc = doc;
    const form = $("#metadata-form");
    for (const key of ["title", "authors", "year", "doi"])
        form.elements[key].value = doc[key] || "";
    $("#metadata-status").textContent = "";
    $("#metadata-dialog").showModal();
}
$("#metadata-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    try {
        const doc = await api(`/api/documents/${state.editDoc.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(values),
        });
        Object.assign(state.editDoc, doc);
        $("#metadata-dialog").close();
        await listLibrary();
        renderTabs();
    } catch (error) {
        $("#metadata-status").textContent = error.message;
    }
};

function renderAcquire() {
    showSources($("#acquire-providers"));
    const host = $("#acquire-list");
    host.replaceChildren();
    if (!state.references.length) {
        emptyReferences(host);
        updateSelectionCount();
        return;
    }
    for (const ref of state.references) {
        const row = el("div", "", `acquire-row ${referenceTone(ref)}`);
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = ref.id;
        box.disabled = !!ref.local_document_id || !!ref.legacy;
        const saved = JSON.parse(
            localStorage.getItem(
                `paperassist-selection-${state.current.main.doc.id}`,
            ) || "[]",
        );
        box.checked = !box.disabled && saved.includes(String(ref.id));
        box.onchange = saveSelection;
        const label = el(
            "label",
            `${ref.label || ""} ${ref.title || ref.raw || "Unidentified reference"}`,
        );
        label.prepend(box, " ");
        label.append(
            el("small", `${count(ref)} · ${ref.local_document_id && !ref.local_conflict ? "In library — no download needed" : referenceStatus(ref)}`),
        );
        const summary = retrievalSummary(ref);
        if (summary) label.append(el("small", summary));
        row.append(label);
        if (!ref.local_document_id)
            row.append(...[openPdfLink(ref, "open-pdf"), ...libraryLinks(ref, currentRetrieval(ref)?.status === "blocked")].filter(Boolean));
        host.append(row);
    }
    updateSelectionCount();
}
const commentLoads = new WeakMap();
function updateCommentIndicators(reader, notes) {
    const readers = new Set([reader]);
    for (const tab of state.tabs) {
        if (tab.main.doc.id === reader.doc.id) readers.add(tab.main);
        if (tab.preview?.doc.id === reader.doc.id) readers.add(tab.preview);
    }
    for (const target of readers) {
        commentLoads.set(target, (commentLoads.get(target) || 0) + 1);
        setCommentIndicators(target, notes);
    }
}
async function loadCommentIndicators(reader) {
    const version = (commentLoads.get(reader) || 0) + 1;
    commentLoads.set(reader, version);
    try {
        const data = await api(`/api/documents/${reader.doc.id}/notes`);
        if (commentLoads.get(reader) === version && reader.element.isConnected)
            setCommentIndicators(reader, Array.isArray(data) ? data : data.notes);
    } catch (error) {
        report(`Comments unavailable: ${error.message}`);
    }
}
let commentDraft = null;
let pendingAnchor = null;
async function showComments() {
    const reader = currentReader(),
        host = $("#detail-content");
    if (!reader) return;
    host.textContent = "Loading comments…";
    try {
        const data = await api(`/api/documents/${reader.doc.id}/notes`);
        if (reader !== currentReader() || state.detail !== "comments") return;
        const notes = Array.isArray(data) ? data : data.notes;
        host.replaceChildren();
        if (commentDraft?.reader === reader) {
            const form = el("form", "", "comment-composer");
            const head = el("div", "", "comment-anchor");
            fillDraftHead(head);
            form.append(head);
            const text = document.createElement("textarea");
            text.rows = 4;
            text.placeholder = "Write a comment…";
            text.setAttribute("aria-label", "Comment text");
            text.value = commentDraft.text || "";
            text.oninput = () => {
                commentDraft.text = text.value;
            };
            const actions = el("div", "", "comment-actions");
            const save = el("button", "Save comment", "primary");
            save.type = "submit";
            const cancel = el("button", "Cancel");
            cancel.type = "button";
            cancel.onclick = () => {
                commentDraft = null;
                clearCommentAnchor(reader);
                updateEditorOptions();
                showComments();
            };
            actions.append(save, cancel);
            form.append(text, actions);
            form.onsubmit = async (event) => {
                event.preventDefault();
                const draft = commentDraft;
                if (!draft || !text.value.trim()) return;
                save.disabled = true;
                try {
                    const latest = await api(
                        `/api/documents/${draft.reader.doc.id}/notes`,
                    );
                    const updated = Array.isArray(latest)
                        ? latest
                        : latest.notes;
                    updated.push({
                        page: draft.page,
                        text: text.value.trim(),
                        ...(draft.quote
                            ? { quote: draft.quote, anchor: draft.anchor }
                            : {}),
                    });
                    await api(`/api/documents/${draft.reader.doc.id}/notes`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(updated),
                    });
                    updateCommentIndicators(draft.reader, updated);
                    clearUndo();
                    commentDraft = null;
                    clearCommentAnchor(draft.reader);
                    updateEditorOptions();
                    if (
                        currentReader() === draft.reader &&
                        state.detail === "comments"
                    )
                        showComments();
                } catch (error) {
                    status.textContent = `Could not save: ${error.message}`;
                    save.disabled = false;
                }
            };
            const status = el("p", "", "status");
            form.append(status);
            host.append(form);
            text.focus();
        } else {
            const add = el("button", "Add comment", "panel-action");
            add.onclick = comment;
            host.append(add);
        }
        if (!notes.length)
            host.append(el("p", "No comments on this document yet.", "empty"));
        for (const note of notes) {
            const row = el("div", "", "note");
            row.dataset.note = JSON.stringify(note);
            const jump = el("button", `Page ${note.page}`, "note-meta");
            jump.onclick = () => selectComment(reader, note, true);
            row.append(jump);
            if (note.quote || note.selection)
                row.append(el("blockquote", note.quote || note.selection));
            row.append(el("p", note.text));
            const remove = el("button", "Delete comment", "note-delete");
            remove.onclick = () => updateComment(reader, note, null);
            row.append(remove);
            host.append(row);
        }
        markSelectedNote();
    } catch (error) {
        host.textContent = `Comments unavailable: ${error.message}`;
    }
}
function selectComment(reader, note, scroll = false) {
    state.selectedComment = note ? { reader, note } : null;
    if (note) revealCommentAnchor(reader, note, scroll);
    else clearCommentAnchor(reader);
    updateEditorOptions();
    // Picking a commented phrase in the document shows its comment in the right column;
    // the list marks the selection once it has loaded.
    if (note && state.detail !== "comments") {
        state.detail = "comments";
        switchDetail();
    } else markSelectedNote();
}
// Notes have no ids, so rows and selection are matched by content like updateComment does.
function markSelectedNote() {
    if (state.detail !== "comments") return;
    const selected = state.selectedComment?.reader === currentReader()
        ? JSON.stringify(state.selectedComment.note) : null;
    for (const row of document.querySelectorAll("#detail-content .note")) {
        const on = row.dataset.note === selected;
        row.classList.toggle("selected", on);
        if (on) row.scrollIntoView({ block: "nearest" });
    }
}

// Comments are saved at once, so a deleted one can be brought back for a few seconds.
const undoBar = el("span", "", "comment-undo");
const undoButton = el("button", "Undo");
undoBar.hidden = true;
undoBar.append(el("span", "Comment deleted."), undoButton);
$("#editor-hint").after(undoBar);
function clearUndo() {
    clearTimeout(state.undo?.timer);
    state.undo = null;
    undoBar.hidden = true;
}
function offerUndo(reader, note, index) {
    clearUndo();
    state.undo = { reader, note, index, timer: setTimeout(clearUndo, 10000) };
    undoBar.hidden = reader !== currentReader();
}
undoButton.onpointerdown = (event) => event.preventDefault();
undoButton.onclick = async () => {
    const { reader, note, index } = state.undo;
    clearUndo();
    try {
        const latest = await api(`/api/documents/${reader.doc.id}/notes`);
        const notes = Array.isArray(latest) ? latest : latest.notes;
        notes.splice(Math.min(index, notes.length), 0, note);
        await api(`/api/documents/${reader.doc.id}/notes`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(notes),
        });
        updateCommentIndicators(reader, notes);
        if (currentReader() === reader && state.detail === "comments") showComments();
    } catch (error) {
        report(`Could not restore comment: ${error.message}`);
    }
};

async function updateComment(reader, note, color) {
    clearUndo();
    try {
        const latest = await api(`/api/documents/${reader.doc.id}/notes`);
        const notes = Array.isArray(latest) ? latest : latest.notes;
        const index = notes.findIndex(
            (item) => JSON.stringify(item) === JSON.stringify(note),
        );
        if (index < 0)
            throw new Error("Comment changed; refresh and try again.");
        const updated = color ? { ...note, color } : null;
        if (updated) notes[index] = updated;
        else notes.splice(index, 1);
        await api(`/api/documents/${reader.doc.id}/notes`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(notes),
        });
        updateCommentIndicators(reader, notes);
        clearCommentAnchor(reader);
        if (state.selectedComment?.reader === reader) {
            state.selectedComment = updated ? { reader, note: updated } : null;
            if (updated) revealCommentAnchor(reader, updated, false);
        }
        if (!updated) offerUndo(reader, note, index);
        if (currentReader() === reader && state.detail === "comments") {
            await showComments();
        }
        updateEditorOptions();
    } catch (error) {
        report(`Could not update comment: ${error.message}`);
    }
}

function fillDraftHead(head) {
    head.replaceChildren(el("strong", `Comment · Page ${commentDraft.page}`));
    if (commentDraft.quote) head.append(el("blockquote", commentDraft.quote));
    else
        head.append(
            el(
                "p",
                "Page comment. Select text in the PDF to anchor it to a phrase.",
                "note-meta",
            ),
        );
}

// Only the text layer's own selection counts, so clicking into the textarea or the
// panels (which collapses it) keeps the last phrase instead of dropping the anchor.
function setDraftAnchor(selected) {
    const draft = commentDraft;
    if (!draft || !selected) return;
    if (selected.quote === draft.quote &&
        JSON.stringify(selected.anchor) === JSON.stringify(draft.anchor)) return;
    Object.assign(draft, {
        page: selected.page,
        quote: selected.quote,
        anchor: selected.anchor,
    });
    // Without scrolling: the reader just made this selection and is looking at it.
    revealCommentAnchor(draft.reader, { page: selected.page, anchor: selected.anchor }, false);
    const head = document.querySelector("#detail-content .comment-anchor");
    if (head) fillDraftHead(head);
}

let selectionTimer = 0;
document.addEventListener("selectionchange", () => {
    if (!commentDraft || !state.commentTool) return;
    clearTimeout(selectionTimer);
    selectionTimer = setTimeout(() => {
        if (commentDraft?.reader === currentReader() && state.commentTool)
            setDraftAnchor(captureCommentAnchor(commentDraft.reader));
    }, 150);
});

function comment() {
    const reader = currentReader();
    if (!reader?.pdf) return;
    const selected =
        pendingAnchor?.reader === reader
            ? pendingAnchor.anchor
            : captureCommentAnchor(reader);
    pendingAnchor = null;
    clearCommentAnchor(reader);
    state.selectedComment = null;
    commentDraft = {
        reader,
        page: reader.viewer.currentPageNumber,
        text: "",
    };
    setDraftAnchor(selected);
    // A PDF.js editor mode would turn the drag that picks the phrase into an annotation.
    state.commentTool = true;
    reader.tool = "select-tool";
    reader.color = null;
    reader.thickness = null;
    reader.setMode(pdfjs.AnnotationEditorType.NONE);
    updateEditorOptions();
    state.detail = "comments";
    setPanel("details", true);
    switchDetail();
}

function switchDetail() {
    $("#references-tab").classList.toggle(
        "current",
        state.detail === "references",
    );
    $("#comments-tab").classList.toggle("current", state.detail === "comments");
    if (state.detail === "references") renderReferences();
    else showComments();
}
const openSources = createSourcesDialog({
    api,
    onSaved: applyConfig,
    canLeave: () => !hasUnsavedEdits(),
});
$("#library-button").onclick = $("#welcome-library").onclick = library;
$("#search").oninput = () => listLibrary();
$("#files").onchange = (e) => importFiles(e.target.files);
$("#folder").onchange = (e) => importFiles(e.target.files);
for (const button of document.querySelectorAll("[data-close]"))
    button.onclick = () => $(`#${button.dataset.close}`).close();
$("#references-tab").onclick = () => {
    state.detail = "references";
    switchDetail();
};
$("#comments-tab").onclick = () => {
    state.detail = "comments";
    switchDetail();
};
function updateSelectionCount() {
    const count = document.querySelectorAll("#acquire-list input:checked:not(:disabled)").length;
    $("#selection-count").textContent = `${count} selected`;
    $("#download-selected").disabled = count === 0;
}
function saveSelection() {
    updateSelectionCount();
    localStorage.setItem(
        `paperassist-selection-${state.current.main.doc.id}`,
        JSON.stringify(
            [...document.querySelectorAll("#acquire-list input:checked:not(:disabled)")].map(
                (box) => box.value,
            ),
        ),
    );
}
$("#select-all").onclick = () => {
    for (const box of document.querySelectorAll(
        "#acquire-list input:not(:disabled)",
    ))
        box.checked = true;
    saveSelection();
};
$("#clear-selection").onclick = () => {
    for (const box of document.querySelectorAll("#acquire-list input:checked")) box.checked = false;
    saveSelection();
};
$("#download-selected").onclick = () => {
    const ids = [...document.querySelectorAll("#acquire-list input:checked:not(:disabled)")].map((box) => box.value);
    if (!ids.length) return;
    $("#acquire-dialog").close();
    runAcquisition(ids);
};
async function runAcquisition(ids, openId = null) {
    const tab = state.current;
    if (!tab || state.searching) return;
    const doc = tab.main.doc;
    state.searching = { id: openId, message: "Searching…" };
    if (state.detail === "references") renderReferences();
    const progress = $("#acquisition-status"), cancel = $("#cancel-acquisition");
    progress.hidden = false;
    cancel.hidden = false;
    try {
        const started = await api(`/api/documents/${doc.id}/references/download`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ids }),
        });
        cancel.onclick = async () => {
            cancel.disabled = true;
            await api(`/api/jobs/${started.id}/cancel`, { method: "POST" });
        };
        let cancelled = false;
        const result = await job(started.id, (j) => {
            cancelled = j.status === "cancelled";
            const p = j.progress || {};
            const provider = p.provider ? `Searching ${providerName(p.provider)}…` : "Searching…";
            progress.textContent = p.total ? `${provider} ${p.completed || 0} / ${p.total}` : provider;
            if (state.current === tab && openId) {
                state.searching = { id: openId, message: provider };
                if (state.detail === "references") renderReferences();
            }
        });
        const results = result?.references || [];
        const labels = { downloaded: "downloaded", local: "already in library", not_found: "no open-access PDF", not_identified: "not identified", ambiguous: "several matches", failed: "incomplete" };
        const counts = {};
        for (const item of results) counts[item.status] = (counts[item.status] || 0) + 1;
        const summary = Object.entries(counts).map(([key, total]) => `${total} ${key === "blocked" ? `PDF${total > 1 ? "s" : ""} to open in browser` : labels[key] || key}`);
        const skipped = Math.max(0, ids.length - results.length);
        if (skipped) summary.push(`${skipped} not searched`);
        progress.textContent = `${cancelled ? "Search cancelled" : "Search finished"}: ${summary.join(" · ") || "no results"}`;
        tab.acquisitionSummary = progress.textContent;
        if (state.current === tab) {
            await loadReferences();
            const ref = state.references.find((item) => item.id === openId);
            if (ref?.local_document_id) await openReference(ref);
        }
    } catch (error) {
        progress.textContent = `Search failed: ${error.message}`;
        tab.acquisitionSummary = progress.textContent;
        if (state.current === tab && openId) {
            const ref = state.references.find((item) => item.id === openId);
            if (ref) ref.retrieval = { version: 2, status: "failed", summary: `Search failed: ${error.message}` };
        }
    } finally {
        state.searching = null;
        cancel.hidden = true;
        cancel.disabled = false;
        if (state.current === tab && state.detail === "references") renderReferences();
    }
}

$("#save-pdf").onclick = () => saveReader(currentReader());
window.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveReader(currentReader());
    }
});
// A plain click inside a commented phrase selects it, like clicking a PDF.js highlight.
// Drags are left alone so the phrase can still be selected as text.
document.addEventListener("click", (event) => {
    const reader = currentReader();
    if (!reader || reader.readOnly || commentDraft?.reader === reader ||
        !reader.container.contains(event.target) ||
        reader.viewer.annotationEditorMode !== pdfjs.AnnotationEditorType.NONE ||
        reader.selection?.hasSelection) return;
    const picked = window.getSelection().isCollapsed ? commentAtClient(reader, event) : null;
    if (picked) selectComment(reader, picked);
    else if (state.selectedComment) selectComment(reader, null);
});
window.addEventListener("keydown", (event) => {
    const reader = currentReader();
    if (!state.selectedComment || state.selectedComment.reader !== reader ||
        event.ctrlKey || event.metaKey || event.altKey ||
        event.target.closest?.("input, textarea, select, [contenteditable]") ||
        document.querySelector("dialog[open]")) return;
    if (event.key === "Escape") selectComment(reader, null);
    else if (["Delete", "Backspace"].includes(event.key) && !reader.selection?.hasSelection) {
        event.preventDefault();
        updateComment(reader, state.selectedComment.note, null);
    }
});
function hasUnsavedEdits() {
    return state.tabs.some((tab) => [tab.main, tab.preview].some(
        (reader) => reader && (reader.dirty || reader.saving),
    ));
}
window.addEventListener("beforeunload", (event) => {
    if (hasUnsavedEdits()) {
        event.preventDefault();
        event.returnValue = "";
    }
});
const zoom = (change) => {
    const reader = currentReader();
    if (!reader) return;
    reader.zoomed = true;
    change(reader.viewer);
};
$("#zoom-in").onclick = () => zoom((viewer) => viewer.increaseScale());
$("#zoom-out").onclick = () => zoom((viewer) => viewer.decreaseScale());
$("#zoom-label").onclick = () => {
    if (currentReader())
        currentReader().viewer.currentScaleValue = "page-width";
};
const editorModes = {
    "select-tool": pdfjs.AnnotationEditorType.NONE,
    "ink-tool": pdfjs.AnnotationEditorType.INK,
    "highlight-tool": pdfjs.AnnotationEditorType.HIGHLIGHT,
};
const colors = [
    ["Yellow", "#f5d76e"],
    ["Green", "#8fd3aa"],
    ["Blue", "#79c5df"],
    ["Pink", "#e9a9bc"],
    ["Dark green", "#243f3a"],
];
// The row never leaves the toolbar, so the workspace below keeps its position;
// only the controls that apply to the active tool are rendered.
const HINTS = {
    none: "Open a paper to use the tools",
    readOnly: "Read-only preview — annotation tools are off",
    select: "Click a drawing, highlight or comment to change its color or delete it",
    comment: "Select the text to comment on in the PDF, then write in the Comments panel",
};
function updateEditorOptions() {
    const reader = currentReader();
    undoBar.hidden = !state.undo || state.undo.reader !== reader;
    const tool = reader?.tool || "select-tool";
    const selection = reader?.selection;
    const selectedComment =
        state.selectedComment?.reader === reader ? state.selectedComment : null;
    const selectedType = selection?.hasSelection ? selection.type : null;
    const commenting = !!reader && state.commentTool && commentDraft?.reader === reader;
    const inactive = !reader || reader.readOnly;
    const drawing = !inactive && !commenting && tool !== "select-tool";
    const selected = !inactive && !commenting && (!!selectedComment || !!selectedType);
    state.tool = tool;
    for (const id of Object.keys(editorModes))
        $(`#${id}`).classList.toggle("selected", id === tool && !commenting);
    $("#comment-tool").classList.toggle("selected", commenting);
    const label = commenting ? "Comment"
        : selectedComment ? "Selected comment"
        : selectedType ? `Selected ${selectedType}`
        : tool === "ink-tool" ? "Draw"
        : tool === "highlight-tool" ? "Highlight"
        : "Select";
    $("#editor-options-label").textContent = inactive ? "" : label;
    $("#editor-options-label").hidden = inactive;
    const hint = !reader ? HINTS.none
        : reader.readOnly ? HINTS.readOnly
        : commenting ? HINTS.comment
        : !drawing && !selected ? HINTS.select
        : "";
    $("#editor-hint").textContent = hint;
    $("#editor-hint").hidden = !hint;
    const colors = drawing || selected;
    $("#color-label").hidden = $("#color-options").hidden = !colors;
    $(".thickness").hidden = !(drawing || (selected && !selectedComment));
    $("#visibility-option").hidden =
        inactive || commenting || tool === "ink-tool" || !!selectedComment || (!!selectedType && selectedType !== "highlight");
    $("#delete-annotation").hidden = !(drawing || selected);
    $("#editor-thickness").disabled = tool === "select-tool";
    $("#editor-thickness").value =
        reader?.thickness || (tool === "ink-tool" ? 2 : 12);
    $("#delete-annotation").disabled =
        !selectedComment && !selection?.canDelete;
    const color = (
        selectedComment?.note.color ||
        selection?.color ||
        reader?.color ||
        ""
    ).toLowerCase();
    for (const chip of $("#color-options").children) {
        chip.disabled =
            !selectedComment &&
            !!selection?.hasSelection &&
            !["ink", "highlight"].includes(selectedType);
        chip.classList.toggle(
            "selected",
            chip.dataset.color.toLowerCase() === color,
        );
    }
}

function setParam(type, value) {
    const reader = currentReader();
    reader?.bus.dispatch("switchannotationeditorparams", {
        source: reader,
        type,
        value,
    });
}
for (const [name, color] of colors) {
    const chip = el("button", "", "color-chip");
    chip.style.backgroundColor = color;
    chip.dataset.color = color;
    chip.title = name;
    chip.setAttribute("aria-label", `${name} annotation color`);
    chip.onpointerdown = (event) => event.preventDefault();
    chip.onclick = () => {
        const reader = currentReader();
        if (!reader) return;
        reader.color = color;
        if (state.selectedComment?.reader === reader) {
            updateComment(reader, state.selectedComment.note, color);
            return;
        }
        if (reader.selection?.hasSelection) {
            reader.setAnnotationColor(color);
            updateEditorOptions();
            return;
        }
        setParam(
            state.tool === "ink-tool"
                ? pdfjs.AnnotationEditorParamsType.INK_COLOR
                : pdfjs.AnnotationEditorParamsType.HIGHLIGHT_COLOR,
            color,
        );
        updateEditorOptions();
    };
    $("#color-options").append(chip);
}
$("#editor-thickness").oninput = (event) => {
    const reader = currentReader();
    if (!reader) return;
    reader.thickness = Number(event.target.value);
    setParam(
        state.tool === "ink-tool"
            ? pdfjs.AnnotationEditorParamsType.INK_THICKNESS
            : pdfjs.AnnotationEditorParamsType.HIGHLIGHT_THICKNESS,
        reader.thickness,
    );
};
$("#show-highlights").onchange = (event) =>
    setParam(
        pdfjs.AnnotationEditorParamsType.HIGHLIGHT_SHOW_ALL,
        event.target.checked,
    );
$("#delete-annotation").onpointerdown = (event) => event.preventDefault();
$("#delete-annotation").onclick = () => {
    const reader = currentReader();
    if (state.selectedComment?.reader === reader)
        updateComment(reader, state.selectedComment.note, null);
    else reader?.deleteSelectedAnnotation();
};
for (const [id, mode] of Object.entries(editorModes))
    $(`#${id}`).onclick = () => {
        const reader = currentReader();
        if (!reader) return;
        state.commentTool = false;
        if (state.selectedComment) selectComment(reader, null);
        reader.tool = id;
        reader.color = null;
        reader.thickness = null;
        reader.setMode(mode);
        updateEditorOptions();
    };
$("#comment-tool").onpointerdown = () => {
    const reader = currentReader();
    pendingAnchor = reader
        ? { reader, anchor: captureCommentAnchor(reader) }
        : null;
};
$("#comment-tool").onclick = comment;
const panelNames = ["outline", "details"];
function setPanel(name, expanded) {
    const workspace = $(".workspace");
    workspace.classList.toggle(`${name}-collapsed`, !expanded);
    try {
        localStorage.setItem(`paperassist-${name}-expanded`, String(expanded));
    } catch {}
    // One toolbar button serves both columns, so it reports whether both are visible.
    const both = panelNames.every(
        (panel) => !workspace.classList.contains(`${panel}-collapsed`),
    );
    const button = $("#panels-button");
    const label = both ? "Hide side panels" : "Show side panels";
    button.setAttribute("aria-expanded", String(both));
    button.setAttribute("aria-label", label);
    button.title = label;
}
for (const name of panelNames) {
    let saved = null;
    try {
        saved = localStorage.getItem(`paperassist-${name}-expanded`);
    } catch {}
    setPanel(name, saved === null ? true : saved === "true");
}
$("#panels-button").onclick = () => {
    const show = $("#panels-button").getAttribute("aria-expanded") !== "true";
    for (const name of panelNames) setPanel(name, show);
};
$("#outline-toggle").onclick = () => setPanel("outline", false);
$("#details-toggle").onclick = () => setPanel("details", false);
$("#previous-page").onclick = () => {
    const reader = currentReader();
    if (reader)
        reader.viewer.currentPageNumber = Math.max(
            1,
            reader.viewer.currentPageNumber - 1,
        );
};
$("#next-page").onclick = () => {
    const reader = currentReader();
    if (reader?.pdf)
        reader.viewer.currentPageNumber = Math.min(
            reader.pdf.numPages,
            reader.viewer.currentPageNumber + 1,
        );
};
$("#page-input").onchange = (event) => {
    const reader = currentReader();
    if (reader?.pdf)
        reader.viewer.currentPageNumber = Math.max(
            1,
            Math.min(reader.pdf.numPages, Number(event.target.value) || 1),
        );
};
function find(type = "", backwards = false) {
    const reader = currentReader();
    if (!reader) return;
    reader.bus.dispatch("find", {
        source: reader,
        type,
        query: $("#find-input").value,
        phraseSearch: true,
        highlightAll: true,
        findPrevious: backwards,
        caseSensitive: false,
        entireWord: false,
        matchDiacritics: false,
    });
}
$("#find-input").oninput = () => find();
$("#find-input").onkeydown = (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        find("again", event.shiftKey);
    }
};
$("#find-next").onclick = () => find("again");
$("#find-previous").onclick = () => find("again", true);
function applyConfig(config) {
    state.providers = config.providers;
    state.proxy = config.proxy || "";
    state.inbox = config.inbox || "";
    if ($("#acquire-dialog").open) renderAcquire();
    // Without a paper the panel keeps its "Open a paper" message.
    if (state.current && state.detail === "references") renderReferences();
}
refreshToolbar();
api("/api/config")
    .then((config) => {
        state.token = config.token;
        applyConfig(config);
    })
    .catch((error) => report(`Could not connect: ${error.message}`));
installShortcuts({ currentReader });
