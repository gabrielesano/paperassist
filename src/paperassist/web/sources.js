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
// Why the location cannot be changed from here when the user decided it at start-up.
const FIXED_LOCATION = {
    option: "Set by --data-dir when PaperAssist starts.",
    environment: "Set by PAPERASSIST_DATA_DIR.",
};
const post = (api, path, values) =>
    api(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(values) });

// The server restarts on the same port after a switch, so the first requests may fail.
async function waitForLibrary(api, path) {
    for (let attempt = 0; attempt < 60; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        try {
            if ((await api("/api/config")).library_path === path) return;
        } catch {}
    }
    throw new Error("The new library did not start in time. Reload the page.");
}

function sourceStatus(source, view, providers, enabled) {
    if (!enabled) return "Turned off";
    const key = view.keys[source.id];
    if (source.key === "optional") return key?.configured ? `Optional key set …${key.hint}` : "Ready";
    if (source.key) return key.configured ? `Ready · ${source.noun} set …${key.hint}` : `Needs a ${source.noun}`;
    return providers[source.id]?.configured ? "Ready" : "Needs an email";
}

// `onSaved(config)` lets the page refresh what it derives from the settings;
// `canLeave()` is false while open papers hold unsaved edits.
export function createSourcesDialog({ api, onSaved, canLeave = () => true }) {
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

    // The location is not part of Save: it belongs to the user, not to one library's settings.json.
    let plan = null;
    const confirmBox = $("#sources-library-confirm");
    const locationButtons = ["browse", "use", "yes", "no"].map((id) => $(`#sources-library-${id}`));
    const setLocationBusy = (busy) => locationButtons.forEach((button) => (button.disabled = busy));

    function closeConfirm() {
        plan = null;
        confirmBox.hidden = true;
    }

    function showLocation(config) {
        const reason = FIXED_LOCATION[config.library_source];
        field("library").value = config.library_path;
        field("library").disabled = Boolean(reason);
        setLocationBusy(Boolean(reason));
        $("#sources-library-note").textContent = reason ?? "PaperAssist remembers this choice for your user account.";
        closeConfirm();
    }

    async function refresh() {
        const [settings, config] = await Promise.all([api("/api/settings"), api("/api/config")]);
        view = settings;
        for (const name of ["email", "proxy", "inbox"]) field(name).value = view[name];
        showLocation(config);
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

    $("#sources-library-browse").onclick = async () => {
        setLocationBusy(true);
        closeConfirm();
        say("Choose a folder in the window that opened.");
        try {
            const { path } = await post(api, "/api/choose-folder", {
                start: field("library").value.trim(),
                title: "Library folder",
            });
            if (path) field("library").value = path;
            say(path ? 'Folder selected. Choose "Use this folder" to continue.' : "");
        } catch (error) {
            say(error.message, true);
        } finally {
            setLocationBusy(false);
        }
    };
    field("library").oninput = closeConfirm;
    field("library").onkeydown = (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        $("#sources-library-use").click();
    };

    // Ask the server what the folder means first, so the user confirms a concrete outcome.
    $("#sources-library-use").onclick = async () => {
        say("");
        if (!canLeave()) return say("Save or discard changes in open papers first.", true);
        if (Object.keys(changes()).length) return say("Save the other changes in this dialog first.", true);
        try {
            plan = await post(api, "/api/library/inspect", { path: field("library").value });
        } catch (error) {
            return say(error.message, true);
        }
        const actionable = ["open", "move"].includes(plan.action);
        $("#sources-library-question").textContent = plan.message;
        confirmBox.classList.toggle("refused", !actionable);
        $("#sources-library-yes").hidden = !actionable;
        $("#sources-library-yes").textContent = plan.action === "move" ? "Move library" : "Open library";
        $("#sources-library-no").textContent = actionable ? "Cancel" : "OK";
        confirmBox.hidden = false;
        confirmBox.scrollIntoView({ block: "nearest" });
        (actionable ? $("#sources-library-yes") : $("#sources-library-no")).focus();
    };
    $("#sources-library-no").onclick = closeConfirm;
    $("#sources-library-yes").onclick = async () => {
        if (!canLeave()) {
            closeConfirm();
            return say("Save or discard changes in open papers first.", true);
        }
        const { action } = plan;
        setLocationBusy(true);
        say(action === "move" ? "Copying and checking the library…" : "Opening the library…");
        try {
            const { path } = await post(api, "/api/library/switch", { path: field("library").value, action });
            say("Restarting with the new library…");
            await waitForLibrary(api, path);
            location.reload();
        } catch (error) {
            say(error.message, true);
            setLocationBusy(false);
            closeConfirm();
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
