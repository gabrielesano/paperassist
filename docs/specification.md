# PaperAssist specification

PaperAssist is a lightweight, local-first reader and shared library for scientific
papers. All project content, including the user interface, is English.

## Development principles

Prefer short, direct, human-readable solutions. Reuse capable libraries rather than
rebuilding PDF rendering or editors. Add a dependency only when it removes real
complexity. Comments explain why a decision exists. Avoid speculative abstractions,
large frameworks, and code that does not serve a working user path.

## Workspace

A top toolbar sits above three columns: document outline on the left, the reader
in the center, and a right sidebar that switches between References and Comments.
The document gets most of the space; controls are compact, clear, and accessible.

The center supports in-app tabs. Quick reference consultation splits the center
in one of two layouts, chosen with a toggle in the quick-view bar and remembered
per viewer: stacked (main paper above, secondary paper below) or side by side
(main paper left, secondary paper right). Narrow reader areas always stack.
Each pane has independent scroll and zoom. The secondary pane can close or move
into its own tab without losing page, zoom, or annotations. The main reading
position is preserved. Keep one quick preview per tab. Resize it by dragging
the separator between the panes; support keyboard arrows and Home/End without
covering PDF text or activating annotation tools.

A clean quick view of an already-open PDF may edit until another view has unsaved
edits. Promoting it to an existing owner transfers the preview's page and always
uses Fit width in the destination. The owner's unsaved edits are preserved.

Each side column collapses with a "−" button in its header. One toolbar button
hides both columns when both are visible and otherwise shows both, which also
brings back a single collapsed column. The states persist per viewer, and the
toolbar button stays reachable when the columns are collapsed. Toolbar rows
reflow above the workspace rather than overlapping PDF text.
Each collapsed side column also has its own plus button at the corresponding
edge of the reader, restoring only that column and saving its visibility.

The toolbar provides zoom, drawing, text highlighting, and comments. Annotation
and Save controls target the clearly indicated active document. Main page, find
and zoom controls always target the main paper; the quick-view header has its own
page, find and zoom controls. Navigation shortcuts operate on the active pane.
Changing a zoom label must not shift neighboring controls, and page controls
must remain compact rather than stretching to fill the toolbar.
The outline belongs to the main paper
of the current tab; when no outline exists, page navigation remains available.
References belong to that main paper; comments identify the active document.

Renaming a tab sets a session-only name for that tab. It must not change the
library title or label, the filename, PDF bytes, or other views of the same paper.
Clearing the name restores the paper's current display title.

Opening a paper from a sidebar reference shows it in the lower preview. Clicking
a citation link in the main PDF opens the cited paper in that preview when a
valid copy is available in the library. Otherwise it reveals and selects the
corresponding entry in the right References column, opening that column and
clearing a filter that hides the entry when needed. It preserves the main reading
position and does not jump to the bibliography. A missing paper leaves an existing
quick view untouched. Ambiguous or grouped citations offer a choice in the sidebar;
the chosen reference follows the same local-or-missing rule. Papers not yet
available offer retrieval and prefilled search. Ordinary section, figure and
table links keep their normal PDF navigation. Within a window session, the second
and all subsequent clicks on the same reference reveal its card in the right
column, including when the cited paper is already open. They must not reopen or
navigate the cited PDF. Click history is independent for each source paper and
reference.

Existing ink, highlights and comments can be selected, recolored and deleted using
contextual controls in the top toolbar. Tool menus must not float over the PDF.
Selected ink drawings can be dragged by their bounds and resized proportionally
with corner handles. Each gesture is one undo step; Escape cancels it. Text
highlights keep their text geometry.
Selecting text before Comment creates a phrase-linked note with its quote and
page-linked PDF-coordinate rectangles. Navigation focuses that phrase after zoom
and reopen. Notes created without a selection remain page comments. The composer
stays in the right Comments panel, keeping the document visible. Every saved
phrase comment leaves a persistent red dashed outline without fill, including
when the sidebar is closed. Zoom, reopening and deletion update these indicators.

