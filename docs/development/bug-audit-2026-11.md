# Consolidated bug audit fixes

The reported high, medium and low severity bugs are addressed in source. Tests
use synthetic PDFs, disposable libraries and mocked network responses.

| Audit item | Resolution |
| --- | --- |
| 1. Sources check after dialog close | A Qt slot provides receiver lifetime tracking, disconnecting completion when the dialog is deleted. |
| 2. Concurrent job snapshots | Worker assignments and updates share the polling snapshot lock; published nested values are replaced rather than mutated. |
| 3. Reference lost updates and download batch failures | Reference updates serialize across library objects sharing a database. Extraction and repair lookups run outside the lock; final commits reload and merge current state. UI tasks respect existing jobs, removed references fail individually, and self-matches retain their successful retrieval status. |
| 4–5, 10. Foreign PDF annotations | Indirect values and PDF nulls are resolved consistently; reading and regenerated appearances share default, grayscale, RGB and CMYK color handling. |
| 6. Author-year citations | Citation matching supports year suffixes, two-author forms and particle surnames. |
| 7. Reentrant close | A second close request is ignored while the original close decision is pending. |
| 8–9. Linux installation paths | Trailing slashes are removed before maintenance paths are formed; an existing empty destination is removed before the staging rename. Both uninstall entry points follow the same path rules. |
| 11. Null bookmark coordinate | PDF null top coordinates use the default position without aborting the outline walk. |
| 12. Rotated text geometry | Search merges PDFium spans along the displayed text baseline; comment anchors infer baseline direction from displayed spans, retaining separate lines. |
| 13. Destination migration | Link annotations are re-extracted instead of reusing old occurrence geometry as text. |
| 14–15. Local DOI and provider URLs | Local DOI reuse is independent of Crossref success. OpenAlex and Unpaywall apply the existing HTTPS candidate filter, keeping invalid HTTP candidates from masking blocked HTTPS browser fallbacks. |
| 16. Rematched legacy IDs | Download selection accepts existing reference IDs. Analysis preserves matched IDs and persists a counter so removed IDs cannot be reused by later references. |
| 17. arXiv fallback | An empty filtered title uses journal-reference lookup instead of an author-only query. |
| 18–19. Year provenance | OCR arXiv submission years receive the same exception as text extraction; refreshed provisional years and their sources remain consistent. |
| 20. Incomplete OCR TSV | A missing trailing text field is treated as empty text. |
| 21. Library switch with pending opens | Preferences refuses the switch before persisting configuration while external PDFs are waiting to open. |
| 22. UI lifecycle and caches | Closed views are weakly held; the last pane release removes the comments model; a closed position keeper ignores queued restores; automatic acquisition offers check the active tab; reference analysis invalidates citation caches and rejects stale callbacks. |

The low severity fixes also cover elided citation choices, deselected acquisition
rows, added and removed acquisition rows, quick-view activation disconnection on
promotion, invalid PDF page geometry, normalized annotation rectangles,
PDF-suffixed directories in originals, missing preferred work files, settings
object validation, non-ASCII filename fallback, filesystem errors during folder
inspection, page shortcuts on Welcome, modal metadata editing during external
opening, and Linux uninstall desktop/icon cleanup.

Regression coverage is in the existing workspace, references, retrieval,
library, metadata, OCR, settings, location and installer suites, plus
`test_native_audit_lifecycle.py`, `test_native_audit_pdf.py`, and expanded native
acquisition/comments tests. The lifecycle regressions also run with the Xcb Qt
platform in Xvfb, creating real native windows.

The synthetic reader benchmark uses 16 pages, 128 search matches and 100 searches
per process. Median search time was 22.45 ms before and 22.93 ms after; p95 was
24.85 ms and 27.01 ms. Peak RSS was 42,792 KiB and 43,440 KiB. These are local
synthetic measurements, not acceptance results for arbitrary research PDFs.

Final integration validation:

- `.venv/bin/python -m unittest discover -s tests -v`: 927 tests in 144.28 s;
  904 passed, 21 skipped, and the same two pre-existing frozen-bundle failures.
  The existing `dist/PaperAssist` reports `0.2.0a1`, while source expects
  `0.2.0a2`; its version and provenance assertions fail. No new failures remain.
  The source changes do not rebuild that existing binary artifact.
- The 11 lifecycle regressions pass with `QT_QPA_PLATFORM=xcb` under Xvfb.
- `node --test tests/*.test.mjs`: exit 0, zero tests (web UI retired).
- `git diff --check`: passes.

There are 50 additional tests compared with the reported 877-test baseline.

## Follow-up: indirect strokes, null appearances and punctuation

Three additional edge cases are fixed:

- Every stroke array inside `/InkList` is dereferenced consistently during
  reading, appearance regeneration, width changes and transforms. Both the outer
  array and its individual strokes may be indirect objects.
- A direct or indirect PDF null at `/AP /N` is treated as a missing appearance;
  recoloring regenerates a usable normal appearance.
- Comment direction uses the largest displayed text span instead of the most
  extreme aspect ratio. Small punctuation beside a word no longer changes
  horizontal line merging into vertical column merging. Horizontal and vertical
  examples are checked at all four page rotations.

Seven new tests cover these cases, including mixed direct/indirect PDF objects,
PDFium rendering, incremental byte-prefix preservation, and the native window's
recolor/resize/explicit-Save flow. The focused PDF, comments, editing, core and
citation run passes all 128 tests. Two relevant regressions also pass with Xcb
under Xvfb. The full-suite run recorded above predates this follow-up.

A local comment-geometry measurement merged 100 rectangles into 20 lines in
2.49 ms median over 50 runs, with 20.8 KiB peak traced allocation for one call
and 67,096 KiB peak process RSS. This is a synthetic measurement.
