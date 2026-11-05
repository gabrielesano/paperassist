// The application menu behind the brand button. The native popover supplies light dismiss
// and Esc; the arrow keys are what the menu role promises to assistive technology.
import { isMac } from "./shortcuts.js";

const $ = (selector) => document.querySelector(selector);

// `actions` maps each item's data-action to the function that opens its dialog.
export function installAppMenu(actions) {
    const button = $("#brand-button"), menu = $("#app-menu");
    const items = [...menu.querySelectorAll("[role=menuitem]")];
    $("#preferences-hint").textContent = `${isMac() ? "⌘" : "Ctrl"}+,`;

    menu.addEventListener("beforetoggle", (event) => {
        if (event.newState !== "open") return;
        // The menu is scaled by the interface size, so its offsets are in scaled pixels.
        const zoom = parseFloat(getComputedStyle(menu).zoom) || 1;
        const box = button.getBoundingClientRect();
        menu.style.left = `${box.left / zoom}px`;
        menu.style.top = `${(box.bottom + 4) / zoom}px`;
    });
    menu.addEventListener("toggle", (event) => {
        const open = event.newState === "open";
        button.setAttribute("aria-expanded", String(open));
        if (open) items[0].focus();
    });
    button.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowDown") return;
        event.preventDefault();
        if (!menu.matches(":popover-open")) menu.showPopover();
    });
    menu.addEventListener("keydown", (event) => {
        // Closing first lets focus return to the button before Tab moves on.
        if (event.key === "Tab") return menu.hidePopover();
        const at = items.indexOf(document.activeElement);
        const target = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[event.key];
        if (target === undefined) return;
        event.preventDefault();
        items[(target + items.length) % items.length].focus();
    });
    menu.addEventListener("click", (event) => {
        const action = event.target.closest("[data-action]")?.dataset.action;
        if (!action) return;
        menu.hidePopover();
        actions[action]();
    });
}
