# Link-Based Citation Cross-Check Prototype

Historical prototype evaluation. The subsequent review found incorrect row assignments and counting/navigation gaps; the implementation and current validation are documented in [Reference link integration](reference-link-integration.md). The figures below describe the original prototype, not the corrected implementation.

The experiment compared text-regex citation counting in [`src/paperassist/references.py`](../../src/paperassist/references.py) against internal PDF link annotations (`/Link` -> `/Dest`), measured read-only across the 17 analyzed PDFs (`1,279` extracted references) in the local development corpus. At the time of this experiment, production code in `src/paperassist/` was intentionally left unchanged pending review.

## Problem Summary

In-text regex counting (`_citation` / `extract` in `src/paperassist/references.py`) frequently reports `citation_count == 0` for references that are genuinely cited, and occasionally over-counts citations due to author-year collisions or layout edge cases. Common failure modes observed in the corpus include:

- **3+ explicit authors without `et al.`** (for example `Fang, Xia, and Keppens, 2013`), whereas regex matching pairs the first surname directly with `et al.` or the year.
- **Compressed multi-year citations** (`Chou 1999; 2001`), where the second year has no repeated surname nearby.
- **Unrecognized multi-word surnames** (`Landini & Monsignori Fossi 1970`) lacking known nobility particles.
- **Typography and kerning artifacts** (`(20 05)`, `et a l.`) and **cross-page citation breaks** (`(Bommier` at page bottom followed by `et al. 1994)` on the next page).
- **Document-defined aliases** (for example defining `BC13` for `Bradshaw & Cargill 2013` and citing `BC13` 11 subsequent times).
- **Appendices with unrecognized headings**: lettered sections after `References` that do not match `END_START` or `END_HEADING` (such as `A Continuous Diffusion Language Model Survey` in numbered ML preprints) cause `_bibliography_page_range` to classify pages 11-42 as bibliography and skip all appendix citations.
- **False positives in regex matching**: a paper whose second author shares a surname and year with another entry's first author (`Kohutova & Verwichte (2017)` falsely counted under `Verwichte, E., & Kohutova, P. 2017`).

## Why PDF Internal Links Still Need Geometry

Reading raw PDF objects is already sufficient on the **source side** (in-text citation boxes): page `/Annots` entries with `/Subtype /Link` store direct destination names in `/Dest` or `/A -> /D` (for example `cite.fang_xia_keppens_2013` or `bm_apjlab0c1fbib5`). That removes all ambiguity about which named destination a citation box targets—including aliases (`BC13`), grouped citations (`[2, 3, 12]`), and kerning-split text.

However, reading raw PDF objects does **not** bypass anchor-to-entry geometry on the **target side** (the bibliography):

1. `16 / 17` analyzed PDFs are untagged (`tagged=False`, no `/StructTreeRoot`), so there is no structural XML/role-tree link between `/Dest` keys and bibliography paragraphs.
2. Named destinations use opaque or arbitrary author keys (`Item.N`, `bm_<hash>bib5`, `cite.2006ApJ...647.1452P`), and PDF `/Dest` objects map those names only to viewer coordinates (`[page, /XYZ|/FitR|/FitH|/FitBH, ...]`).
3. Therefore, linking body `/Link` annotations back to parsed `refs` rows still relies on mapping each named destination's `(page, left, top)` to the corresponding bibliography line (`map_destinations`).

## Prototype Design

Two prototype functions were tested against the 17-document corpus:

### 1. Relaxed `map_destinations`

Production `map_destinations()` (`src/paperassist/references.py`) required `/XYZ` destinations strictly within `abs(x - left) <= 3` pt and `-1 <= top - y <= 4` pt. That failed completely on `7` linked documents (`0` mapped refs) and matched only `4 / 59` in the thesis PDF due to typical `hyperref` anchor offsets (`dx = 5..86` pt, `dy = +6..+9` pt) and `/FitR` targets.

The prototype adds five targeted fixes:

1. **Support `/FitR`, `/FitH`, and `/FitBH`**: In addition to `/XYZ`, extract `top` from `/FitH`/`/FitBH` and `max(top, bottom)` for `/FitR`. Using `max(top, bottom)` is required because some publishers (observed in IOP/ApJL `/FitR` destinations) emit inverted coordinates where `bottom > top` (`bottom=665, top=656`), which otherwise shifts every entry down by one bibliography row (`bm_...bib5` mis-mapping to `bib6`).
2. **Wider anchor tolerance (`-5 <= top - y <= 14`, `-10 <= x - left <= 120`)**: Accommodates hyperref vertical anchor placement above the text line and hanging indents without cross-matching neighboring columns.
3. **Baseline-first grouping with leftmost `x`**: Group candidate text spans by rounded baseline (`0.1` pt), rank baselines by `(dy, dx)`, and collect row fragments starting from `left - 10` rather than starting from the closest candidate span's `x`. Otherwise, a mid-line tall glyph (`Å` or an italic token like `TMLR`) whose ascender sits fractionally closer to `top` sets `cand_x` mid-row and drops the author prefix, breaking `raw.startswith(nline)` and causing fallback to the next line down.
4. **Repeated-author em-dash bibliography rows**: Entries written as `—. 1993, A&A, ...` are expanded during `extract()` into `Author 1993, ...`, causing `raw.startswith(nline)` to fail. Stripping leading dash prefixes (`^[—–-]{1,3}\.?\s*`) and matching unique substrings (`len >= 12`) recovers them.
5. **Structural-destination and backref guards**: Ignore LaTeX structural destinations (`page.*`, `section.*`, `subsection.*`, `figure.*`, `table.*`, `equation.*`, `Hfootnote.*`, `footnote.*`, `Item.*`, `aalinkref.*`), which otherwise collide with bibliography entries at page tops (`page.7` linked from page-footer `page 1 of 7` numbers inflated `Arber 2001` from `1` to `7`). Also require `len(nline) >= 12` when matching `raw == nline` so backref page-number artifacts (`"9"`, `"1, 3"`) cannot hijack valid `cite.*` destinations.