## Opening and acquiring references

Each new main-paper or independent-tab opening offers to acquire all references,
a checkbox selection, or continue reading without acquisition. The selection can
be reopened later. Switching existing tabs is not a new opening. Previewing a
reference does not recursively prompt for or download its bibliography.

The dialog provides Select all, Clear all, individual selection and a live count.
Local PDFs are marked as available and excluded from download requests. Missing
papers expose their actions inline in the reference sidebar; only explicit Change
or Link actions open the library picker. Provider availability, current progress,
per-provider outcomes, cancellation and batch result counts must be visible.
Search starts on an explicit acquisition action. A successful single-reference
search opens the acquired PDF below the main paper.

Manual links can be changed or reset without deleting PDFs. Reject a new link to
the source paper; preserve and visibly flag an existing invalid association for
review. Invalid self-links must never propagate to other source papers.

Show local availability, selected items, analysis state, and citation frequency.
Preserve previous selections and reuse local files. Analysis and downloads must
not block the reader, and network failures must not block local consultation.

The ranking uses occurrences in the current source document, not global citation
counts. Exclude bibliography entries themselves. Numeric groups/ranges attribute
one occurrence to each referenced work; reconcile link and text detections.
Preserve page, context, and coordinates when available. If multiple entries refer
to one verified work, aggregate distinct citation events and retain their labels.
Unresolved entries remain separate. Incomplete analysis is labeled as detected
occurrences, never presented as a complete count or an exhaustive bibliography.

## Deterministic bulk import

Support selecting multiple PDF files and recursively scanning local folders.
Importing and organizing metadata must not use an LLM. Existing local collections
are a first-class source of the library, including scanned PDFs and papers with
missing identifiers or metadata.

The first storage mode makes a managed copy and leaves source files untouched.
Show the selected size as an upper estimate of additional storage; deduplication
can reduce it. Indexing external files in place is deferred. Do not follow linked
directories by default or create traversal cycles.

Process files incrementally with bounded concurrency. Hash while streaming into
a unique temporary file; reuse known SHA-256 content and discard only the duplicate
temporary copy. Validate before publishing into a hash-based managed path. Commit
each file independently, report progress and per-file failures, and allow cancel.
Bulk importing does not open documents or trigger bibliography downloads. For a
single PDF, offer Import only and Import & open; the latter opens the imported
file (or its existing duplicate) in a reader tab only after a successful import.

Reimporting the same selection is idempotent. After interruption, completed files
remain available; reselecting the folders skips completed hashes and retries
unfinished work. This is not automatic restoration of an entire batch manifest.
Do not reparse successful metadata for unchanged content/parser versions or
overwrite manual corrections and preferred-file choices.

Read PDF Info/XMP and explicit identifiers, then bounded first-page text when
needed. Recover provisional multiline titles and author lines from bounded
first-page typography when embedded properties or filenames are generic. Keep
the source of each recovered field visible. Explicit arXiv stamp dates can supply
a preprint year only when they agree with the paper's own identifier; later
revision dates and unrelated years remain candidates. This local recovery does
not require network access. Preserve value, source, and candidate/confirmed status. Filenames are
provisional titles; PDF creation time is not publication year. A DOI anywhere on
the first page may belong to a citation: do not adopt the first match blindly.
Exclude bibliography text where recognizable and corroborate identity.

