# Verification

## Reference extraction spot check

The following checks use the original PDFs in `BibArticolo`; the files were read but not modified. Page numbers are PDF pages, not printed page labels. The extracted page text was inspected for the named authors independently of the reported counts.

- `Moschou2015.pdf`: the bibliography begins on PDF page 42. Ahn 2014 has two detected mentions, both on page 41; inspection of that page confirms the two occurrences. Antolin and Verwichte 2011 has six detected mentions: one on page 3 and five on page 41, including one that wraps to the next line. A grouped citation on page 41 contains an additional 2010 year following a 2012 citation; the current matcher does not associate that second year with Antolin 2010.
- `Mignone2007.pdf`: Aloy 1999 has one detected mention on page 2, and Alexiades 1996 has one on page 14. Inspection also found both names in the bibliography on page 27, which is excluded from those counts.

These are small spot checks, not a measured accuracy rate or proof of exhaustive coverage. Analysis is explicitly partial. The parser can miss bibliography layouts, wrapped or grouped citations, non-Latin author names, multi-author forms, and extraction errors; it can also mistake text for a citation. Same-surname and same-year bibliography entries are marked ambiguous rather than assigned individual counts. Named PDF destinations now identify bibliography entries for quick view, while citation ranking still uses text matches. Link events are not reconciled with occurrence counts, reference aliases are not grouped, and there are no citation-occurrence coordinates. Scanned pages require OCR, which is not provided. Manual review remains necessary.

Unpaywall was exercised with mocked provider responses, including invalid HTML; no live Unpaywall check was made because no contact email was configured. Live arXiv acquisition was verified in the later acquisition checks below. A missing email is reported separately from a negative search result.

## Reader interaction fixes (4 November 2026)

Real headless Brave interactions against the isolated QA library confirmed:

- No horizontal document overflow at 1440, 1024, 800 and 600 px; toolbar controls stay
  separate and the workspace starts below all toolbar rows. Both columns collapse
  and restore without covering the PDF. Sidebar rules no longer collide with PDF.js.
- Split fits its central viewport, including sidebar collapse. Existing-paper
  read-only preview, promotion to its editable tab, preview closure and closing all
  tabs complete without page errors.
- Drawing, native saved-annotation selection, recoloring and deletion persist
  through PDF save/reopen. pypdf independently found no Ink after the deletion check.
- A selected phrase creates an inline comment with its quote and PDF-coordinate
  rectangles. New anchors remain aligned after zoom and reopen; two-line selection
  creates two rectangles. Comment colors and deletion persist. Legacy page comments
  remain usable. Tests did not alter the main library or source PDFs.

Two integration failures were reproduced and corrected: the pinned PageViewport
has convertToViewportPoint, not convertToViewportRectangle; PDFDocumentProxy lacks
destroy, so teardown uses PDFDocumentLoadingTask.destroy. Capture now uses the text
layer's content bounds to avoid a 9 px page-border offset. Existing anchors produced
before that correction are not silently rewritten. Full browser multi-page capture
and rotation remain outside this accepted check, although viewport transforms have
unit coverage against actual PDF.js. The Python suite has 23 passing tests; Node
highlight-line and comment-anchor tests pass.

Additional same-session checks passed: newly drawn ink commits on pointerup,
autosaves without leaving Draw, and can be deleted immediately. New ink and
highlights can be selected in Select mode without reloading; recoloring and
deletion save successfully, and ordinary text selection still works afterward.
The native PDF.js page keeps content-box sizing despite the global reset,
which aligns ink with the pointer. Thin strokes receive a small hit tolerance
without changing the saved drawing. Final screenshot is
`tmp/qa/fixes/highlight-same-session-verified.png`.

## Follow-up: citation destinations, visibility and comment indicators

- Confirmed CSS collision: app sidebar `--outline-width` replaced PDF.js's 2 px
  selection stroke with 150–210 px. App variable is now `--sidebar-width`.
  Browser-selected highlight uses a 4 px outer stroke with no fill, leaving text
  readable. Selected ink has a transparent background and 1 px outline.
- Ink save and deletion returned 200. All seven Guarrasi pages retain identical
  decoded page-content streams and extracted text before/after the ink save.
  All 15 Reale pages retain original content streams and text in the selected
  highlight snapshot. Both source PDFs retain their imported SHA-256 hashes.
  Native editor conversion can save a new snapshot; selection is not claimed
  to leave the snapshot revision unchanged.
- Computed text contrast normal/hover: Delete selected 10.80/6.54; tab title
  6.65/7.21. Search placeholder and dark save status also have explicit light text.
- Every reference title is actionable. Guarrasi 2014 offers its existing PDF as a
  candidate for confirmation; linking opens the lower preview immediately, and
  subsequent title clicks open it directly. No page overflow at 1024/600 px.
- Named blue year-only links use exact destination mappings. Guarrasi 2014 opens
  the correct lower PDF with main scrollTop unchanged (1185); Guarrasi 2010 opens
  its own linking picker. Equation 9 still navigates page 3 -> page 2; mailto is
  untouched. Török 2004 opens its correct picker after Unicode extraction repair.
- Reale extraction now finds 106 entries and maps 102 named destinations. This is
  not total bibliography coverage. Unsupported/short/ambiguous target lines and
  some PDF layouts remain unresolved. Candidate author matching supports Unicode
  and multiword surnames, without automatic linking based only on those names.
- Cached extraction/destination versions refresh once. Tests cover retaining
  manual links/retrieval when an older parser merged entries; unresolved old
  associations stay visible with a review cue.
