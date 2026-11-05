# Citation analysis accuracy

How well `references.extract` finds bibliography entries and counts in-text
citations, measured against a hand-labelled gold set, and what changed as a result
(extraction version 2). Measured on 5 November 2026.

## Method

Six papers in the local library, chosen for different layouts. For each, every
bibliography entry (or a documented random sample for long papers) was labelled with
its true citations in the body and the page of each. Labelling used `pdftotext` and
the rendered page, independently of the extractor's regexes: every occurrence of each
entry's first-author surname was listed with its context and judged by hand.

Reading rules: numeric ranges are expanded; "Smith et al. 2010a,b" and
"Smith 2010, 2012" cite one entry per year; citations in captions, footnotes and
table cells count. A citation that cannot be decided from the text (two entries that
print the same author and year, or a suffix the bibliography lacks) is recorded as
uncertain and scored as neither a hit nor a miss. Name-only mentions without a year
("Sweet-Parker model", "Paper I") are not counted.

Scoring compares per (entry, page): a true positive is a detected occurrence on a page
where the entry is truly cited. A hit on the right page but the wrong sentence is not
detected by this measure. The gold files hold the private papers' text and stay in an
ignored directory, so they cannot be reproduced from the repository. The fixes were
tuned on this gold set, so its scores are optimistic; a separate check on other papers
is reported below.

| Paper style | Entries labelled | True citations | Undecidable |
| --- | --- | --- | --- |
| ApJ, emulateapj, author-year | 65 (all) | 100 | 0 |
| A&A, two columns, heading mid-page | 33 (all) | 45 | 2 |
| ApJS, 34 pages | 30 of 83 (random) | 39 | 14 |
| 1991 ApJL scan, two columns, repeated-author dashes | 20 (all) | 29 | 2 |
| Preprint, bibliography with the year last | 25 of 54 (random) | 50 | 11 |
| Numbered `[n]` (machine learning paper) | 40 (all) | 76 | 0 |

## Results

| Paper style | Entries found | Precision | Recall | Exact counts |
| --- | --- | --- | --- | --- |
| ApJ | 54/65 -> 65/65 | 0.97 -> 1.00 | 0.56 -> 0.98 | 46/52 -> 63/65 |
| A&A | 30/33 -> 33/33 | 1.00 -> 1.00 | 0.80 -> 0.98 | 25/30 -> 32/33 |
| ApJS (sample) | 26/30 -> 30/30 | 1.00 -> 1.00 | 0.77 -> 1.00 | 19/22 -> 27/27 |
| 1991 scan | 2/20 -> 16/20 | 1.00 -> 1.00 | 0.03 -> 0.69 | 1/2 -> 14/16 |
| Preprint, year last (sample) | 24/25 -> 25/25 | 1.00 -> 1.00 | 0.98 -> 0.98 | 21/22 -> 24/25 |
| Numbered | 40/40 -> 40/40 | 1.00 -> 1.00 | 0.89 -> 1.00 | 32/40 -> 40/40 |
| **All** | **176/213 -> 209/213** | **0.99 -> 1.00** | **0.71 -> 0.96** | **144/168 -> 200/206** |

"Exact counts" are entries whose count equals the true count, among entries that
received a count. Before the fix, 19 true citations sat in entries reported as
ambiguous (for example "Antolin et al. 2010" versus "Antolin & Shibata 2010"); now
only entries that really cannot be told apart abstain (two "Balsara 2001", two
"Mignone et al. 2010").

Precision was already high because the extractor only counts name-plus-year forms.
The gold set underestimates one false-positive class: a second author counted as a
first author ("Rappazzo & Velli 2011" counted for Velli 2011). Comparing old and new
output on nine library papers, 24 of the old 669 occurrences (3.6%) were of this
kind and are no longer counted. A hand check of 45 randomly chosen occurrences that
only the new version reports found them all to be genuine citations.

## What was fixed

- Detached accents and ligatures from PDF text ("M¨ uller", "Mart ´ ınez") broke entry
  detection and merged entries. Text is repaired before parsing.
- A space inside a year ("200 8"), an initial ("V .") or a name ("W atanabe"), and
  "Jr." in an author list.
- Year lists and suffix lists: "A et al. 2003, 2004", "A 2010a,b", "A (2010a, b)".
- A second author is not a citation of that author; a second author may carry
  particles ("Rouppe van der Voort").
- Entries with the same first author and year are separated by author count and second
  author instead of all being marked ambiguous. A citation that fits none or several
  entries still marks them ambiguous.
- Surnames hyphenated at a line end ("McKin-/ney").
- A heading in the middle of a two-column page no longer discards the body text that
  follows it.
- Numeric markers with inner spaces ("[ 1]").
- Scanned two-column pages: entries printed above the heading, several entries on
  one line, and repeated-author dashes.

## Known remaining failures

- Scans with OCR errors or interleaved columns: recall 0.69 on the 1991 scan. Entries
  with OCR damage in the name, or not preceded by a digit when joined to the previous
  entry, are missed. Reading text by position, as destination mapping already does,
  would fix the column order.
- Citations that list three or more authors by name ("Datlow, Elcan, & Hudson 1974").
- Forms without a plain name and year: "Fedun et al. (Submitted on 27 Jan 2011)", words
  glued together by kerning ("SeeKlimchuk (2006)"), aliases such as "Paper I", and a
  compound surname cited by its first part ("Mendoza et al." for Mendoza-Briceño).
- A citation suffix that the bibliography lacks ("2012a" against one entry "2012") is
  not counted, so the entry shows 0 instead of unknown.
