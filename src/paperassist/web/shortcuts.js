// One table drives both the key handling and the help dialog, so the list cannot drift
// from the behaviour. Entries without `match` document keys that the app, the browser
// or PDF.js already handle.
//
// Keys are compared by `event.key`, which follows the keyboard layout: the Italian layout
// needs AltGr for brackets, so the table sticks to letters, digits, `,` `.` `?` and arrows.
// Browser-reserved combinations (Ctrl+T/N/W/Tab/PageUp/PageDown/L/D/H/J, Alt+arrows) are avoided.

const $ = (selector) => document.querySelector(selector);
const NO_EDITOR_MODE = 0; // pdfjs.AnnotationEditorType.NONE, kept literal so this file needs no PDF.js

const lower = (event) => event.key.toLowerCase();
const modifierFree = (event) => !event.ctrlKey && !event.metaKey && !event.altKey;
// A plain key ignores Shift, so Caps Lock and the Shift needed for "?" do not matter.
const plain = (...keys) => (event) => modifierFree(event) && keys.includes(lower(event));
// Ctrl on most systems, Command on macOS; the other platform's key is not special.
const mod = (...keys) => (event) =>
    (event.ctrlKey || event.metaKey) && !event.altKey && keys.includes(lower(event));
const isRedo = (event) =>
    (mod("z")(event) && event.shiftKey) || (event.ctrlKey && !event.metaKey && lower(event) === "y");

const click = (selector) => () => $(selector).click();
// The toolbar has one control group per paper; keys use the group of the pane last clicked.
export const controlId = (reader, name) => (reader?.kind === "preview" ? `second-${name}` : name);
const control = (reader, name) => $(`#${controlId(reader, name)}`);
// Page controls act on the paper, so without one they decline and the key keeps its default.
const withReader = (action) => (app, event) => {
    const reader = app.currentReader();
    return reader?.pdf ? action(reader, event) : false;
};
const clickControl = (name) => withReader((reader) => control(reader, name).click());
const focusControl = (name) => withReader((reader) => {
    control(reader, name).focus();
    control(reader, name).select();
});
// A field returns to the paper its own group drives, wherever the last click was.
const fieldReader = (app, event) =>
    event.target.closest?.("#second-paper") ? app.previewReader() : app.mainReader();

function focusDocument(reader) {
    // Scroll keys need a focused scroller; the viewer is not focusable by itself.
    reader.container.tabIndex = -1;
    reader.container.focus({ preventScroll: true });
}

function showPanel(selector) {
    if ($("#panels-button").getAttribute("aria-expanded") !== "true") $("#panels-button").click();
    $(selector).tabIndex = -1;
    $(selector).focus();
}

function stepTab(step) {
    const titles = [...document.querySelectorAll("#tabs .tab-title")];
    const target = titles[titles.findIndex((title) => title.ariaCurrent === "page") + step];
    if (!target) return false;
    target.click();
}

function editingAction(name) {
    return (app, event) => {
        const reader = app.currentReader();
        if (!reader || reader.readOnly) return false;
        const restore = $(".comment-undo:not([hidden]) button");
        if (name === "undo" && restore) {
            restore.click();
            // PDF.js listens on the window too and would also undo an annotation.
            event.stopImmediatePropagation();
            return;
        }
        // Outside Select mode PDF.js handles these keys itself; dispatching would undo twice.
        if (reader.viewer.annotationEditorMode !== NO_EDITOR_MODE) return false;
        reader.bus.dispatch("editingaction", { source: reader, name });
    };
}