### 2. Body `/Link` Extraction & Box Merging (`extract_link_occurrences`)

For each mapped destination name `d` in `ref["destinations"]`:

1. Scan `reader.pages` `/Annots` for `/Subtype /Link` annotations whose `/Dest` or `/A -> /D` resolves to `d`.
2. **Exclude bibliography regions safely**: Across all `17` PDFs, `0` bibliography entries contained `/Link` annotations pointing to another mapped citation destination, whereas body/appendix citations often share a page with `References` (e.g., Conclusions above `References`, a left-column Appendix on a two-column `References` page, or 25+ pages of lettered appendices after `References`). Using `[heading_page .. max(dest_pages)]` with column-aware `heading_xy` filtering on `heading_page` preserves appendix and split-column citations without including bibliography entries.
3. **Merge split bounding boxes per `(page, dest)`**: Combine boxes on the same line (including slight `-3 pt` horizontal overlaps such as `Author` + `Year` links) and wrapped line continuations (including `1 pt`-high degenerate boxes observed in two-column papers such as `Doc 14`).

## Measured Corpus Results

Running the prototype read-only across all `17` analyzed documents (`1,279` extracted references):

| Metric | Production `map_destinations` | Prototype `map_destinations` + Links |
|---|---:|---:|
| References with mapped destinations (all 17 docs) | `324 / 1,279` (`25.3%`) | `649 / 1,279` (`50.7%`) |
| Documents with `> 0` mapped destinations | `6 / 17` | `13 / 17` |
| Destination coverage across the 13 linked docs (`670` refs) | `324 / 670` (`48.4%`) | `649 / 670` (`96.9%`) |
| Text-regex zero counts in the 13 linked docs | `50` | `50` |
| False zeros recovered by `/Link` annotations | `—` | `35 / 50` (`70.0%`) |
| Remaining zeros across the 13 linked docs | `—` | `15` |

> **Note on the 4 unlinked documents (`609 / 1,279` refs)**: Three older/journal PDFs (`Doc 20`, 6 pp.; `Doc 74`, 140 pp. with 555 extracted refs; `Doc 117`, 36 pp.) and one other PDF (`Doc 165`, 25 pp. with 5 extracted refs) contain `0` mapped bibliography destinations (`Doc 74` has only `2` bibliography-pointing links), accounting for `67` of the `82` corpus-wide `still_zero` entries. Links cannot recover citations in unlinked PDFs.

### Breakdown of the 15 Remaining Zero Entries in Linked Docs

Inspection showed that only **1** mapped entry in the linked author-year corpus was truly uncited (`Doc 39`, `Parker 1988`: 0 text mentions outside the bibliography and 0 links). None of the other 10 author-year linked papers had any remaining zeros (`still_zero = 0` in `Doc 7`, `Doc 14`, `Doc 19`, `Doc 41`, `Doc 56`, `Doc 58`, `Doc 61`, `Doc 81`, `Doc 84`, `Doc 131`):

- **Doc 39 (`1` zero)**: genuine uncited bibliography entry (`Parker 1988`).
- **Doc 139 (`1` zero)**: bibliography extraction split artifact (`"Brussels 2018"`, continuation of the preceding entry).
- **Doc 141 (`13` zeros)**: `9` are split backref page-number artifacts (`"6"`, `"9"`, `"1, 3"`, etc.) created by `extract()` rather than real entries, and `4` are genuine numbered entries (`[47]`, `[59]`, `[73]`, `[96]`) whose text rows did not match their destination lines (one of which, `[47]`, is linked in the appendix).

### Per-Document Results (13 Linked Documents)