- Numbered styles other than `[n]`: superscripts and `(n)` find nothing, and an interval
  such as "[1, 5]" would be counted as a citation of entries 1 and 5.
- The extracted `marker` text is repaired (accents composed), so the reader's
  text search may fall back to the page for those citations.

## Bibliography boundaries (extraction version 3)

Before this change the last entry of a list kept every line up to the end of the document:
"Figure Captions", tables, appendices and the axis numbers of figure pages. A real case was
`Winebarger 2011 ... 2011, Submitted. Figure Captions Figure 1 Active region ...` followed by the
text of several figure pages. The fix uses the document's structure, not a length cap.

- **Sections after the list end it**: a line that is a bare "Figure Captions", "Tables", "List of
  Figures", "Acronyms", "Data Availability" and similar, or starts with "Appendix",
  "Supplementary", "Supporting Information" or "Acknowledg...". Bare words must fill the line, so a
  wrapped title such as "Table of integrals" stays in its entry. Acknowledgements printed *before*
  the heading are body text, as before.
- **Floating figures and tables** ("Figure 14.", "Fig. 6.—", "Table 2") only close the entry before
  them; the list resumes at the next entry. Preprints and emulateapj papers place figures between
  reference pages, and stopping there lost entries in the first trial.
- **Figure pages** (at least 8 lines, half of them under 16 characters: axis ticks and labels) are
  skipped.
- **A citation ends** at a journal reference ("ApJ, 745, 152", "Phys. Rev. Lett. 99, 1 (2007)",
  "Journal 4, 2020"), a DOI, an arXiv id, a status ("Submitted", "in press", "in prep."), a thesis or
  a publisher, or a conference page ("(San Francisco: ASP), 235"). Status, publisher and page cues
  count only after the first year, because "Springer" and "Press" are surnames in author lists.
  After that, only a plausible continuation is appended (a wrapped DOI or URL, a page range, a
  parenthetical note, a line after a trailing comma); any other line closes the entry. An entry
  whose citation never ends (no cue) behaves as before.
- **Entries after a finished one** may also start as "Hood A. W., Browning P. K." (no comma after the
  surname) and with two-letter initials ("Zel'dovich, Ya. B."). Before, such entries were glued to the
  previous one and lost.
- **Outlier safeguard**: an entry longer than 6 times the median of its list is cut at its first
  citation end and marked `truncated`. It never fired on the library: after the rules above, the
  entries over 6 times the median are legitimate collaboration author lists and two scanned pages.
- Analyses are recomputed (`EXTRACTION_VERSION = 3`). A shortened entry inherits the manual link,
  retrieval state and identity of the over-long entry it came from through the existing prefix
  rule. Its minimum length dropped from 20 to 12 normalised characters because short entries such
  as `Baty, H. 2000, A&A, 353, 1074` (18) were left as legacy.

### Measurement on the library (130 PDFs, 7,210 entries before)

Run over the pypdf text of every PDF in a read-only copy of the library.

| | Before | After |
| --- | --- | --- |
| Entries | 7,210 | 7,416 |
| Median / p90 / p99 length | 75 / 212 / 382 | 73 / 208 / 323 |
| Longest entry | 12,818 | 5,595 (a 1988 scan with whole columns on one line) |
| Entries over 600 / 1000 characters | 40 / 29 | 7 / 3 |
| Entries over 3 times their list's median | 96 in 52 papers | 59 in 29 papers |
| Over 6 times / 10 times | 40 / 34 | 7 / 5 |

- 233 entries were shortened (median 132 -> 72 characters); 75 of them lost only a page number or
  page mark, the others a running header ("Article number, page 12 of 12", "This preprint was
  prepared with the AAS LATEX macros"), a caption, a table or figure text.
- 6,962 entries are unchanged; 15 entries of body text or appendix, parsed as entries before, are gone
  or merged into the entry they wrapped from.
- 221 entries have no trace in the old output (the other 233 changed entries are the shortened
  ones). Most are real references that were glued to the previous entry (Dere et al. 1997 went from
  one entry holding the whole list to 20). A sample of 30 was all genuine, and so were 25 sampled occurrences counted for them.
- The citation counts of the 6,962 unchanged entries are identical. Counted occurrences rose from
  10,286 to 10,553, all from the recovered entries.
- **Hand check of 20 changed entries** (two samples of 20, seeds 1 and 7): nothing that belongs to
  an entry was cut except a few words where the citation ends on a line break ("Submitted to
  Astron." + "Astrophys.", "D. Reidel Publishing" + "Company (Dordrecht ...)"), which does not
  affect the author, year, title or journal. Across all 233, about 18 real entries that the old
  parser had glued to the previous one are neither entries nor part of one now: their first line
  starts in a format the entry patterns do not know ("Kraichnan, 1965", "Handy, B N., et al.",
  OCR damage such as "Mul̈ler" or dashes "̈ ̈ ̈. 1998"). They were already unusable before.
- **Carry-over**: re-analysing the ten stored analyses in the copy (262 references with a manual
  link, retrieval state or identity) kept all 262 on new entries; 0 became legacy.
- **Gold set** (`tmp/gold`): unchanged. 326 true positives, 0 false positives, 13 false negatives,
  precision 1.00, recall 0.96, exact counts 200/206, 209/213 entries matched, before and after.

Remaining failures: an entry glued to the next one on one line by a scanned layout (Parker 1988),
mid-line entries with unusual author formats (above), and lowercase-initial OCR names.
