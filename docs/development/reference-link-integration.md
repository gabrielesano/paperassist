# Reference link integration

This records the initial integration in `cfac1fd`. Subsequent bibliography parsing, cross-page events, text grammar, and acquisition-layout changes are documented in [Reference parsing follow-up](reference-parser-followup.md).

The [original prototype](link-citation-crosscheck.md) improved destination coverage but could assign a citation to an adjacent bibliography entry. It also merged separate consecutive-line citations and excluded valid citations on bibliography/appendix pages. This implementation corrects those paths and uses one reconciled occurrence list for ranking and native navigation.

## Mapping

`references.map_destinations` supports `/XYZ`, `/FitR`, `/FitH`, and `/FitBH`. Rectangle destinations use the greater of their two vertical coordinates, including inverted publisher rectangles. The expanded search window accommodates hanging indents and anchors above the text.

Rows are ranked by distance to their baseline, not the estimated top of the tallest glyph. In compact bibliographies an anchor can sit inside the font-height band; choosing the nearest font top mapped several author-year destinations to the following entry during validation. Once a baseline is selected, failure to recognize its text leaves the destination unresolved. There is no search for a more convenient entry on another row. Equidistant baselines and nonunique entry matches also remain unresolved.

Fragments sharing horizontal coordinates retain content-stream order. Repeated-author dash rows require a unique text match. Numeric labels must agree. Column limits come from other destination columns where available, allowing long single-column lines. Height-only destinations without horizontal evidence abstain if the combined row cannot identify an entry uniquely.

Explicit page/section/figure/table and similar structural destinations are excluded. `Item.*` and `aalinkref.*` are eligible when their row text identifies a bibliography entry; their names alone are insufficient evidence for rejection.

## Counting and navigation

`references.crosscheck_links` reuses the native PDFium citation index, which handles named `/Dest` and `/GoTo` actions. On bibliography pages the index masks recognized entry rows, rather than excluding complete pages or columns. Body text beside the bibliography and later appendices remain eligible.

Link fragments merge by destination and geometry, with text evidence distinguishing an incomplete prefix from a complete year/number citation. Separate complete citations on consecutive lines stay separate. Nearly zero-height continuation boxes and small overlaps remain supported. Identical annotation rectangles do not create duplicate events. Merging occurs in PDF coordinates, independently of page rotation.

Regex occurrences are located in the page text and reconciled with link rectangles by intersection. A text/link overlap contributes one event; an unlinked mention contributes its own event. This is a positional union, not `max(text_count, link_count)`. Ambiguous counts remain unknown. All results retain their existing partial-analysis status.

Reconciled occurrences preserve page, available context, detection source, and PDF-space rectangles. The native count button reads this same stored list, transforming rectangles for crop and rotation. It no longer discards same-page or post-bibliography events from a fresh analysis. Older name-only callers without bibliography geometry retain a conservative exclusion of the destination page.

Extraction version 4 and destination version 6 invalidate earlier analyses. Reanalysis preserves the existing manual-link, identity, and retrieval carry-over rules. Only analysis state changes; PDF revisions still require explicit Save. PDFium calls are serialized within each process because background analysis can overlap foreground reading; rendering remains in the existing worker process.

## Reproduction and verification

The regression fixtures are generated from synthetic text and links and contain no private PDFs:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_reference_links.py' -v
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p 'test_reference_link_window.py' -v
```

Tests cover incorrect-row fallback, anchors inside font-height bands, inverted rectangles, height-only ambiguity, structural names, repeated authors, fragment ordering, long lines, split/submitted-year citations, degenerate boxes, duplicate annotations, text/link deduplication, and ambiguous counts. The combined fixture exercises four page rotations, an unlinked mention, a linked bibliography row, a same-page appendix, and a later appendix. Source hashes remain unchanged.

The native-window test opens a disposable library, migrates a stale analysis while retaining retrieval state, clicks the count button through five positions and wraps, and verifies that the document stays unmodified at revision zero. It also passes with the X11 Qt backend on a private Xvfb display.

The source regression run covered 828 tests in 53 modules, with 8 expected skips. Modules ran in separate processes to isolate Qt object lifetimes. The shortcuts module passed with its normal offscreen backend after focus-dependent failures under bare Xvfb; the new reference-window test also passed on Xvfb. Installed/frozen artifact acceptance is separate and was not counted as source validation.

The corpus comparison is now a repository script. Run against an existing local library; it opens SQLite read-only and hashes originals before and after. Output contains local document IDs and aggregate measurements, not filenames or paper text. Keep the generated JSON ignored:

```bash
.venv/bin/python scripts/benchmark_reference_links.py \
  --data-dir data --baseline-ref HEAD \
  --output tmp/qa/reference-links/benchmark.json
```

`--baseline-ref` must identify the pre-integration revision when comparing against the original implementation. Timings for the baseline include initial pypdf text extraction; current extraction/mapping timings reuse that text and must not be presented as a speedup.

Read-only comparison on the same 17 PDFs / 1,279 extracted entries:

| Measure | Previous implementation | Corrected implementation |
| --- | ---: | ---: |
| Entries with mapped destinations | 324 | 655 |
| Coverage in the 13 documents with mapped destinations (670 entries) | 48.4% | 97.8% |
| Previously zero counts with detected link events | — | 39 |
| Remaining zero counts over all 17 documents | 117 | 78 |

No retained baseline destination changed entry ownership. The removed baseline destination was the structural `page.7` target previously assigned to a bibliography entry; no entry lost all its baseline destinations. All 17 original hashes were unchanged. Cross-checking added approximately 11 seconds total, at most 1.6 seconds per document in this run; peak process RSS was approximately 188 MiB for the sequential comparison. These are local observations, not performance limits or incremental reader-memory measurements.

A real native window opening a disposable copy of the 123-page linked paper completed background analysis and first rendering in 5.4 seconds, with 337 timer observations and a maximum 221 ms timer gap; peak main-process RSS was 349 MiB. A control run disabling only cross-checking took 4.3 seconds, with a 356 ms maximum timer gap and 302 MiB peak RSS. These single runs include ordinary PDF parsing, Qt, and initial rendering; timing variation does not establish a responsiveness improvement. Both left the source hash and PDF revision unchanged.

The existing six-paper hand-labelled gold set, scored per entry/page with uncertain citations neutral, reports 329 true positives, 0 false positives and 10 false negatives after reconciliation (previously 326 / 0 / 13). Exact counts remain 200/206. This gold set was used while correcting the implementation; it is not an independent holdout.

## Remaining limits

Mapped-entry coverage is not a precision score. The corpus comparison does not independently validate every newly assigned destination. PDFs without usable internal bibliography links still rely on text detection. Unrecognized bibliography rows, unusual names, damaged OCR, grouped ranges with only endpoint links, and page-only text matches remain partial evidence. A malformed link rectangle can still give an imprecise highlight. Cross-page link fragments are not unified into one multi-page citation event. The original bibliography-splitting/backreference artifacts and general recognition of lettered appendix headings remain separate parser limitations.
