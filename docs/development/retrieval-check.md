# Reference retrieval check (5 November 2026)

Live check of the app's own acquisition (`POST .../references/download`, one reference per
job, sequential, as the UI does) against the real services, on a disposable copy of the
QA library. Crossref and Unpaywall ran with the contact address configured through the
environment; ADS, CORE and OpenAlex keys were not configured. Sample: 31 references taken
from four ApJ/A&A papers (Reale 2016, Hood 2016, Guarrasi 2014, Rappazzo 2013), chosen
to mix journals (ApJ, ApJL, ApJS, A&A, SoPh, SSRv, PhRvL/E, LRSP, Nature, Science,
GeCoA), years (1989-2016) and one thesis. Same sample before and after the fixes below.

## Results

| Status | Before | After |
| --- | --- | --- |
| downloaded | 22 | 24 |
| not_found | 3 | 3 |
| blocked | 1 | 3 |
| failed | 2 | 0 |
| not_identified | 2 | 1 |
| ambiguous | 1 | 0 |

Sources of the downloads: before arXiv 20, OpenAlex 2; after arXiv 21, OpenAlex 3.
Semantic Scholar, CORE and Unpaywall never supplied a PDF; ADS was not configured.

**Wrong downloads: none, before or after.** (This check was manual; `download` now makes it itself, see below.) The first page of every downloaded PDF
was extracted and checked for the identified first author (spacing-insensitive, because
small-caps author lines extract as "H OOD") and for the identified title words. All 46
passed; the three first-author misses in the first pass were the small-caps artefact and
were confirmed by reading the pages.

## Changes the run led to

- **Science, ADS-style page (`Sci, 346, D315`)**: before, not identified; after,
  identified and the arXiv preprint downloaded (its first page carries the cited title
  and first author). The rule is deliberately narrow: journal Science, page of the form
  letter plus digits, and exactly one Crossref article in that volume and year with that
  first author, an electronic ID and no printed page. Two candidates give `ambiguous`; an
  article with a printed page or another journal never qualifies. The match is marked
  `page_check: relaxed` in the evidence. It cannot prove that "D315" maps to this
  article, so the first-page check remains the safeguard.
- **Plain-HTTP redirect (Velli 1990 through a DOI link)**: was `failed` with "Only public
  HTTPS downloads are allowed", which suggested retrying. A redirect to a non-HTTPS
  address is now `blocked` with a message that the link cannot be fetched automatically.
- **Bot-check pages (van Ballegooijen 2014 from IOP)**: the page began with `<head>`
  rather than `<html>`, was not recognised and ended as `failed`
  ("Download is not a PDF"). Any download that starts with markup is now `blocked`.
- **`Living Rev. Solar Phys.`** (Chen 2011) was missing from the journal table, so the
  page-less Crossref record could not be matched; now identified and downloaded.
- **arXiv title match** now tolerates punctuation, hyphens, LaTeX markup, Greek letters,
  a trailing period and a subtitle that only one side prints (token similarity >= 0.9, or
  the part before the colon when it has four or more words). It still requires the first
  author and the year (+/-1), refuses ambiguous candidates, and refuses titles that differ
  by a number or a Roman numeral ("Part I" against "Part II"). The arXiv query now asks for
  the longest plain title words instead of one exact phrase.
- **All-caps Crossref titles**: IOP deposits ApJ/ApJL titles in capitals up to about
  2016, and OpenAlex and Semantic Scholar copy them, so the library showed the title
  in capitals. They only reach the verified metadata title; file names use author, year
  and journal reference, and title matching ignores case. No safe case rule exists
  (acronyms, instrument names and proper nouns cannot be told from words), so the
  deposited title is replaced only by the matched arXiv record's spelling when it has
  exactly the same letters. In the run this restored mixed case for ApJ/ApJL papers found
  on arXiv. Two titles stayed in capitals: Hood 2016, whose preprint has a different
  title, and Antolin 2014, which came from the publisher copy through OpenAlex.

## First-page check before import

Added after the run: before a fetched PDF is imported, the text of its first two pages is
compared with the expected first-author surname (the reference's author, else the
identified one; case, accents and spaces are ignored so that small caps such as "H OOD"
match).

- Surname found: import and verify as before.
- Text present but the surname absent: not imported. The attempt gets the status
  `mismatch` ("The PDF from X does not name Y on its first page."), and the next
  provider and the next link of the same provider are tried. If every file mismatches,
  the result is `not_found` (or `failed`/`blocked` when other attempts decide that) and
  the summary says that a PDF from the named sources did not name the first author.
- No extractable text (a scan) or an unreadable file: imported, but not verified; the
  attempt carries `checked: false` and the summary asks the user to review it.

Re-checked live on a fresh copy: 7 downloads (Science, ApJL, LRSP twice, ApJ twice, A&A
via arXiv) all passed the new check, and the IOP bot-check page stayed `blocked`. A
mismatching file cannot be produced on demand from the real services, so that path is
covered by mocked tests only.

## Remaining gaps

- Closed papers are correctly `not_found` (Nature 2012/2013, GeCoA 1989), and A&A and
  IOP publisher links answer with HTTP 403 or a bot check, so those stay `blocked` and
  need Link local PDF. Failed searches are not evidence of a paywall.
- Only the first author is compared, so a different paper by the same first author would
  pass; identification, not this check, has to separate those.
- ADS, CORE and OpenAlex keys were not exercised live, so ADS identification and the
  ADS e-print route remain covered by mocked tests only.
- All-caps titles remain for papers that are not on arXiv or whose preprint title differs.
- A Science page ID is accepted only through the narrow rule above; a first author with
  two electronic-ID articles in the same volume stays ambiguous.
- References merged with neighbouring text, thesis entries and books are not identified
  (reported as such), and "Too many arXiv results" can still appear for unidentified
  references.
