# Release acceptance: 0.3.0

The [0.3.0 release](https://github.com/gabrielesano/paperassist/releases/tag/v0.3.0)
consolidates the backend, native Qt reader, and packaging stack while cutting
multi-paper resident memory by 69–101 MB (-18% to -24% across a 3-paper X11/XCB
session), hardening library and PDF annotation handling, and eliminating N+1
reference resolution and global HTTP throttling bottlenecks.

## Summary of verified changes

1. **Memory & worker optimization (`paperassist.native.worker`, `paperassist.citations`, `paperassist.native.document`):**
   - Shared a single PDFium `Pdf` instance between page rendering and `CitationIndex` in the render worker.
   - Added destination pre-checking and per-page word-cache eviction in `CitationIndex.locate()`, reducing the 3-paper worker RSS delta during citation lookup from `+53.4 MB` to `+1.9 MB`.
   - Enabled worker idle heap trimming (`trim_heap()`) after tab close (`trimmed = False` + `gc.collect()`), hidden-tab `PdfDocument.hibernate()` for unmodified `pypdf` structures, and UI heap trimming after background jobs.
   - Across the 3-paper Xvfb XCB benchmark (`Zhou2018.pdf` + `Moschou2015.pdf` + `KohutovaVerwichte2016.pdf`, 32.33 MB / 98 pages), combined UI + worker RSS dropped from `344.2 → 275.5 MB` (-68.7 MB) after 1 paper, `388.5 → 319.1 MB` (-69.4 MB) with 3 tabs open, `428.5 → 354.4 MB` (-74.1 MB) after `locate()` across all 3 documents, and `417.1 → 316.4 MB` (-100.7 MB / -24.1%) after closing 2 tabs.
2. **Library & PDF engine hardening (`paperassist.library`, `paperassist.native.engine`, `paperassist.native.document`, `paperassist.native.outline`):**
   - Verified SHA-256 digests of original and working PDFs on open, refusing replaced files or symlinks and passing verified snapshots to the render worker.
   - Added support for indirect `QuadPoints` and ink stroke arrays, null `/Border` and `/AP` entries, gray/CMYK default stroke colors, rotated-page text and note baseline merging, single-step undo across multi-page highlights, and direct PDFium outline reading (`Pdf.outline()`).
3. **Backend performance & concurrency (`paperassist.http_client`, `paperassist.references`, `paperassist.library`, `paperassist.workspace`):**
   - Extracted `paperassist.http_client` with cached SSL context discovery, SSRF-safe PDF downloads, and per-host rate-limit locks released before `urlopen()`.
   - Eliminated N+1 `library.get_path()` checks and $O(R \times D)$ token recomputation in `references._resolve()`, reused extracted geometry chunks in `_analyze_pdf`, and reused thread-local SQLite WAL connections.
4. **Architecture & packaging cleanup (`packaging/`, `src/paperassist/native/`):**
   - Extracted pure citation matching into `paperassist.citations`, consolidated platform/worker/source/dialog modules, and replaced `install(window)` monkey-patching and `WeakSet` pane scanners with explicit composition and `MainWindow.pane_added`.
   - Shared Linux installer locking via `packaging/installer/lock_common.sh`, delegated `--uninstall` to `uninstall.sh`, moved third-party license texts into `packaging/licenses/` with platform-gated library notices, shared single-pass ELF inspection in `packaging/audit_glibc.py`, and unified `packaging/entry.py` with `paperassist.cli:main`.

## Local verification

- Full offscreen unit test suite (`QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v`): all backend, native UI, and packaging source tests pass.
- Virtual desktop (`xvfb-run -a`, real Qt `xcb` platform): native GUI suites, interactive window/split-view/annotation/dialog walkthrough, `--self-test`, and `--self-test-ocr` pass with zero orphan render workers.

## Remaining scope

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization, and additional architectures remain
outside this release's automated acceptance. Libraries and saved revisions remain
compatible with `0.2.0a6`; installation does not change the default PDF reader
or library.