Online metadata enrichment is optional. Compare candidates against available
evidence rather than accepting the first search result or merging on title
similarity. Scans and unidentified papers remain visible with editable metadata;
OCR is not required for import. A metadata failure differs from a file failure.
The library needs basic title/author/year/identifier/status filtering.
Column headers sort title, authors, year and metadata status in either direction;
unknown years stay last. Preserve the chosen order and selection on list refresh.
Allow selecting multiple papers and explicitly refreshing their metadata or the
entire library, including papers hidden by a search. Run refreshes in cancellable
background jobs with progress and per-paper failures. Jobs keep running when a
paper is opened or the Library window is hidden or closed. Reread immutable
originals, preserve the fields the user corrected (each field separately, so a refresh may still
fill the others), notes, reading positions and saved PDF revisions,
and optionally fetch fresh arXiv or Crossref metadata using the configured source
switches. Use explicit identifiers first, then bounded title/author searches with
independent corroboration. Provider failures must not prevent a successful
fallback or discard locally recovered data. Ambiguous identities remain candidates
for review. Distinguish local improvements from online verification in the summary,
and retain a per-paper reason when metadata remains incomplete or lookup fails.

## Shared library and data model

Separate a bibliographic work, a particular PDF content, and a citation in a source
paper. Multiple papers can reuse a single local PDF; citation counts stay local.

| Entity | Responsibility |
| --- | --- |
| `works` | Stable local work identity, optional title/authors/year/journal and preferred PDF. |
| `work_identifiers` | Verified normalized identifiers, unique by scheme/value; initially DOI. |
| `pdf_files` | Immutable original content, unique SHA-256/path, optional work association, original filename/origin, extracted metadata/provenance/parser status and independent citation-analysis status. |
| `bibliography_entries` | Source PDF and local entry index/label/raw reference, candidate metadata, optional target work; unique within the source. |
| `citation_occurrences` | Entry, source-local citation-event key, page and optional context/rectangles. |
| `retrieval_state` | Last provider observation and its expiry, candidate URL, separate last download outcome. |

Implement only the subset needed by a working increment. Enable foreign keys on
every SQLite connection. Library queries include files without a work association.
Notes and reading positions belong to a precise original file ID, not a mutable
bibliographic preference or a transient parser occurrence ID.

For example, A cites work D five times and B once. Both bibliography entries link
to D and reuse its existing PDF, while the two source rankings retain 5 and 1.
A result found by a metadata query is a candidate until sufficiently corroborated.
Missing DOI or similar titles do not justify automatic merging. Confirmed merges
transfer identities, files and references transactionally without moving note
anchors. Different DOIs, preprints and published versions need explicit handling.

The first verified file is a stable preference; later variants do not replace it.
Changing the preference affects new openings only. Existing views retain their
concrete file ID. Check local existence/readability before reuse; changed bytes
are new content, not a silent hash update on the previous record.

A citation-event key is scoped to its source PDF. Reconcile text/link detections
before assigning it; page or text alone is not unique. Count distinct events per
verified target work in that source. Reanalysis replaces occurrences atomically
and preserves explicit manual associations.

## Persistence and retrieval

Keep immutable originals separate from working annotated PDFs and per-view state.
Ink and highlights are standard PDF annotations that survive closing and reopening
and stay readable by other viewers, including those written by the web reader; custom
comments may use a sidecar database model. Test the actual round trip before relying
on editor internals. A single save authority/revision check must prevent two views
of one PDF from overwriting each other's edits. Failed saves must be visible.
PDF writes require explicit Save or Ctrl/Cmd+S. Timers, close, navigation, preview,
and tab promotion must never save implicitly. Display the current save state.
Closing or replacing a modified pane offers Save, Discard and Cancel; cancellation
keeps the reader and edits. Collect decisions before closing a multi-pane tab.
A failed save keeps the unsaved state and requires an explicit retry. Quitting
the application warns about pending work. Read-only previews may show the last saved version
of an open modified PDF, clearly labeled; its editable tab retains unsaved edits.

SIGTERM requests the same Save/Discard/Cancel flow as closing the window. Cancel
keeps the application and edits open; repeated requests during a close prompt do
not open duplicate prompts. Shutdown cancels or drains admitted jobs before
releasing the library lock. It never saves PDF changes without an explicit choice.
If a library selected in Preferences fails to open, report the error, restore the
previous library choice and reopen it. Do not create an empty replacement when
the previous library is unavailable too; report that failure instead.

