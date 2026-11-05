# Native stack decision

Recorded 2026-11-05. The interface moves from the web app to a native window built
with Python, Qt Widgets (PySide6) and a C PDF engine. This note keeps the measurements
behind that choice and the options deliberately left for later.

## Measurements

Same four papers (64 pages), Xvfb, display scale 1.5, PSS of the whole process tree
in MiB: empty window / four papers open / all scrolled / three tabs closed.

| Interface | Memory | Page render | Quick view open / close |
| --- | --- | --- | --- |
| Web, QtWebEngine, before the memory fixes | 553 / 1394 / 3849 / 3451 | 0.17 s | 0.32 s / 0.01 s |
| Web, QtWebEngine, with the fixes | 536 / 1151 / 2211 / 1906 | 0.17 s | 0.32 s / 0.01 s |
| Web, WebKitGTK (device pixel ratio 2) | 450 / 899 / 1844 / 976 | 0.52 s | 0.6 s / 0.05-0.12 s |
| Native, Qt Widgets + MuPDF | 165-180 / 253-280 / ~500 / ~320 | 0.037 s | 0.04 s / 0.01 s |

Most of the native memory after scrolling is page pixels, which any implementation
pays. Python itself costs about 5 MiB per interpreter; Python plus the PySide6
bindings about 30-40 MiB in total. Rendering already runs in C.

A trimmed PyInstaller bundle of the native prototype is 181 MB installed and 61 MB
compressed on Linux. The largest parts are PyMuPDF (52 MB, of which 18 MB are
bundled fonts and 15 MB are bindings), ICU data in the Linux Qt wheels (37 MB), and
Qt Core/Gui/Widgets with bindings (about 40 MB). pypdfium2 is 8 MB installed.

## Open choices

- **PDF engine.** MuPDF (PyMuPDF, AGPL-3.0) or PDFium (pypdfium2, BSD/Apache, much
  smaller). A comparison of quality, reading-order selection, annotations and bundle
  size decides; all engine calls stay in `src/paperassist/native/engine.py`.
- **Linux bundle size.** Filtered ICU data or a shared runtime (distribution packages,
  Flatpak) would remove most of the 37 MB that Linux Qt wheels carry.

## Deferred: Rust

A Rust implementation (PDFium in a render thread, no Python runtime, a lighter
toolkit such as Slint, egui, iced or GTK4) is the only compiled rewrite worth
considering. Estimated gain over Python + Qt + PDFium: 60-100 MiB less memory with
papers open, an empty window of perhaps 30-60 MiB, 10-20 MB smaller downloads and
instant start. It would mean rewriting the backend as well (library, citation
analysis tuned on real papers, retrieval, metadata, and their tests) and accepting
less mature desktop toolkits for accessibility, dialogs and text input.

Not pursued now. Revisit it if measured footprint still matters once the native app
reaches parity. Measure it first with a spike shaped like the native prototype.
A smaller step comes first if needed: a compiled render helper in place of the
Python render process.