- A saved phrase comment adds a red dashed, transparent, pointer-transparent
  outline. Real browser checks covered save, sidebar closure, zoom, reopening
  without visiting Comments, and deletion. Markers appear/disappear correctly.
- Final Python suite: 31 tests passed. Node comment-anchor/highlight-line suites
  passed. Browser checks had no page errors. All annotation writes used QA data.

Evidence under `tmp/qa/fixes/`: `selected-text-visible.png`,
`drawing-selected-text-visible.png`, `torok-citation-picker.png`,
`blue-citation-split.png`, `persistent-comment-outline.png`.

Main service smoke also passed after restart: 68 library PDFs; Reale has 106
references/102 mapped destinations; Guarrasi suggests existing document 61; the
Török blue citation opens the correct picker, with no PDF writes or page errors.

## Follow-up: availability, acquisition and link correction

- Real browser: Select all selects eligible missing references; Clear all empties
  every checkbox, shows 0 selected and disables the search button. Single-item
  deselection works. Local and legacy entries cannot leak into selected requests.
- Main Reale corpus has automatic publication matches for Antiochos 1999,
  Antolin 2012, Bradshaw 2013, Guarrasi 2014, Peres 1982 and Reale 2012 in a clean
  analysis probe. In the actual main library, the user's Guarrasi manual link is
  preserved. Its blue citation and an automatically resolved Antolin title open
  split directly without a library modal. Main reading position stays fixed.
- Missing reference titles expand inline sidebar actions instead of opening the
  general library. Availability, Not searched, identification failure, provider
  setup, per-provider progress/results and batch summaries are visible.
- LIVE arXiv ID lookup/download/import succeeded for 1505.05333 (12,563,619-byte PDF)
  in isolated tmp/qa/oa-live. A separate synthetic bibliography referencing
  arXiv:1706.03762 exercised the real browser -> provider -> download -> import ->
  persisted link -> automatic split. Unidentifiable Example 2020 showed the exact
  provider reasons and Search finished: 1 identification needed. No page errors.
- arXiv DOI/ID and exact author/publication matching are deterministic, accept only
  unique verified results, cache repeated queries and space queries by 3 seconds.
  Unpaywall remains explicitly unavailable without a real contact email; live
  Unpaywall retrieval is not claimed. Unsupported citations stay identifiable as
  unresolved, with prefilled Google links.
- Root found a preexisting user link from Hood 2009 to the source Reale PDF itself.
  It is now flagged invalid while retaining the saved target for review; root did
  not reset it. New self-links are rejected and excluded from shared resolution.
- QA browser verified Reset PDF link removes only the association, keeps the PDF,
  and restores resolution. Change PDF excludes the source paper. The main invalid
  link shows reset/change controls; no PDF or user association was changed by QA.
- Publication evidence is cached by exact content hash and memoized per request.
  A&A/ApJ/ApJL/ApJS matches require exact publication details and author evidence;
  contradictory corrected author/year metadata and ambiguous files block matches.
  Availability checks the readable saved revision, not merely an existing original.
- Final Python suite: 44 tests passed. Main/QA services restarted after final code.

Evidence under tmp/qa/fixes/: acquisition-clear-all.png, main-clear-all-final.png,
reference-missing-inline.png, oa-live-split.png, main-auto-local-split.png,
invalid-link-controls.png. Test-only fixture is tmp/qa/oa-flow-fixture.pdf, imported
as QA document 7; its acquired arXiv target is QA document 8. Neither is main data.

## Manual PDF saves and draggable quick view (supersedes autosave checks above)

- PDF writes now happen only on explicit Save, Ctrl/Cmd+S or Save in the close
  dialog. Real QA browser: ink remained unsaved after 2.2 seconds with zero PUTs
  and unchanged revision. Cancel retained the editor; Discard closed it without
  a write and reopening restored the saved PDF.
- Explicit Save returned 200 and the ink survived reopening. Ctrl+S and Save in
  the close dialog each wrote once. A simulated 409 kept Save failed/unsaved
  state; no retry happened after two seconds. A new explicit Save succeeded.
- Dirty preview replacement respects Cancel/Discard. Editable preview promotion,
  tab switches and opening an already-owned dirty PDF generated zero PDF writes.
  The read-only copy said Last saved version; promoting it returned to the same
  dirty editable tab. A real browser beforeunload dialog was dismissed, retaining
  the reader and edits until explicit Discard.
- Preview Draw readiness exposed a PDF.js initialization/redraw race. The reader
  now waits for editor-layer readiness and holds the page inert during mode
  transitions; controls follow the confirmed mode. Repeated preview drawing and
  replacement then passed. Browser automation must wait for the actual preview,
  not the existing primary canvas, before testing it.
- Pointer drag changed a quick view from 358 to 508 px. Home/End reached 20/75%
  (179/671.25 px of an 895 px stack); arrows moved in 5% steps. Dragging beyond
  the parent clamps correctly. At 600 px no document overflow appeared; promotion
  removes the separator and reopening preview restores it. No PDF writes occurred.
- Save and status form one toolbar group; no standalone orphan Save row. Source
  PDFs remain immutable. Comments still use their explicit Save comment action.
- Node split-resize, comment-anchor and highlight-line suites passed. Python
  backend was unchanged in this increment; its last full suite had 44 passing tests.

Evidence: tmp/qa/fixes/explicit-save-verified.png,
explicit-save-navigation-verified.png, split-drag-verified.png,
split-drag-narrow.png, main-manual-save-split.png. PDF write tests used QA fixture
7 only; navigation tests drew into QA target 8 in memory and discarded the edits.
