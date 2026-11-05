# Roadmap

The product contract is in [specification.md](specification.md). Verification
records are in [development/](development/).

## Current work

1. Library and import: deterministic metadata, managed originals, SQLite catalog,
   hash deduplication, manual correction and tested persistence.
2. Native reader (Qt Widgets + MuPDF): tabs, quick view, selection, highlights and
   explicit save; then drawing, comments and editing existing annotations,
   compatible with PDFs annotated by the web reader.
3. References and background work without HTTP: citation analysis, shared local
   resolution, selected open-access retrieval and web-search fallback, called
   directly by the native interface.
4. Native application parity with the web interface, following the
   [migration checklist](development/native-migration.md): library, references
   column, comments, preferences, shortcuts and accessibility. Then standalone
   builds and PDF associations per OS/architecture, and removal of the web
   interface, the HTTP server and the pywebview window
   ([desktop plan](development/desktop-plan.md), transitional).

## Acceptance sequence

- Bulk import and reimport a real collection without duplicates or lost unresolved files.
- Open a library paper, annotate, save, close and reopen without losing changes.
- Consult a reference below the primary document, then promote it into a separate tab.
- Verify local citation ranking and visibly partial results against manually checked examples.
- Exercise local-file reuse, download selection, recoverable failures and search fallback.
- Check accessibility, narrow-window layout, active-document focus, and English UI.
- Verify application close/reopen, one owner per library and platform installation.
- Measure memory, render time and interface stalls with the reference papers.

## Later extensions

OCR, external-file indexing, broader citation-format coverage, advanced search,
annotation export and bibliographic integrations follow actual observed needs.
A Rust implementation stays deferred; see the [native stack decision](development/native-stack.md).
LLMs are not used for importing or organizing the local collection.
