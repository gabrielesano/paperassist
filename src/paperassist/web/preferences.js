// The Preferences dialog: the size of the app chrome and the two folders PaperAssist uses.

const ZOOM_KEY = "paperassist-ui-zoom";
export const ZOOM_CHOICES = [0.8, 0.9, 1, 1.1, 1.25, 1.5];

// Only listed sizes are accepted, so a stale or hand-edited value cannot break the layout.
export function parseZoom(stored) {
    const value = Number(stored);
    return ZOOM_CHOICES.includes(value) ? value : 1;
}

export function readZoom() {
    try {
        return parseZoom(localStorage.getItem(ZOOM_KEY));
    } catch {
        return 1;
    }
}

// The stylesheet scales the toolbar, tabs, side panels and dialogs by this property and
// leaves the PDF viewers alone: PDF.js geometry must not be distorted by an ancestor.
export function applyZoom(value = readZoom()) {
    document.documentElement.style.setProperty("--ui-zoom", String(value));
}

const $ = (selector) => document.querySelector(selector);
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

// `onSaved(config)` lets the page refresh what it derives from the settings;
// `canLeave()` is false while open papers hold unsaved edits.
export function createPreferencesDialog({ api, onSaved, canLeave = () => true }) {
    const dialog = $("#preferences-dialog"), message = $("#preferences-message");
    const size = $("#preferences-zoom");
    let savedInbox = "";

    const say = (text, bad = false) => {
        message.textContent = text;
        message.classList.toggle("error", bad);
    };
    const field = (name) => $(`#preferences-${name}`);

    size.replaceChildren(...ZOOM_CHOICES.map((value) => new Option(`${Math.round(value * 100)}%`, String(value))));
    // A size applies at once; it is a view preference, not part of a library's settings.
    size.onchange = () => {
        const value = parseZoom(size.value);
        applyZoom(value);
        try {
            localStorage.setItem(ZOOM_KEY, String(value));
        } catch {}
    };

    // The location is not part of Save: it belongs to the user, not to one library's settings.json.
    let plan = null;
    const confirmBox = $("#preferences-library-confirm");
    const locationButtons = ["browse", "use", "yes", "no"].map((id) => $(`#preferences-library-${id}`));
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
        $("#preferences-library-note").textContent = reason ?? "PaperAssist remembers this choice for your user account.";
        closeConfirm();
    }

    async function refresh() {
        const [settings, config] = await Promise.all([api("/api/settings"), api("/api/config")]);
        savedInbox = settings.inbox;
        field("inbox").value = savedInbox;
        showLocation(config);
        return config;
    }

    $("#preferences-form").onsubmit = async (event) => {
        event.preventDefault();
        const inbox = field("inbox").value.trim();
        if (inbox === savedInbox) return say("Nothing to save.");
        say("Saving…");
        try {
            // The server treats omitted settings as unchanged and an empty folder as cleared.
            await api("/api/settings", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ inbox: inbox || null }) });
            onSaved(await refresh());
            say("Saved.");
        } catch (error) {
            say(error.message, true);
        }
    };

    // The browser cannot learn a folder's absolute path, so the local server opens the
    // system chooser. The path only fills the field; Save stays the explicit step.
    $("#preferences-browse").onclick = async () => {
        const button = $("#preferences-browse");
        button.disabled = true;
        say("Choose a folder in the window that opened.");
        try {
            const { path } = await post(api, "/api/choose-folder", { start: field("inbox").value.trim() });
            if (path) field("inbox").value = path;
            say(path ? "Folder selected. Save to keep it." : "");
        } catch (error) {
            say(error.message, true);
        } finally {
            button.disabled = false;
        }
    };

    $("#preferences-library-browse").onclick = async () => {
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
        $("#preferences-library-use").click();
    };

    // Ask the server what the folder means first, so the user confirms a concrete outcome.
    $("#preferences-library-use").onclick = async () => {
        say("");
        if (!canLeave()) return say("Save or discard changes in open papers first.", true);
        if (field("inbox").value.trim() !== savedInbox) return say("Save the other changes in this dialog first.", true);
        try {
            plan = await post(api, "/api/library/inspect", { path: field("library").value });
        } catch (error) {
            return say(error.message, true);
        }
        const actionable = ["open", "move"].includes(plan.action);
        $("#preferences-library-question").textContent = plan.message;
        confirmBox.classList.toggle("refused", !actionable);
        $("#preferences-library-yes").hidden = !actionable;
        $("#preferences-library-yes").textContent = plan.action === "move" ? "Move library" : "Open library";
        $("#preferences-library-no").textContent = actionable ? "Cancel" : "OK";
        confirmBox.hidden = false;
        confirmBox.scrollIntoView({ block: "nearest" });
        (actionable ? $("#preferences-library-yes") : $("#preferences-library-no")).focus();
    };
    $("#preferences-library-no").onclick = closeConfirm;
    $("#preferences-library-yes").onclick = async () => {
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

    return async function open(focus = "zoom") {
        say("");
        size.value = String(readZoom());
        dialog.showModal();
        field(focus).focus();
        try {
            await refresh();
        } catch (error) {
            say(`Settings unavailable: ${error.message}`, true);
        }
    };
}