Use a single local backend per library with an effective process lock and per-work
download serialization. Check the local library again after acquiring a lock.
Download to unique partial files, validate actual PDF content, calculate its hash,
publish it, then register it transactionally. Restart interrupted transfers from
zero initially. Reconcile filesystem/database disagreements conservatively without
automatic deletion of orphan files.

Check local files before external providers. Distinguish unresolved identity,
local availability, an accessible copy, no copy found, and temporary failure.
A timeout, rate limit, or HTML response is not evidence of a paywall. Negative
results expire and can be retried; a failed download does not erase a positive
availability observation.

Crossref and DOI-based Unpaywall are candidate metadata/OA sources. Provider email
or credentials must be genuinely configured, never invented, and their absence
must not block reading. For unavailable or non-OA papers, offer correctly encoded
Google searches using title, author/year, DOI, and a PDF-specific variant. Open
searches only on request. Support linking a manually obtained local PDF.

## Technical direction

Python and SQLite handle library data and retrieval. The interface is a native
desktop application: Qt Widgets through PySide6, with PDFium through pypdfium2 for
rendering, text, search and links, and pypdf for annotation writing. It calls the Python library modules
directly, without an HTTP layer. Pages render in a separate process so the interface
stays responsive, and only pages near the visible area keep their pixels. Unsaved
annotation revisions stay in memory and are rendered from their actual PDF bytes;
only explicit Save publishes those bytes atomically. PDFium binaries are supplied
by pypdfium2's third-party builds. See the measured decision in
[the native stack record](development/native-stack.md).

Resource use is a product requirement. Measure memory, render time and interface
stalls with real papers for reader changes; a lighter design is worth extra setup.
A browser engine measured 1.1-2.2 GiB with four papers open, the native prototype
0.3-0.5 GiB.

During the migration the web interface stays usable with its current guarantees:
a loopback HTTP app serves HTML/CSS, vanilla JavaScript modules and a pinned, locally
served PDF.js viewer, documents are served by controlled IDs, and write requests are
protected from unrelated web pages. It is removed, with the HTTP server and the
pywebview window, once the native interface passes the parity checklist in
docs/development/native-migration.md.

Evaluate a targeted parser such as pypdf for background bulk metadata extraction,
independent of the reader tab. Test bounds on problematic files; limiting page count
alone does not guarantee low memory. Keep bibliography analysis deterministic and
make unsupported formats visible. OCR, model-based summaries, advanced library
search and external-file indexing are subsequent extensions.

## Verification

Use a real local collection of papers (the reference collection had 68 distinct PDFs,
1,145 pages; see docs/development/corpus-verification.md). Never modify source files.

Verify real import/reimport, duplicate content with different names, metadata
uncertainty and corrections, isolated failures, interruption and retry, original
immutability, note and PDF annotation persistence, independent panes, split-to-tab
promotion, shared reference reuse, known numeric/author-year citation counts,
ambiguous or incomplete results, invalid downloads and Google fallback.

## Primary references

- [pypdfium2 documentation](https://pypdfium2.readthedocs.io/)
- [pypdf documentation](https://pypdf.readthedocs.io/)
- [Qt for Python (PySide6)](https://doc.qt.io/qtforpython-6/)
- [PDF.js component example](https://github.com/mozilla/pdf.js/blob/master/examples/components/simpleviewer.mjs)
- [PDF.js document API](https://mozilla.github.io/pdf.js/api/draft/module-pdfjsLib-PDFDocumentProxy.html)
- [pypdf metadata](https://pypdf.readthedocs.io/en/stable/user/metadata.html)
- [pypdf text extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [Unpaywall API](https://data.unpaywall.org/products/api)
