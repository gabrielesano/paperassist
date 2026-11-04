// The Sources dialog: which services PaperAssist may ask, and the access data they need.
// Keys are write-only here: the server returns only the last characters of a stored key.

// `key` names what the source cannot run without ("token", "key"), or "optional" when a
// key only raises rate limits.
export const SOURCES = [
    { id: "crossref", name: "Crossref", help: "Open metadata and publisher links. The contact email puts requests in its polite pool." },
    { id: "arxiv", name: "arXiv", help: "Preprints. No setup needed." },
    { id: "openalex", name: "OpenAlex", key: "optional", help: "An optional key raises the rate limit." },
    {
        id: "semantic_scholar", name: "Semantic Scholar", key: "optional",
        help: "An optional key raises the rate limit.",
        link: ["https://www.semanticscholar.org/product/api", "Request a key"],
    },
    {
        id: "ads", name: "NASA ADS", key: "token", noun: "token",
        help: "Astrophysics and physics records. Needs a free token.",
        link: ["https://ui.adsabs.harvard.edu/user/settings/token", "Get a token"],
    },
    {
        id: "core", name: "CORE", key: "key", noun: "key",
        help: "Open-access repository copies. Needs a free key.",
        link: ["https://core.ac.uk/services/api", "Get a key"],
    },
    { id: "unpaywall", name: "Unpaywall", help: "Open-access copies found by DOI. Needs the contact email above." },
];
export const SOURCE_NAMES = Object.fromEntries(SOURCES.map((source) => [source.id, source.name]));

// The proxy only serves links the reader opens in a browser, never automatic downloads.
// Only web schemes may become links, because both the template and the target are data.
export function proxyUrl(template, target) {
    if (!template || !target) return null;
    const href = template.includes("{url}")
        ? template.replaceAll("{url}", encodeURIComponent(target))
        : template + target;
    try {
        return [href, target].every((value) => ["http:", "https:"].includes(new URL(value).protocol)) ? href : null;
    } catch {
        return null;
    }
}

const $ = (selector) => document.querySelector(selector);
const element = (tag, text, className) => {
    const node = document.createElement(tag);
    node.textContent = text ?? "";
    if (className) node.className = className;
    return node;
};
const JSON_HEADERS = { "Content-Type": "application/json" };

function sourceStatus(source, view, providers, enabled) {
    if (!enabled) return "Turned off";
    const key = view.keys[source.id];
    if (source.key === "optional") return key?.configured ? `Optional key set …${key.hint}` : "Ready";
    if (source.key) return key.configured ? `Ready · ${source.noun} set …${key.hint}` : `Needs a ${source.noun}`;
    return providers[source.id]?.configured ? "Ready" : "Needs an email";
}