| Doc | Style / Pages | Refs | Dest (Old -> Proto) | Text Zeros | Recovered by Link | Remaining Zeros |
|---:|---|---:|---:|---:|---:|---:|
| 7 | Two-col author-year (7 pp.) | 40 | 40 -> 40 | 0 | 0 | 0 |
| 14 | Two-col author-year (8 pp.) | 63 | 0 -> 62 | 1 | 1 | 0 |
| 19 | Double-spaced manuscript (38 pp.) | 75 | 0 -> 70 | 1 | 1 | 0 |
| 39 | Two-col author-year (7 pp.) | 27 | 27 -> 27 | 1 | 0 | 1 (uncited) |
| 41 | Two-col `/FitR` (8 pp.) | 20 | 0 -> 20 | 1 | 1 | 0 |
| 56 | Two-col author-year (15 pp.) | 106 | 106 -> 106 | 3 | 3 | 0 |
| 58 | PhD thesis (123 pp.) | 59 | 4 -> 58 | 16 | 16 | 0 |
| 61 | Two-col author-year (7 pp.) | 33 | 0 -> 33 | 1 | 1 | 0 |
| 81 | Two-col author-year (10 pp.) | 22 | 0 -> 22 | 0 | 0 | 0 |
| 84 | Two-col author-year (13 pp.) | 37 | 0 -> 37 | 4 | 4 | 0 |
| 131 | Two-col + App. (15 pp.) | 27 | 0 -> 27 | 0 | 0 | 0 |
| 139 | Numbered preprint (40 pp.) | 53 | 52 -> 52 | 2 | 1 | 1 (artifact) |
| 141 | Numbered ML + App. (42 pp.) | 108 | 95 -> 95 | 20 | 7 | 13 (9 artifacts, 4 unmapped) |

## Remaining Edge Cases Found During Evaluation

While the prototype eliminates nearly all false zeros in linked papers, manual inspection of `text_count != link_count` discrepancies revealed four remaining issues to address before production integration:

1. **Over-merging consecutive lines in double-spaced manuscripts**: In double-spaced ApJ manuscripts (`Doc 19`), two *separate* citations to the same paper on consecutive lines (`Ramsey & Smith 1966` on line $n$ and `Ramsey & Smith (1966)` on line $n+1$, `9.9` pt gap with `h = 14` pt) satisfy the prototype's `prev[1] - r[3] < h * 2.2` wrap condition, lowering `link_count` from `2` to `1`. A wrap merge across consecutive lines should check either tight inter-box vertical contact (`<= 2` pt) or inspect the text inside `prev` to verify it is an incomplete prefix (no trailing year/number) before merging onto the next line.
2. **Duplicate coordinates / bibliography extraction splits (`Doc 58`, `Doc 141`)**:
   - In `Doc 58`, `Moschou 2015` remained unmapped because a continuation fragment (`"Ad-"`) had the exact same `(x, baseline)` in `extract_text(visitor_text=...)` and sorted before `"Moschou..."` alphabetically; sorting row spans by `(round(x, 1), stream_order)` instead of text value fixes this.
   - In `Doc 141`, backref page numbers appended at the end of wrapped lines were split into standalone fake reference rows by `extract()` (`9`, `1, 3`), creating spurious `citation_count == 0` entries and leaving `4` numbered refs unmapped because `260` pt horizontal row truncation is narrower than single-column lines.
3. **Unlinked body mentions (`text_count > link_count`)**: Authors occasionally write an unlinked prose citation alongside hyperlinked ones (observed in `Doc 14` and `Doc 141`). Therefore, link counts should **supplement** rather than blindly overwrite text counts: e.g., when destinations map cleanly (`len(ref["destinations"]) > 0`), `citation_count` can be reconciled conservatively or cross-checked via `max(text_count, link_count)` once wrap over-merging is fixed and regex false positives (like `END_START` / second-author collisions) are addressed.
4. **Coalesced `cite` links vs multiple labels sharing a destination**: Some grouped `hyperref` targets in double-author/year strings (`Doc 14`, `Doc 84`) still differ between `text_count` and `link_count` (`±1` to `±3`) when multiple occurrences sit in unlinked figures/captions or grouped ranges `[1-4]` (which link only the endpoints in numbered papers).

## Original Recommendation (Superseded)

The prototype suggested the following two stages. Review subsequently showed that wider tolerances and nearest-font-top matching can assign a destination to an adjacent entry; mapped-reference coverage alone does not establish accuracy or native navigation coverage. The corrected implementation uses baseline geometry, abstains on unrecognized rows, and reconciles individual events rather than taking the maximum of two counts.

1. **Upgrade `map_destinations()`** in [`src/paperassist/references.py`](../../src/paperassist/references.py) with `/FitR` (using `max(top, bottom)`), `/FitH`/`/FitBH`, relaxed `-5..+14` pt / `-10..+120` pt anchor matching, baseline-first leftmost-`x` grouping, em-dash entry matching, and structural destination filtering. Even before changing citation counting, this immediately enables click-to-jump and citation reveal (`src/paperassist/native/citations.py`) across `96.9%` of references in linked PDFs (up from `48.4%`).
2. **Add `/Link`-based cross-checking as a second phase**, pairing `extract_link_occurrences()` (tightened wrap-merge rule so consecutive-line double-spaced citations stay distinct) with improved `END_START` / `extract()` handling for lettered appendices (`Doc 141`), and using link matches to recover `citation_count` whenever text regexes miss multi-author, compressed-year, kerning-broken, or alias citations.