// `anywhere` lets an entry work while a text field has focus.
export const shortcuts = [
    { group: "General", keys: [["?"]], label: "Show keyboard shortcuts", match: plain("?"),
        run: click("#shortcuts-button") },
    { group: "General", keys: [["Mod", "S"]], label: "Save the PDF" },
    { group: "General", keys: [["L"]], label: "Open the library", match: plain("l"),
        run: click("#library-button") },
    { group: "General", keys: [["Esc"]], label: "Close a dialog" },
    { group: "General", keys: [["Esc"]], label: "Return from Find or Page to the document",
        anywhere: true,
        match: (event) => event.key === "Escape" && event.target.closest?.(".toolbar input"),
        run: (app, event) => {
            const reader = fieldReader(app, event);
            if (!reader?.pdf) return false;
            // Chrome would also clear a search field on Escape; the query stays for next/previous.
            event.preventDefault();
            focusDocument(reader);
        } },

    { group: "Navigation", keys: [["N"], ["J"]], label: "Next page", match: plain("n", "j"),
        run: clickControl("next-page") },
    { group: "Navigation", keys: [["P"], ["K"]], label: "Previous page", match: plain("p", "k"),
        run: clickControl("previous-page") },
    { group: "Navigation", keys: [["Home"]], label: "First page", match: plain("home"),
        run: withReader((reader) => { reader.viewer.currentPageNumber = 1; }) },
    { group: "Navigation", keys: [["End"]], label: "Last page", match: plain("end"),
        run: withReader((reader) => { reader.viewer.currentPageNumber = reader.pdf.numPages; }) },
    { group: "Navigation", keys: [["G"]], label: "Go to page: type a number, then Enter",
        match: plain("g"), run: focusControl("page-input") },
    // Not listed: lets the arrow and Space keys scroll right after choosing a page.
    { group: "Navigation", anywhere: true,
        match: (event) => event.key === "Enter" && event.target.id?.endsWith("page-input"),
        run: (app, event) => {
            const reader = fieldReader(app, event);
            return reader?.pdf ? focusDocument(reader) : false;
        } },
    { group: "Navigation", keys: [["↑"], ["↓"]], label: "Scroll the document (click it, or Esc in a field)" },
    { group: "Navigation", keys: [["Space"], ["Shift", "Space"]], label: "Scroll a screen down or up" },
    { group: "Navigation", keys: [["Page Up"], ["Page Down"]], label: "Scroll a screen up or down" },

    { group: "Find", keys: [["Mod", "F"]], label: "Find in the paper you last clicked", anywhere: true,
        match: mod("f"), run: focusControl("find-input") },
    { group: "Find", keys: [["Enter"], ["Shift", "Enter"]], label: "Next or previous match" },

    { group: "Zoom", keys: [["Mod", "+"], ["Mod", "="]], label: "Zoom in", anywhere: true,
        match: mod("+", "="), run: clickControl("zoom-in") },
    { group: "Zoom", keys: [["Mod", "-"]], label: "Zoom out", anywhere: true,
        match: mod("-"), run: clickControl("zoom-out") },
    { group: "Zoom", keys: [["Mod", "0"]], label: "Fit the page width", anywhere: true,
        match: mod("0"), run: clickControl("zoom-label") },

    { group: "Annotations", keys: [["V"]], label: "Select tool", match: plain("v"),
        run: click("#select-tool") },
    { group: "Annotations", keys: [["D"]], label: "Draw tool", match: plain("d"),
        run: click("#ink-tool") },
    { group: "Annotations", keys: [["H"]], label: "Highlight tool", match: plain("h"),
        run: click("#highlight-tool") },
    { group: "Annotations", keys: [["C"]], label: "Comment on the selected text", match: plain("c"),
        run: click("#comment-tool") },
    { group: "Annotations", keys: [["Delete"], ["Backspace"]],
        label: "Delete the selected annotation or comment" },
    { group: "Annotations", keys: [["Mod", "Z"]], label: "Undo", match: (event) => mod("z")(event) && !event.shiftKey,
        run: editingAction("undo") },
    { group: "Annotations", keys: [["Mod", "Shift", "Z"], ["Ctrl", "Y"]], label: "Redo",
        match: isRedo, run: editingAction("redo") },

    { group: "Tabs and panels", keys: [[","], ["."]], label: "Previous or next paper tab",
        match: plain(",", "."), run: (app, event) => stepTab(event.key === "," ? -1 : 1) },
    { group: "Tabs and panels", keys: [["X"]], label: "Close the paper tab",
        match: plain("x"), run: () => {
            const close = $("#tabs .tab.current .tab-close");
            if (!close) return false;
            close.click();
        } },
    { group: "Tabs and panels", keys: [["B"]], label: "Show or hide the side panels", match: plain("b"),
        run: click("#panels-button") },
    { group: "Tabs and panels", keys: [["O"]], label: "Go to the outline", match: plain("o"),
        run: () => showPanel("#outline") },
    { group: "Tabs and panels", keys: [["1"]], label: "Show the references", match: plain("1"),
        run: () => { $("#references-tab").click(); showPanel("#detail-content"); } },
    { group: "Tabs and panels", keys: [["2"]], label: "Show the comments", match: plain("2"),
        run: () => { $("#comments-tab").click(); showPanel("#detail-content"); } },
    { group: "Lists and dialogs", keys: [["Tab"], ["Shift", "Tab"]], label: "Move between controls and list items" },
    { group: "Lists and dialogs", keys: [["Enter"], ["Space"]], label: "Open the focused item" },
];

// First matching entry that may run for this event. `typing` is true while a text field has focus.
export function shortcutFor(event, { typing = false, dialogOpen = false } = {}) {
    if (event.defaultPrevented || event.isComposing || dialogOpen) return null;
    return shortcuts.find((entry) =>
        entry.match?.(event) && (entry.anywhere || !typing)) ?? null;
}

const isMac = () => /Mac|iPhone|iPad/.test(globalThis.navigator?.platform ?? "");

export function renderShortcuts(host) {
    const names = { Mod: isMac() ? "⌘" : "Ctrl" };
    const groups = Map.groupBy(shortcuts.filter((entry) => entry.label), (entry) => entry.group);
    for (const [name, entries] of groups) {
        const section = document.createElement("section");
        const heading = document.createElement("h3");
        heading.textContent = name;
        const list = document.createElement("dl");
        for (const entry of entries) {
            const row = document.createElement("div");
            const term = document.createElement("dt");
            entry.keys.forEach((combo, index) => {
                if (index) term.append(" or ");
                combo.forEach((key, position) => {
                    if (position) term.append("+");
                    const kbd = document.createElement("kbd");
                    kbd.textContent = names[key] ?? key;
                    term.append(kbd);
                });
            });
            const description = document.createElement("dd");
            description.textContent = entry.label;
            row.append(term, description);
            list.append(row);
        }
        section.append(heading, list);
        host.append(section);
    }
}

export function installShortcuts(app) {
    renderShortcuts($("#shortcut-groups"));
    $("#shortcuts-button").onclick = () => $("#shortcuts-dialog").showModal();
    window.addEventListener("keydown", (event) => {
        const entry = shortcutFor(event, {
            typing: Boolean(event.target.closest?.("input, textarea, select") || event.target.isContentEditable),
            dialogOpen: Boolean($("dialog[open]")),
        });
        // A run that returns false declined the key, so the browser keeps it.
        if (entry && entry.run(app, event) !== false) event.preventDefault();
    });
}