// `onSaved(config)` lets the page refresh what it derives from the settings.
export function createSourcesDialog({ api, onSaved }) {
    const dialog = $("#sources-dialog"), message = $("#sources-message");
    let view = null, rows = [];

    const say = (text, bad = false) => {
        message.textContent = text;
        message.classList.toggle("error", bad);
    };
    const field = (name) => $(`#sources-${name}`);

    function renderRow(source, providers) {
        const row = element("div", "", "source-row");
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.setAttribute("role", "switch");
        toggle.checked = view.enabled[source.id];
        const label = element("label", "", "source-name");
        label.append(toggle, element("span", source.name));
        const status = element("span", "", "source-status");
        const refreshStatus = () => {
            status.textContent = sourceStatus(source, view, providers, toggle.checked);
        };
        toggle.onchange = refreshStatus;
        refreshStatus();
        row.append(label, status);

        const entry = { source, toggle, input: null, removing: false };
        if (source.key) {
            const noun = source.noun || "key";
            const input = document.createElement("input");
            // A password input makes browsers treat the dialog as a login form: they offer
            // to save the key and show autofill popups (one crashes Brave). Mask a plain
            // text input instead, and fall back to password where CSS masking is missing.
            const masked = CSS.supports("-webkit-text-security", "disc");
            input.type = masked ? "text" : "password";
            input.classList.add("secret");
            input.autocomplete = "off";
            input.autocapitalize = "off";
            input.spellcheck = false;
            input.placeholder = view.keys[source.id].configured ? `Paste a new ${noun} to replace it` : `Paste your ${noun}`;
            input.setAttribute("aria-label", `${source.name} ${noun}`);
            entry.input = input;
            const keyField = element("div", "", "key-field");
            keyField.append(input);
            row.append(keyField);
            if (view.keys[source.id].configured) {
                const remove = element("button", "Remove", "link-button");
                remove.type = "button";
                // Removal is applied by Save like every other change.
                remove.onclick = () => {
                    entry.removing = !entry.removing;
                    input.disabled = entry.removing;
                    input.value = "";
                    remove.textContent = entry.removing ? "Keep" : "Remove";
                    status.textContent = entry.removing ? `The ${noun} will be removed when you save` : sourceStatus(source, view, providers, toggle.checked);
                };
                keyField.append(remove);
            }
        }
        const help = element("p", `${source.help} `, "source-help");
        if (source.link) {
            const link = element("a", source.link[1]);
            link.href = source.link[0];
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            help.append(link);
        }
        entry.result = element("p", "", "source-result");
        entry.result.setAttribute("aria-live", "polite");
        row.append(help, entry.result);
        entry.row = row;
        return entry;
    }

    async function refresh() {
        const [settings, config] = await Promise.all([api("/api/settings"), api("/api/config")]);
        view = settings;
        for (const name of ["email", "proxy", "inbox"]) field(name).value = view[name];
        $("#sources-library").textContent = config.library_path;
        rows = SOURCES.map((source) => renderRow(source, config.providers));
        $("#sources-list").replaceChildren(...rows.map((entry) => entry.row));
        return config;
    }

    // Only changed fields are sent; the server treats omitted ones as unchanged.
    function changes() {
        const found = {};
        for (const name of ["email", "proxy", "inbox"])
            if (field(name).value.trim() !== view[name]) found[name] = field(name).value.trim() || null;
        for (const { source, toggle, input, removing } of rows) {
            if (toggle.checked !== view.enabled[source.id]) (found.enabled ??= {})[source.id] = toggle.checked;
            const typed = input?.value.trim();
            if (removing) (found.keys ??= {})[source.id] = null;
            else if (typed) (found.keys ??= {})[source.id] = typed;
        }
        return found;
    }

    $("#sources-form").onsubmit = async (event) => {
        event.preventDefault();
        const pending = changes();
        if (!Object.keys(pending).length) return say("Nothing to save.");
        say("Saving…");
        try {
            await api("/api/settings", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(pending) });
            onSaved(await refresh());
            say("Saved.");
        } catch (error) {
            say(error.message, true);
        }
    };
    $("#sources-check").onclick = async () => {
        if (Object.keys(changes()).length) return say("Save your changes first; the check uses saved settings.", true);
        const button = $("#sources-check");
        button.disabled = true;
        say("Checking access…");
        try {
            const results = await api("/api/settings/check", { method: "POST" });
            for (const { source, result } of rows) {
                const outcome = results[source.id];
                result.textContent = outcome ? `${outcome.ok ? "✓" : "✗"} ${outcome.message}` : "";
                result.classList.toggle("error", outcome?.ok === false);
            }
            say("");
        } catch (error) {
            say(error.message, true);
        } finally {
            button.disabled = false;
        }
    };

    // The browser cannot learn a folder's absolute path, so the local server opens the
    // system chooser. The path only fills the field; Save stays the explicit step.
    $("#sources-browse").onclick = async () => {
        const button = $("#sources-browse");
        button.disabled = true;
        say("Choose a folder in the window that opened.");
        try {
            const { path } = await api("/api/choose-folder", {
                method: "POST",
                headers: JSON_HEADERS,
                body: JSON.stringify({ start: field("inbox").value.trim() }),
            });
            if (path) field("inbox").value = path;
            say(path ? "Folder selected. Save to keep it." : "");
        } catch (error) {
            say(error.message, true);
        } finally {
            button.disabled = false;
        }
    };

    return async function open(focus = "email") {
        say("");
        dialog.showModal();
        try {
            await refresh();
            field(focus).focus();
        } catch (error) {
            say(`Settings unavailable: ${error.message}`, true);
        }
    };
}
