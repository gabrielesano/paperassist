# Reference parsing and acquisition follow-up

Implemented on 13 November 2026 after the initial [link integration](reference-link-integration.md), committed as `cfac1fd`.

## Bibliography boundaries and identity

Wrapped lines such as `2021. 6` or `2023. 1, 3` now stay with their preceding entry instead of becoming bibliography entries numbered 2021 or 2023. Numbered lists no longer treat an unnumbered conference venue as a new author. The two numbered preprints in the evaluation corpus contain 52 and 99 real parsed rows, previously 53 and 108; the ten removed rows were the venue/backreference artifacts identified during review.

Lettered headings such as `A Additional Results` and `A.1 Setup` can end a completed bibliography. Page-start headings after long numbered conference entries are also recognized when there is sufficient title/year evidence. Wrapped titles inside an unfinished entry remain intact. Author/title running headers already seen at the start of body pages are excluded from this heading decision; this avoids truncating a bibliography when it continues on the next page.

Text after a recognized appendix boundary remains body text, with its original page numbers, including figure-heavy appendix pages. Existing treatment of separate post-bibliography caption/table sections remains conservative.

Reanalysis keeps existing entry IDs for unambiguous unchanged rows and for matching numbered rows repaired by the parser. New rows receive unused IDs. Removing an artifact therefore cannot move an acquisition selection onto the next paper. Existing manual-link, retrieval and identity preservation also remains in effect. Extraction version 5 triggers reanalysis; PDF bytes and revisions are unchanged.

## Text grammar and split events

The text detector now supports:

- Explicit lists of three or more authors when the names agree with the bibliography, including distinguishing different author lists sharing a first author and year.
- Known compound second surnames, without relaxing arbitrary prose into citation grammar.
- Compressed year lists separated by semicolons.
- Spacing damage inside a year or `et al.` and possessive forms such as `Smith's (2000)`.
- Adjacent-page citations: the text match retains a separate marker for each page, while contributing one event.

Flattened bibliography text can split at terminal periods as well as numbers. A fragment resembling an editor's name cannot start a new same-line entry without supporting year or completed-entry evidence. This preserves editor lists while recovering bibliography rows from an older scan whose entire list was extracted as one line.

Link-only fragments across adjacent pages merge only with matching destinations, an incomplete author prefix near the bottom of the first page, and a compatible year continuation near the top of the next page. A complete citation at each page edge remains two events. Numeric and other cross-page citations can also be detected through the text grammar; uncertain link-only joins remain separate.

Multi-page occurrences keep their primary page/rectangles plus page-specific `continuations`. Geometry is persisted in PDF coordinates and transformed independently for each page. The native count button visits the event once and outlines both parts. Text/link reconciliation compares every part, avoiding a duplicate text event for the same split citation. Rotated-page text ordering is handled when locating page-break fragments.

## Acquisition layout

Reference captions use a native checkbox with Qt text layout for wrapping at word boundaries or within an unbroken identifier. Full titles, ampersands, Unicode names, tooltips, computed accessible names, keyboard Space, and clicking wrapped caption lines remain supported. The height follows available width and font changes; the checkbox indicator stays beside the first line. No selection or download starts simply because the dialog is shown or resized.

## Verification

Synthetic regression fixtures cover parser boundaries, backreference artifacts, stable IDs, editor lists, new citation forms, multi-page geometry, all four rotations, distinct page-edge citations, and native navigation. The acquisition regression covers long Unicode text and URLs, accessible names, caption clicks, keyboard selection, and doubled font size. Native-window tests run against disposable libraries and verify revision zero and unchanged source hashes.

```bash
.venv/bin/python -m unittest tests.test_references tests.test_reference_text tests.test_reference_links -v
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p test_reference_link_window.py -v
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p test_native_acquisition.py -v
```

The source regression run covers 853 tests in 54 isolated modules, with eight expected skips. The acquisition test also passes with XCB/Xvfb. An initial font-change assertion ran before nested Qt layout requests had completed; the regression now waits for the event loop, and both offscreen and XCB runs pass. Frozen/installed-package acceptance is separate from this source validation.

The existing six-paper gold set now reports 330 true positives, zero false positives and nine false negatives, with 201/206 exact counts and 209/213 matched entries. Before this follow-up the corresponding figures were 329 / 0 / 10 and 200/206. This remains a development set, not an independent accuracy estimate.

Two additional modern papers were labelled from Poppler text and rendered pages before their parser output was examined. One ten-entry sample has 12 true citations and the fresh six-entry sample has 11; all 23 were detected with no extras. A missed manual label in the first sample was corrected after verifying its rendered page; no parser rule was changed to fit that correction. The fresh sample's labels were unchanged on the final rerun, including after a running-header fix driven by the original corpus. These are small author-year samples, not a representative cross-domain benchmark.

An older unlinked scan was initially examined as another external sample. Its collapsed bibliography exposed the splitting issue and it was then explicitly reclassified as a development/diagnostic sample. After the fix, all eight sampled entries are extracted and 14 of their 15 true citations are detected without extras. The remaining miss uses a surname spelling that differs from its bibliography entry. This sample is not included in the independent accuracy claim.

Private labels, text, rendered pages, hashes and generated JSON stay in ignored QA directories. Generated synthetic fixtures and the read-only corpus comparison script remain reproducible from the repository.

Final read-only comparison on the original 17 PDFs:

| Measure | Initial link integration | Follow-up |
| --- | ---: | ---: |
| Extracted rows | 1,279 | 1,269 |
| Rows with mapped destinations | 655 | 655 |
| Zero counts after reconciliation | 78 | 52 |
| Mapped destinations lost | — | 0 |

The reduction in row count is exactly the ten known artifacts, not a loss of bibliography coverage. In the 13 linked documents the denominator is now 660 rows, with 655 mapped (99.2%). Mapping coverage remains distinct from correctness. The comparison script's raw-text owner-change flags also include repaired entry text; they are not automatically changed work identities. All original hashes remained unchanged, and SQLite was opened read-only.

The sequential comparison peaked at approximately 190 MiB RSS; cross-checking took approximately 13 seconds total, with a maximum of 1.9 seconds for one document in this run. In the actual native window, background analysis and first rendering of the disposable 123-page paper completed in 5.34 seconds, with 310 timer observations, a maximum 256 ms timer gap, and 352 MiB peak main-process RSS. The PDF remained unmodified. These are individual local observations, not performance guarantees or a controlled speedup comparison.

Visual checks at 680 and 560 logical pixels and 150% scaling showed wrapped captions and identifiers with no horizontal scrollbar. The full source title remains available to accessibility APIs and tooltips.

## Remaining limits

Arbitrary spelling/OCR corrections, image-only papers without an extractable text layer, unknown author formats, and ambiguous bibliographic identities remain partial evidence. No approximate-name match is silently accepted. Column-spanning or malformed link geometry can still leave fragments separate; conservative joins prefer an extra unresolved fragment over merging distinct citations. Name-only aliases without links or an explicit year remain outside text-only counting. The small external checks do not validate every recovered event in the full corpus.
