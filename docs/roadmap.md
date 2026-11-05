# Roadmap

The product contract is in [specification.md](specification.md). Verification
records are in [development/](development/).

## Current work

1. Library and import: deterministic metadata, managed originals, SQLite catalog,
   hash deduplication, manual correction and tested persistence.
2. Reader: local PDF.js, compact three-column workspace, native drawing and
   highlights, comments, independent panes, internal tabs and save/reopen proof.
3. Server and references: local API, controlled background work, citation analysis,
   shared local resolution, selected open-access retrieval and web-search fallback.
4. Desktop window: source preview with safe close and existing-server attachment;
   complete interactive and Windows/macOS checks, then file delivery, PDF
   associations and standalone builds per OS/architecture.
   See the [desktop plan](development/desktop-plan.md).

## Acceptance sequence

- Bulk import and reimport a real collection without duplicates or lost unresolved files.
- Open a library paper, annotate, save, close and reopen without losing changes.
- Consult a reference below the primary document, then promote it into a separate tab.
- Verify local citation ranking and visibly partial results against manually checked examples.
- Exercise local-file reuse, download selection, recoverable failures and search fallback.
- Check accessibility, narrow-window layout, active-document focus, and English UI.
- Verify desktop close/reopen, existing-server attachment and platform installation.

## Later extensions

OCR, external-file indexing, broader citation-format coverage, advanced search,
annotation export and bibliographic integrations follow actual observed needs.
LLMs are not used for importing or organizing the local collection.
