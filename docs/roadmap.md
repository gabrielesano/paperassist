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
   targets. Retire the web interface, HTTP server and pywebview host through a
   deliberate transition ([desktop plan](development/desktop-plan.md)).
3. Bundle a minimal Tesseract 5 so [offline OCR](development/offline-ocr.md) works
   without external dependencies. Build only what PaperAssist uses: the engine
   reading the grayscale PGM pages it renders and writing TSV, Leptonica without
   optional image codecs, and the English LSTM model. Build it from source on each
   release host (the Linux baseline, Ubuntu 22.04, ships only 4.1), add its
   licenses to the third-party notices, and prefer the bundled engine over a system
   one. Record the size added per platform, and accept it only after a frozen
   bundle recovers a scan on clean hosts without a system Tesseract.

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
