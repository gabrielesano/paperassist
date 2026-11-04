// The quick view sits below ("horizontal" separator) or right of ("vertical") the
// main paper; each orientation remembers its own size.
const DEFAULT_SIZE = { horizontal: 40, vertical: 50 };

export function splitSize(extent, total) {
    return Math.max(20, Math.min(75, total > 0 ? extent / total * 100 : 40));
}

// Returns setOrientation(), which the owner calls whenever the layout changes.
export function setupSplitResize(handle, pane, signal) {
    let pointer = null;
    let orientation = "horizontal";
    const sizes = { ...DEFAULT_SIZE };
    const setSize = (size) => {
        const value = Math.max(20, Math.min(75, size));
        sizes[orientation] = value;
        pane.style.flexBasis = `${value}%`;
        handle.setAttribute("aria-valuenow", String(Math.round(value)));
    };
    const currentSize = () => orientation === "vertical"
        ? splitSize(pane.offsetWidth, pane.parentElement.offsetWidth)
        : splitSize(pane.offsetHeight, pane.parentElement.offsetHeight);
    const stop = () => {
        const captured = pointer;
        pointer = null;
        if (captured !== null && handle.hasPointerCapture(captured)) {
            handle.releasePointerCapture(captured);
        }
    };
    handle.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        pointer = event.pointerId;
        handle.setPointerCapture(pointer);
    }, { signal });
    handle.addEventListener("pointermove", (event) => {
        if (event.pointerId !== pointer) return;
        event.preventDefault();
        const bounds = pane.parentElement.getBoundingClientRect();
        setSize(orientation === "vertical"
            ? splitSize(bounds.right - event.clientX, bounds.width)
            : splitSize(bounds.bottom - event.clientY, bounds.height));
    }, { signal });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
        handle.addEventListener(name, (event) => {
            if (event.pointerId === pointer) stop();
        }, { signal });
    }
    handle.addEventListener("focusin", (event) => event.stopPropagation(), { signal });
    handle.addEventListener("keydown", (event) => {
        let value = currentSize();
        // The quick view is the end pane: moving toward the start grows it.
        if (event.key === "ArrowUp" || event.key === "ArrowLeft") value += 5;
        else if (event.key === "ArrowDown" || event.key === "ArrowRight") value -= 5;
        else if (event.key === "Home") value = 20;
        else if (event.key === "End") value = 75;
        else return;
        event.preventDefault();
        setSize(value);
    }, { signal });
    signal.addEventListener("abort", stop, { once: true });
    return (next) => {
        if (next === orientation) return;
        stop();
        orientation = next;
        handle.setAttribute("aria-orientation", next);
        setSize(sizes[next]);
    };
}
