import * as pdfjs from "pdfjs-lib";
import { mergeHighlightLines } from "./highlight-lines.js";
import { setupSplitResize } from "./split-resize.js";
import {
    EventBus,
    PDFLinkService,
    PDFFindController,
    PDFViewer,
} from "/vendor/pdfjs/web/pdf_viewer.mjs";

// Keep PDF.js editing and serialization, joining only selection boxes from the same line.
const selectionBoxes =
    pdfjs.AnnotationEditorUIManager.prototype.getSelectionBoxes;
pdfjs.AnnotationEditorUIManager.prototype.getSelectionBoxes = function (layer) {
    const boxes = selectionBoxes.call(this, layer);
    const rotation = layer?.getAttribute("data-main-rotation");
    const rotated = rotation === "90" || rotation === "270";
    const rect = layer?.getBoundingClientRect();
    const scale = rect
        ? rotated
            ? rect.width / rect.height
            : rect.height / rect.width
        : 1;
    return boxes && mergeHighlightLines(boxes, rotated, scale);
};

pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/build/pdf.worker.mjs";

export class Reader {
    constructor(doc, host, api, callbacks, initial = {}) {
        this.doc = doc;
        this.readOnly = initial.readOnly || false;
        this.api = api;
        this.callbacks = callbacks;
        this.revision = doc.revision ?? 0;
        // Set by edits PDF.js reports; `dirty` also compares the serialized annotation state.
        this.edited = false;
        // Serialized annotation state matching the PDF on disk; "" means nothing is edited.
        this.savedHash = "";
        this.saving = false;
        this.changeVersion = 0;
        this.editorReady = false;
        this.modePending = null;
        this.layout = "stacked";
        // Once the reader picks a zoom for this pane, layout changes stop refitting it.
        this.zoomed = false;
        this.element = document.createElement("section");
        this.element.className = "pane";
        this.element.innerHTML =
            '<div class="split-handle" role="separator" aria-label="Resize quick view" aria-orientation="horizontal" aria-valuemin="20" aria-valuemax="75" aria-valuenow="40" tabindex="0" hidden></div><div class="pane-bar"><strong class="pane-label"></strong><div class="spacer"></div><button class="layout-toggle" aria-pressed="false">Side by side</button><button class="promote">Open in tab</button><button class="close-preview">Close preview</button></div><div class="viewer-wrap"><div class="viewer-container"><div class="pdfViewer"></div></div></div>';
        host.append(this.element);
        this.element.addEventListener("pointerdown", () =>
            callbacks.activate(this),
        );
        this.element.addEventListener("focusin", () =>
            callbacks.activate(this),
        );
        this.element.querySelector(".promote").onclick = () =>
            callbacks.promote(this);
        this.element.querySelector(".close-preview").onclick = () =>
            callbacks.closePreview(this);
        this.container = this.element.querySelector(".viewer-container");
        this.events = new AbortController();
        this.setSplitOrientation = setupSplitResize(
            this.element.querySelector(".split-handle"), this.element, this.events.signal);
        this.element.querySelector(".layout-toggle").onclick = () => callbacks.toggleLayout();
        this.container.addEventListener("pointerdown", (event) => {
            if (this.viewer.annotationEditorMode !== pdfjs.AnnotationEditorType.INK ||
                !event.target.closest(".annotationEditorLayer")) return;
            const pointerId = event.pointerId;
            window.addEventListener("pointerup", (up) => {
                if (up.pointerId !== pointerId) return;
                // Finish each stroke so the host can mark it as unsaved immediately.
                queueMicrotask(() => this.editorManager?.currentLayer?.endDrawingSession());
            }, { once: true, signal: this.events.signal });
        }, { signal: this.events.signal });
        // Only consume links that can be mapped confidently; all others remain native PDF links.
        this.container.addEventListener(
            "click",
            (event) => {
                if (!this.readOnly &&
                    this.viewer.annotationEditorMode === pdfjs.AnnotationEditorType.NONE &&
                    document.getSelection()?.isCollapsed) {
                    const page = event.target.closest(".page");
                    const pageIndex = Number(page?.dataset.pageNumber) - 1;
                    for (const editor of this.editorManager?.getEditors(pageIndex) || []) {
                        if (!["ink", "highlight"].includes(editor.editorType)) continue;
                        const path = page.querySelector(`#path_${editor._drawId}`);
                        const matrix = path?.getScreenCTM();
                        if (!matrix) continue;
                        const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
                        let hit;
                        if (editor.editorType === "ink") {
                            // Thin strokes need a small pointer tolerance at normal screen resolutions.
                            const width = path.style.strokeWidth;
                            path.style.strokeWidth = `${Math.max(6, parseFloat(getComputedStyle(path).strokeWidth) || 0)}px`;
                            hit = path.isPointInStroke(point);
                            path.style.strokeWidth = width;
                            if (!hit && getComputedStyle(path).fill !== "none") hit = path.isPointInFill(point);
                        } else {
                            hit = path.isPointInFill(point);
                        }
                        if (!hit) continue;
                        // PDF.js disables editor hit testing in Select mode; use its drawn path, not the editor box.
                        this.viewer.annotationEditorMode = { mode: editor.mode, editId: editor.uid };
                        event.preventDefault();
                        event.stopImmediatePropagation();
                        return;
                    }
                }
                const annotation = event.target.closest(
                    ".annotationLayer .inkAnnotation, .annotationLayer .highlightAnnotation",
                );
                if (annotation && !this.readOnly &&
                    this.viewer.annotationEditorMode === pdfjs.AnnotationEditorType.NONE) {
                    this.viewer.annotationEditorMode = {
                        mode: annotation.classList.contains("inkAnnotation")
                            ? pdfjs.AnnotationEditorType.INK
                            : pdfjs.AnnotationEditorType.HIGHLIGHT,
                        editId: annotation.dataset.annotationId,
                    };
                    return;
                }
                const link = event.target.closest(".annotationLayer a");
                if (!link || this.kind !== "main" ||
                    !link.closest("[data-internal-link]")) return;
                const hash = link.getAttribute("href");
                if (!hash?.startsWith("#") || hash.length === 1) return;
                // PDF.js uses escape(), including its %uXXXX form, for destination hashes.
                const decoded = unescape(hash.slice(1));
                let destination = decoded;
                try {
                    if (Array.isArray(JSON.parse(decoded))) destination = null;
                } catch {}
                const page = link.closest(".page");
                if (!page) return;
                const box = link.getBoundingClientRect();
                const text = [...page.querySelectorAll(".textLayer span")]
                    .filter((span) => {
                        const rect = span.getBoundingClientRect();
                        return (
                            rect.width &&
                            rect.height &&
                            rect.left < box.right &&
                            rect.right > box.left &&
                            rect.top < box.bottom &&
                            rect.bottom > box.top
                        );
                    })
                    .map((span) => span.textContent)
                    .join("")
                    .trim();
                if (callbacks.citation(this, text, destination)) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                }
            },
            true,
        );
        this.bus = new EventBus();
        this.bus.on("annotationeditoruimanager", ({ uiManager }) => {
            if (this.editorManager === uiManager) return;
            this.editorManager = uiManager;
            // PDF.js does not flag edits to editors already in its storage (move, resize);
            // `dirty` sees them in the serialized state, so only the toolbar needs a refresh.
            const addCommands = uiManager.addCommands;
            uiManager.addCommands = (...args) => {
                addCommands.apply(uiManager, args);
                this.callbacks.status(this);
            };
        });
        this.bus.on("annotationeditorlayerrendered", () => {
            if (this.editorReady || !this.editorManager) return;
            this.editorReady = true;
            this.callbacks.status(this);
        });
        this.bus.on("annotationeditormodechanged", ({ mode }) => {
            if (this.modePending !== mode) return;
            this.modePending = null;
            this.container.inert = this.saving;
            this.callbacks.status(this);
        });
        // PDF.js delegates mode changes for existing annotations to its host viewer.
        this.bus.on("switchannotationeditormode", (options) => {
            if (!this.readOnly) this.viewer.annotationEditorMode = options;
        });
        // Also fires on undo and redo, which can bring the annotations back to the saved state.
        this.bus.on("editingstateschanged", () => {
            this.reportSelection();
            this.callbacks.status(this);
        });
        this.bus.on("annotationeditorparamschanged", () => {
            queueMicrotask(() => this.reportSelection());
        });
        this.links = new PDFLinkService({ eventBus: this.bus });
        this.find = new PDFFindController({
            linkService: this.links,
            eventBus: this.bus,
        });
        this.viewer = new PDFViewer({
            container: this.container,
            eventBus: this.bus,
            linkService: this.links,
            findController: this.find,
            annotationEditorMode: this.readOnly
                ? pdfjs.AnnotationEditorType.DISABLE
                : pdfjs.AnnotationEditorType.NONE,
            annotationEditorHighlightColors:
                "yellow=#F5D76E,green=#8FD3AA,blue=#79C5DF,pink=#E9A9BC,darkgreen=#243F3A",
            imageResourcesPath: "/vendor/pdfjs/web/images/",
        });
        this.links.setViewer(this.viewer);
        this.bus.on("pagesinit", () => {
            this.viewer.currentScaleValue = this.fitsWidth() ? "page-width" : initial.scale || "page-width";
            this.viewer.currentPageNumber = initial.page || 1;
        });
        this.bus.on("pagechanging", () => {
            this.remember();
        });
        this.bus.on("scalechanging", () => this.remember());
    }
    async open() {
        this.loadingTask = pdfjs.getDocument({
            url: `/api/documents/${this.doc.id}/pdf`,
            cMapUrl: "/vendor/pdfjs/cmaps/",
            cMapPacked: true,
            standardFontDataUrl: "/vendor/pdfjs/standard_fonts/",
            wasmUrl: "/vendor/pdfjs/wasm/",
            iccUrl: "/vendor/pdfjs/iccs/",
        });
        try {
            this.pdf = await this.loadingTask.promise;
        } catch (error) {
            if (this.closed) return;
            throw error;
        }
        if (this.closed) return;
        // Entering an editing mode turns saved annotations into editors and flips PDF.js's
        // modified flag without any change. Judge by the serialized state (untouched editors
        // serialize to nothing) and re-arm the flag so the next real edit still reports.
        this.pdf.annotationStorage.onSetModified = () => queueMicrotask(() => {
            if (this.dirty) this.markDirty();
            else this.pdf.annotationStorage.resetModified();
        });
        this.find.setDocument(this.pdf);
        this.viewer.setDocument(this.pdf);
        this.links.setDocument(this.pdf);
        this.callbacks.loaded(this);
    }
    // The tab already names a main paper; only a quick view shows a different
    // paper, so only it gets a bar with a label.
    setKind(kind, note = this.readOnly ? "Read only" : "") {
        this.kind = kind;
        this.element.classList.toggle("preview", kind === "preview");
        const name = this.doc.title || this.doc.filename || "Untitled paper";
        const label = kind === "preview"
            ? ["Quick view", note, name].filter(Boolean).join(" · ")
            : "";
        this.element.setAttribute(
            "aria-label",
            `${kind === "preview" ? "Quick view" : "Main paper"}: ${name}`,
        );
        this.element.querySelector(".pane-label").textContent = label;
        this.element.querySelector(".pane-bar").hidden = kind !== "preview";
        this.element.querySelector(".promote").hidden = kind !== "preview";
        this.element.querySelector(".layout-toggle").hidden = kind !== "preview";
        this.element.querySelector(".split-handle").hidden = kind !== "preview";
        if (kind !== "preview") this.element.style.flexBasis = "";
        this.element.querySelector(".close-preview").hidden =
            kind !== "preview";
    }
    // "side" puts the quick view right of the main paper; narrow areas cannot offer it.
    setLayout(layout, canSplitSide) {
        const side = layout === "side";
        const changed = layout !== this.layout;
        this.layout = layout;
        // A remembered fixed zoom can be wider than the half-width pane and clip the page.
        if (changed && this.fitsWidth() && this.viewer.pagesCount)
            this.viewer.currentScaleValue = "page-width";
        const toggle = this.element.querySelector(".layout-toggle");
        this.setSplitOrientation(side ? "vertical" : "horizontal");
        toggle.setAttribute("aria-pressed", String(side));
        toggle.disabled = !canSplitSide;
        toggle.title = canSplitSide ? "" : "The reader is too narrow for two columns";
    }
    fitsWidth() {
        return this.kind === "preview" && this.layout === "side" && !this.zoomed;
    }
    remember() {
        if (!this.pdf) return;
        localStorage.setItem(
            `paperassist-view-${this.doc.id}`,
            JSON.stringify({
                page: this.viewer.currentPageNumber,
                scale: this.viewer.currentScaleValue,
            }),
        );
        this.callbacks.position(this);
    }
    setMode(mode) {
        if (!this.editorReady || this.modePending !== null || this.readOnly) return;
        if (this.viewer.annotationEditorMode === mode) return;
        // PDF.js may finish the page redraw asynchronously after the toolbar click.
        this.modePending = mode;
        this.container.inert = true;
        this.callbacks.status(this);
        try {
            this.viewer.annotationEditorMode = { mode };
        } catch (error) {
            this.modePending = null;
            this.container.inert = this.saving;
            this.callbacks.status(this);
            this.callbacks.error(error.message);
        }
    }
    reportSelection() {
        const editor = this.editorManager?.firstSelectedEditor;
        const type = editor?.editorType || null;
        const colorType = type === "ink"
            ? pdfjs.AnnotationEditorParamsType.INK_COLOR
            : pdfjs.AnnotationEditorParamsType.HIGHLIGHT_COLOR;
        const color = editor?.propertiesToUpdate?.find(([key]) => key === colorType)?.[1] || null;
        this.callbacks.editorSelection?.(this, {
            hasSelection: Boolean(editor), type, color, canDelete: Boolean(editor),
        });
    }
    deleteSelectedAnnotation() {
        if (this.readOnly || !this.editorManager?.hasSelection) return;
        this.bus.dispatch("editingaction", { source: this, name: "delete" });
        // Removing the last unsaved editor resets PDF.js storage without marking it modified.
        this.markDirty();
    }
    setAnnotationColor(color) {
        const type = this.editorManager?.firstSelectedEditor?.editorType;
        if (this.readOnly || !["ink", "highlight"].includes(type)) return;
        this.bus.dispatch("switchannotationeditorparams", {
            source: this,
            type: type === "ink" ? pdfjs.AnnotationEditorParamsType.INK_COLOR : pdfjs.AnnotationEditorParamsType.HIGHLIGHT_COLOR,
            value: color,
        });
        this.markDirty();
        this.reportSelection();
    }
    markDirty() {
        this.edited = true;
        this.changeVersion++;
        this.callbacks.status(this);
    }
    // PDF.js reports some edits late (a keyboard nudge is announced after a second) and
    // others never, so besides the reported flag compare the serialized state with the saved one.
    get dirty() {
        return this.edited || (Boolean(this.pdf) &&
            this.pdf.annotationStorage.serializable.hash !== this.savedHash);
    }
    set dirty(value) {
        this.edited = value;
    }
    commitEdits() {
        this.editorManager?.currentLayer?.endDrawingSession();
        this.editorManager?.commitOrRemove();
    }
    async explicitSave() {
        if (this.saving) return this.savePromise;
        if (this.readOnly || !this.pdf) return !this.dirty;
        this.commitEdits();
        if (!this.dirty) return true;
        this.saving = true;
        this.error = null;
        const version = this.changeVersion;
        const hash = this.pdf.annotationStorage.serializable.hash;
        // PDF.js resets its modified flag during serialization; block page edits until the write finishes.
        this.container.inert = true;
        this.callbacks.status(this);
        this.savePromise = (async () => {
            try {
                const bytes = await this.pdf.saveDocument();
                const result = await this.api(`/api/documents/${this.doc.id}/pdf`, {
                    method: "PUT",
                    headers: {
                        "Content-Type": "application/pdf",
                        "X-PaperAssist-Revision": String(this.revision),
                    },
                    body: bytes,
                });
                this.revision = result.revision ?? result.document?.revision ?? this.revision + 1;
                this.doc.revision = this.revision;
                this.savedHash = hash;
                this.dirty = this.changeVersion !== version;
                return !this.dirty;
            } catch (error) {
                this.dirty = true;
                this.error = error.message;
                return false;
            } finally {
                this.saving = false;
                this.container.inert = this.modePending !== null;
                this.callbacks.status(this);
            }
        })();
        return this.savePromise;
    }
    async close({ discard = false } = {}) {
        if (this.saving) return false;
        this.commitEdits();
        if (this.dirty && !discard) return false;
        this.closed = true;
        this.events.abort();
        this.viewer.setDocument(null);
        this.find.setDocument(null);
        this.links.setDocument(null);
        await this.loadingTask?.destroy();
        this.element.remove();
        return true;
    }
}
export { pdfjs };
