# Native Library and import parity review

Review of the four unchecked Library/import rows on 2026-11-07. No production code was changed. The classifications below apply to the full wording of each row; differences shared with the web are identified explicitly.

1. **Search titles, authors, year, DOI and metadata status without hiding unidentified/scanned PDFs — partially implemented.** `library.py:188` searches all listed fields, filenames and raw metadata status; `native/library_dialog.py:121` displays the results and leaves unidentified PDFs visible. In a real Qt window with a synthetic blank PDF, an empty query and its filename each returned the paper; `needs_review` returned it, but the displayed label `Needs review` did not. Match displayed status wording (and the equivalent incomplete status) in search. The web calls the same backend (`web/app.js:650`), so this is a shared product usability gap, not a native regression.

2. **Accept filesystem paths for deterministic recursive imports without following directory symlinks — verified.** `native/library_dialog.py:190` selects a directory and `:230` sends paths to Workspace; `library.py:203` traverses each directory in sorted order and skips all symlinks, including directory links. Existing tests exercise recursive imports, and the native selection test verifies skipping a file symlink. Directory symlink behavior follows directly from the same unconditional check. No claim is made about atomicity in a filesystem changing during traversal.

3. **Show incremental progress, cancellation, added/duplicate/review/error counts and per-file failures; import opens no tabs/downloads — partially implemented.** The watcher, progress and cancel controls are wired at `native/library_dialog.py:94`, `:230`, and `:242`; `library.py:223` cooperatively cancels between files and retains per-file errors. The summary at `native/library_dialog.py:249` shows added, duplicate and failures with paths, but omits the requested review count (and omits an explicit zero error count). It does not open tabs or launch downloads. Selection pre-scan swallows directory errors (`native/library_dialog.py:18`) and, if it finds no PDFs, stops before the importer can report them (`:195`); an unreadable-only selection is therefore described as no PDFs rather than a failure. Implement a review count and propagate pre-scan errors; report zero counts if the acceptance wording requires all counts always visible. Cancellation during scanning is not provided; processing cancellation is between files.

4. **Link a reference through the library picker, rank suggested matches and require the user to verify them — partially implemented.** The picker (`native/acquisition.py:401`) excludes the source, groups and marks suggestions, and requires an explicit selection and Link action; the backend generates scored, ranked candidates (`references.py:409`, `:444`). The picker converts candidates into a set and sorts only by membership, losing their ranking (`native/acquisition.py:413`). In a real Qt window, candidate order Zulu, Alpha appeared as Alpha, Zulu. Preserve candidate order when sorting suggestions. Web similarly groups candidates by membership (`web/app.js:654`), so the rank-order gap is shared rather than a native regression. Verification means the user is prompted to inspect and explicitly link, not that an additional confirmation dialog exists.

## Verification and limits

- `QT_QPA_PLATFORM=xcb xvfb-run -a .venv/bin/python /tmp/parity_library_check.py` outside the sandbox: passed the live Qt search and candidate-order observations above, with a disposable Workspace and generated blank PDFs; no personal data or active service was touched. The initial sandboxed Xvfb attempt failed to connect to its display socket; the same command ran with display access approved.
- `xvfb-run -a .venv/bin/python -m unittest discover -s tests -p test_native_metadata.py -v`: 13 passed, using the test suite's default offscreen Qt platform; covers native folder selection/import, symlink file exclusion and metadata editing.
- `.venv/bin/python -m unittest tests.test_library tests.test_workspace -q`: 42 passed; includes recursive paths, per-file errors, duplicate handling, progress and cooperative cancellation.
- No end-to-end cancellation click, actual unreadable directory, or full reference-link persistence was exercised in the live-window probe; those conclusions are code-derived or covered by the cited existing tests. The temporary probe is not a durable test fixture.

## Follow-up: displayed metadata status search

Resolved on 2026-11-07. The shared `Library.list_documents` search now includes the
visible "Metadata needs review" label for both `needs_review` and legacy
`incomplete` records. "Needs review" also matches that label. Raw status queries
remain supported; corrected and verified records do not gain the review label.
This resolves item 1 for both native and web callers.

Verification: 33 Library tests passed, including all four statuses, label variants,
case and surrounding whitespace. The 14 native metadata tests passed offscreen
and on XCB/Xvfb. A Qt regression test types the displayed status through the
Library field's debounce, verifies the unidentified PDF appears, confirms its
metadata and verifies it leaves the filtered results. No source PDF is modified.

## Follow-up: import summary and scan failures

Resolved on 2026-11-07. The native import summary now reports added, duplicate,
metadata-review and failure counts, including zero reviews/failures. Both
`needs_review` and legacy `incomplete` count as requiring review; corrected
copies do not. Failed files retain their path and reason.

The selection pre-scan now records traversal and size-read errors. An unreadable-only
selection shows its error instead of "No PDF files". A partial selection displays
the affected paths and can still import readable PDFs; its total storage estimate
is explicitly unavailable. The backend rescans the original paths and reports
persisting scan errors in the final job result.

Verification: 17 native metadata tests passed offscreen and on real XCB/Xvfb Qt
windows. New regression coverage includes mixed success/corrected duplicate/invalid
PDF results, unreadable-only selections and partial readable/unreadable imports.
Permission failures are injected at `os.scandir`; the Workspace/import and Qt
controls are real. Also passed 32 library-window tests (including job progress and
Cancel) and 43 Library/Workspace tests. No source PDF was modified.

## Follow-up: suggested-reference candidate order

Resolved for the native picker on 2026-11-07. The picker now retains each
candidate's backend rank instead of only candidate membership. Suggested PDFs
appear first in that order; remaining results retain the library's title order.
Filtering preserves the relative ranks. The source PDF is excluded and a manual
Link action is still required; suggestions do not create automatic associations.

Verification: 33 library-window, 22 acquisition and 52 reference tests passed.
The new Qt/Workspace regression uses actual metadata and backend candidate scoring:
a first-author match titled Zulu precedes a weaker Alpha match, unrelated PDFs
follow in title order, and filtering preserves the suggestion order. Selecting
and linking the first result persists its exact document ID as a manual target.
That test also passed in a real XCB/Xvfb window. The transitional web picker was
not changed by this native correction.
