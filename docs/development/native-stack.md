# Native stack decision

Updated 2026-11-06. The native interface uses Qt Widgets (PySide6), **PDFium via
pypdfium2 for reading/rendering**, and **pypdf for annotation writing**. PyMuPDF is
no longer a native dependency. The web reader remains usable during migration.

## Why PDFium

The isolated spike compared the same four papers at display scale 1.5. PDFium
used 114 MiB empty, 209–210 MiB with four papers open and 378–384 MiB after scrolling,
against MuPDF's 148–163 / 249–264 / 470–475 MiB. Rendering averaged 30 vs 36 ms/page.
Trimmed prototype bundles were 135 vs 181 MiB installed and 43.8 vs 60.8 MiB with
`tar | zstd -19`. Quality and reading features were comparable; PDFium also expanded
copied ligatures and correctly mapped rotated/cropped pages. These are spike
measurements, distinct from the production-core figures below.

pypdfium2's wrapper is BSD-3-Clause/Apache-2.0; PDFium and its bundled components
have their own notices. The wheels use third-party PDFium binary builds, currently
from pdfium-binaries, rather than a locally built Chromium tree. Keep those notices
in distributions and update the wheels when their PDFium builds change. Sources:
[pypdfium2 installation and licensing](https://github.com/pypdfium2-team/pypdfium2),
[PDFium binary builds](https://github.com/bblanchon/pdfium-binaries).

## Production-core verification

Whole-process-tree PSS, MiB; the same four papers/65 rendered pages, private Xvfb,
`QT_SCALE_FACTOR=1.5`. MuPDF values are the previously recorded core baseline;
PDFium is the current production reader with its usual five-page pixel budget.
No library integration is included in either scenario.

| Scenario | Previous MuPDF core | PDFium core |
| --- | --- | --- |
| Empty | 165–180 | 133 |
| Four papers open | 253–267 | 217 |
| All scrolled | about 500 | 385 |
| Idle 20 seconds | 341–363 | 321 |
| Three tabs closed | 319–320 | 315 |
| Mean page render | 36 ms | 33 ms |
| Linux bundle installed | 173 MiB | 128 MiB |
| Bundle `tar \| zstd -19` | 60.6 MiB | 41.4 MiB |

Current scroll-to-painted latency: mean 48 ms, max 143 ms; max worker render 121 ms;
longest event-loop stall while scrolling 25 ms. Quick view opens in 45–52 ms and
closes in 8 ms. An initial run eagerly loaded pypdf in both processes and used
154 / 238 / 427 / 362 / 336 MiB. Loading the annotation helper only when needed
removes that cost from the render process and idle reader. This keeps `PageRenderer`
free of pypdf imports while preserving raw RGB/HiDPI rendering.

The Linux one-dir bundle retains the existing declarative trim rules and explicitly
includes `pypdfium2_raw`'s library and package license metadata. The frozen app's
`--self-test` passes with an empty environment on the synthetic PDF.js fixture.
Windows/macOS builds remain to be run through the manual build workflow. The
reported bundle includes the annotation bar module; its host wiring is listed below.

## Engine and annotation boundaries

Only `native/engine.py` imports pypdfium2; only it and its private
`native/annotations.py` helper handle PDF structures. The reader still receives
plain `Rect`/`Link` values and uses `Pdf`, `PageRenderer`, `trim_caches`, and
`Pdf.write`. Both UI and render process open document bytes, retaining no source
file handle. `FileSource` still writes a same-directory temporary file, fsyncs it,
replaces atomically and fsyncs the directory where supported.

Annotations carry explicit appearances: text highlights use QuadPoints, RGB,
opacity 1 and Multiply; ink has InkList, width, color and a round stroke. Existing
PDF.js highlights/Ink/InkHighlight and foreign highlights without appearances are
readable/editable. Recoloring preserves existing appearance geometry, including
nested Form XObjects, rather than replacing a freehand fill with a thin stroke.

Each edit produces an incremental PDF revision in memory. The render process gets
those bytes with a new document ID, rejects stale pixels and redraws affected pages.
There is no timer, navigation or temporary-file save. Explicit Save publishes that
same revision. Undo stores revision offsets and state tokens, not full document
copies; restoration appends another update. Undo/redo can therefore return to a
clean saved state and a subsequent Save still keeps all previous file bytes as a
prefix. The helper reserves the declared xref `/Size`: pypdf otherwise may reuse
an unindexed xref-stream object number and confuse it with a new annotation.

Relevant PDFium details covered by tests: loose character boxes, line-end IsHyphen
(U+FFFE in text ranges/U+0002 via GetUnicode), normalized ligatures, merged search
rectangles, FS_RECTF field order, FitH/FitR destinations, named destinations,
rotations and crop boxes. Annotation colors are read from pypdf's `/C`, because
PDFium's GetColor fails for annotations with an appearance stream.

## Annotation bar

`annotation_bar.install(window)` adds the top contextual toolbar. `MainWindow`
installs it right after building its actions and refreshes it from `refresh`.
The bar binds new panes during refresh and listens for activation, edits and
annotation selection. It reuses `highlight_action` and exposes Select/Draw,
five highlight colors plus black, applicable ink thickness, Delete and Undo/Redo.
All controls stay above the PDF; tabs and panes need no annotation-specific code.

## Checks and remaining work

PyMuPDF was uninstalled from the main environment before verification: **373 Python
tests and 61 Node tests pass**, with no skips. The real window passes **19/19**
xdotool checks inside Xvfb: selection/copy, five colors, ink width, select/recolor/
resize-width/delete/undo, clean state, explicit incremental Save/reopen, existing
PDF.js edits, quick view and internal links. Screenshots of PDFium and Poppler
were inspected. Unit tests also compare exact unsaved/saved renderer bytes and
exercise nested foreign appearances, rotated/cropped pages and undo after Save.
QA scripts, logs, screenshots and the bundle stay in ignored `tmp/qa/pdfium-core/`.

Comments/notes are deferred to library integration. Password handling, signed PDFs,
unusual pattern-based appearances and cross-viewer checks in Acrobat/Preview/Okular/
Zotero remain outside these checks. No source PDF, daily web service or prototype
under `tmp/qa/native/` or `tmp/qa/pdfium/` was changed.

## Deferred: Rust

A compiled render helper or a smaller toolkit may reduce the remaining runtime
cost, but neither is needed for this migration. Revisit only after native parity,
with a comparable measured spike. Most memory after scrolling remains page pixels.
