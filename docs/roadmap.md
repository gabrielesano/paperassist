# Roadmap

The product contract is in [specification.md](specification.md). Verification
records are in [development/](development/).

## Current work

Native parity is accepted: 104 rows and five host-only exclusions in the
[Linux migration inventory](development/native-migration.md). Standalone Linux,
Windows and Apple Silicon macOS packages already exist. The web stack is retired
and minimal offline OCR is included in new builds.

The `0.3.0` release lowers multi-paper resident memory by 18–24% (sharing a
single PDFium document between rendering and `CitationIndex` in the worker,
hibernating inactive `pypdf` structures on hidden tabs, and trimming native
heaps after tab close and background jobs), verifies SHA-256 integrity of
original and working PDFs on open, hardens indirect PDF annotation and
rotated-page handling, batches reference resolution and per-host HTTP
throttling, and consolidates the native UI and packaging stack. See
[release notes](releases/0.3.0.md) and the
[release acceptance record](development/release-0.3.0.md).

1. Keep manual desktop acceptance as an explicit follow-up: clean Ubuntu/Debian
   GUI trials and interactive Windows/macOS reading, editing, focus and display
   checks. Hosted build and installer tests cover automated acceptance; untested
   environments remain documented rather than blocking local development.
2. Before broader distribution, address Windows signing and macOS Developer ID
   signing/notarization, and verify additional OS/architecture targets as needed.

## Completed transitions and automated acceptance

- Released `0.4.0` ([release notes](releases/0.4.0.md)) with:
  - Zero-copy `SharedMemory` PDF IPC (parent-owned pool with Python 3.11+
    `resource_tracker` handling) and worker-side PDFium highlight visibility
    filtering (`FPDF_ANNOT_FLAG_HIDDEN`), removing synchronous `pypdf`
    `without_highlights()` UI-thread rewrites.
  - Single-open PDFium bibliography extraction and position-ordered two-column
    reading in `references._analyze_pdf()`, plus superscript/`(n)` numeric
    citations and explicit 3+-author inline forms (`EXTRACTION_VERSION = 8`).
  - On-demand sidecar searchable OCR text layer (`cache/ocr/pages/<sha256>/`)
    with 256 MiB LRU disk bounds (`ocr.recognize_pages()` / `prune_cache()`),
    enabling `Ctrl+F`, text copy, line-aligned highlights, and phrase comments
    on scanned library PDFs without modifying originals.
  - SQLite FTS5 trigram catalog indexing with triggers and lightweight
    `Library.list_documents()` projections, explicit **Compact revisions…**
    maintenance for superseded `working/` snapshots, opt-in open-tab/split-view
    session restoration, BibTeX **Copy BibTeX** export from verified work
    metadata, and Windows owner-only DACL / DPAPI hardening for `settings.json`.
- Released `0.3.2` with async worker-side `Ctrl+F`, reading marks, **Copy with
  source**, batch **Check folder** / **Open blocked**, indexed SQLite
  manual-link lookup, first-class `arXiv`/`bibcode` work identities and local
  reuse, **Remove…** (`Delete`) in **Library**, Markdown **Copy notes** in
  **Comments**, and non-splitting modifier handling plus `Escape` stroke
  cancellation in the reader. See the [release notes](releases/0.3.2.md).
- Added the Pan tool in `0.3.1`, with a toolbar toggle, middle-button drag,
  and temporary panning while holding Space. See the [release notes](releases/0.3.1.md).
- Prepared `0.3.0` with the multi-paper memory and worker optimizations,
  library/annotation integrity hardening, batch reference resolution, per-host
  HTTP throttling, and native/packaging consolidation. See
  [release notes](releases/0.3.0.md) and the
  [acceptance record](development/release-0.3.0.md).
- Published `0.2.0a6` with the PDF right-click context menu and line-number
  exclusion during multi-line selection and highlighting, verified source and
  tag CI, successful Linux/Windows/macOS package acceptance, and all release
  checksums. See [release notes](releases/0.2.0a6.md) and the
  [acceptance record](development/release-0.2.0a6.md).
- Published `0.2.0a5` with margin line-number stripping and resilient
  bibliography heading detection, verified source and tag CI, successful
  Linux/Windows/macOS package acceptance, and all release checksums. See
  [release notes](releases/0.2.0a5.md) and the
  [acceptance record](development/release-0.2.0a5.md).
- Published `0.2.0a4` with the toolbar overflow fix, verified source and tag CI,
  successful Linux/Windows/macOS package acceptance, and all release checksums.
  See [release notes](releases/0.2.0a4.md) and the
  [acceptance record](development/release-0.2.0a4.md).
- Published `0.2.0a3` with the audited bug fixes, verified source and tag CI,
  successful Linux/Windows/macOS package acceptance, and all release checksums.
  See [release notes](releases/0.2.0a3.md) and the
  [acceptance record](development/release-0.2.0a3.md).
- Published `0.2.0a2` with verified source provenance, installer checksums and
  successful hosted source/build acceptance on Linux, Windows and macOS. See
  [release notes](releases/0.2.0a2.md) and
  [the reference parser follow-up](development/reference-parser-followup.md).
- Managed PDF copies for command-line/system opening, Open and Quick view;
  duplicate reuse and unchanged external originals on Save. Native local-socket
  delivery selects the correct library owner without discarding unsaved edits.
  PDF Open with registrations are included for Linux, Windows and macOS without
  forcing a default reader. See [desktop integration](development/desktop-plan.md)
  and [the current verification record](development/file-opening.md).
- Native source entry point, with CLI import, the existing library process lock
  and PDF.js annotation/note interoperability retained.
- Retirement of the web reader, Flask server, pywebview host, PDF.js vendor tree,
  npm manifests and Node CI job. See [web retirement](development/web-retirement.md).
- Minimal Tesseract 5, static Leptonica, the English LSTM model, runtime manifest
  and bundled license notices. No system OCR installation is used by frozen apps.
- Hosted Ubuntu 22.04, Windows and Apple Silicon macOS builds and copied-bundle
  OCR checks passed in [Build run 42661875263](https://github.com/gabrielesano/paperassist/actions/runs/42661875263).
  The Ubuntu bundle passes the GLIBC_2.35 gate. Per-platform payload sizes and
  the scope of installation/GUI checks are recorded in
  [host acceptance](development/host-acceptance.md).
- Installed-launcher/setup/DMG OCR checks passed on all three hosts in
  [Build run 42663515822](https://github.com/gabrielesano/paperassist/actions/runs/42663515822).
  The native Cocoa suite and the four Ubuntu/Windows Python CI jobs also passed
  after the keyboard and desktop-test corrections; see the acceptance record.

## Regression acceptance sequence

- Bulk import and reimport a real collection without duplicates or lost unresolved files.
- Open a library paper, annotate, save, close and reopen without losing changes.
- Consult a reference below the primary document, then promote it into a separate tab.
- Verify local citation ranking and visibly partial results against manually checked examples.
- Exercise local-file reuse, download selection, recoverable failures and search fallback.
- Check accessibility, narrow-window layout, active-document focus, and English UI.
- Verify application close/reopen, one owner per library and platform installation.
- Measure memory, render time and interface stalls with the reference papers.

## Later extensions

External-file in-place indexing, advanced cross-library search, annotation
export, and broader bibliographic integrations follow actual observed needs. A
Rust implementation stays deferred; see the
[native stack decision](development/native-stack.md). LLMs are not used for
importing or organizing the local collection.
