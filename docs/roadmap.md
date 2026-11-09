# Roadmap

The product contract is in [specification.md](specification.md). Verification
records are in [development/](development/).

## Current work

Native parity is accepted in the [Linux migration inventory](development/native-migration.md):
104 accepted rows and five host-only exclusions. CI is green on Ubuntu and
Windows with Python 3.11 and 3.13 from commit `f73ce91`. Cross-platform release
readiness still requires the checks below.

1. Deliver the first Linux release installable with one command without requiring
   Python, following the [session checklist](development/linux-release-plan.md).
   Confirm the format and supported targets, rebuild, implement installation and
   desktop integration, and verify clean installations before publication.
2. Extend standalone builds and PDF associations to further OS/architecture
   targets, including the remaining native file-delivery and desktop integration
   work ([desktop plan](development/desktop-plan.md)).
3. Complete host acceptance of the [minimal bundled OCR runtime](development/bundled-ocr.md).
   The source build, PyInstaller integration, English LSTM model, licenses and
   per-platform size manifest are implemented. Linux frozen OCR works without
   system Tesseract; Windows/macOS execution and Ubuntu 22.04 baseline verification
   remain to be run through the release build workflow.

## Completed transition

The web reader, Flask HTTP server and pywebview host are retired. The native
reader is the default source entry point; Qt/PDFium are core dependencies.
PDF.js assets, npm manifests and the Node CI job are removed. CLI import and
existing library data remain supported. Historical PDF.js-generated annotation
and notes fixtures remain as interoperability regressions.
See the [retirement verification](development/web-retirement.md).

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

An OCR text layer for search and selection, external-file indexing, broader citation-format coverage, advanced search,
annotation export and bibliographic integrations follow actual observed needs.
A Rust implementation stays deferred; see the [native stack decision](development/native-stack.md).
LLMs are not used for importing or organizing the local collection.
