# Offline OCR for metadata recovery

OCR is an optional local fallback for the metadata refresh job. It reads the
immutable original PDF and supplies provisional text and metadata candidates.
It does not add a text layer to the PDF, change annotations, or create a saved
PDF revision. Online enrichment remains a separate choice; with online lookup
off, OCR must make no HTTP requests.

Useful native PDF metadata takes precedence over OCR. Manual corrections and
verified bibliographic values remain protected. OCR recovery alone never marks a
paper as verified: uncertain titles, authors, dates and identifiers remain
reviewable, with their source retained in the extraction record.

## Included runtime

Standalone builds include a private minimal Tesseract 5 engine, Leptonica and the
English LSTM model. The engine reads PDFium's grayscale PGM pages and produces TSV.
The build disables legacy OCR, training tools, graphics, archive/network support
and optional Leptonica image codecs. It never downloads models at runtime.
See [the bundled OCR build and verification record](bundled-ocr.md).

The source app prefers the same runtime in `build/ocr/runtime`, produced with
`python packaging/ocr/build_ocr.py`. Only source installations without that build
may fall back to a system Tesseract 4.1+ and installed language data. Frozen apps
use their own engine and English model exclusively. Missing bundled files produce
an actionable repair message rather than silently changing the engine or model.

## Process and package constraints

PDFium calls cannot overlap between independent threads in one process. OCR
rendering therefore needs its own spawned process, separate from the Qt reader
and its existing render worker. The frozen entry point already calls
`multiprocessing.freeze_support()` before starting the application. A new OCR
worker must remain importable in a source checkout and in the frozen bundle.

Keep image dimensions, page count, output size and execution time bounded. Run
Tesseract without a shell. Cancelling an OCR worker must also stop its Tesseract
child, remove temporary images and release process handles; terminating the
Python worker alone can leave the external child running.

The bundled child retains the frozen app's library search environment so it can
load packaged compiler runtimes. Its English data directory is passed explicitly;
a user's `TESSDATA_PREFIX` cannot redirect it. Source-only system engines restore
the original library search environment. Windows subprocesses have no console
window; only the isolated worker changes the DLL search directory. Cancellation
uses the operating system's absolute `taskkill.exe` path, independent of `PATH`.

An OCR result cache must belong to the exact immutable PDF and the OCR
configuration. Changing engine/parser behavior, language or render settings
must invalidate incompatible results. A failed availability check must not
permanently suppress OCR after Tesseract or its language data is installed.

## Acceptance checks

Use generated, disposable image-only PDFs; do not commit research PDFs or OCR
output from a personal library. Include these behaviors in regression coverage:

- Recover a readable title and authors from a scan while retaining provisional
  status and OCR provenance. An arbitrary year in body text is not a publication
  date, and an OCR DOI remains a candidate until corroborated.
- Preserve useful native fields, manual corrections and verified values when
  OCR disagrees. Check original file hashes, saved revisions, notes and reading
  positions before and after recovery.
- Make no OCR calls when the option is off or the native text is sufficient;
  make no HTTP calls when online lookup is off.
- Cover missing executable, missing English model, timeout, malformed output,
  low-confidence text, rotated pages and paths containing spaces or Unicode.
- Cancel before start and during rendering/recognition, then confirm bounded
  shutdown, no orphan Tesseract process and no temporary image files.
- Reuse compatible successful results and retry recoverable failures after
  dependencies become available.
- Open papers and close/reopen the Library window during OCR. The background
  job must continue, and completion must update metadata without reloading an
  open PDF or losing unsaved edits.

Release acceptance also requires a copied frozen bundle outside the checkout on
each supported host. Verify successful OCR without system Tesseract or model
data, and graceful reporting of a damaged bundle; source-checkout success does
not establish Windows or Finder-launch compatibility. Record the implemented bounds, actual test results
and remaining host-only checks below before declaring OCR release-ready.

## Implemented behavior and bounds

- `paperassist.ocr.recover(path, cache_dir, sha256, cancel, ...)` runs in a spawned
  worker (its own session/process group). The worker renders at most two pages
  with PDFium and runs `tesseract` (TSV, no shell; Tesseract 4.1 or newer is supported).
  The second page is read only when the first yields no recognisable title (cover
  or stamp sheet).
- Bounds: 12 million pixels per page, 2 pages, two attempts per page (200 dpi
  `--psm 3`, then 300 dpi `--psm 11`, the second only if the first is poor),
  20 s per Tesseract run, 60 s per paper, 4000 words and 8 MiB of TSV per page,
  one OCR task at a time (`busy` otherwise), `OMP_THREAD_LIMIT=1`.
- Cancelling or timing out kills the whole process group (`taskkill /T` on
  Windows); the staging directory with the page images is always removed.
- Results are cached in `<library>/cache/ocr/<key>.json`; the key covers the PDF
  SHA-256, Tesseract version, language-model hashes and render configuration.
  For Tesseract 4.1.x, which does not output the model directory in `tesseract --list-langs`,
  models are resolved via `TESSDATA_PREFIX`, the engine directory or well-known system
  paths (`/usr/share/tesseract-ocr/4.00/tessdata`, etc.) so results remain cacheable.
  Only `completed` and `empty` results are cached, so failures retry.
- `metadata.extract_ocr_metadata` derives a title from stacked confident lines
  that are clearly taller than body text or all capitals (above the abstract),
  authors with the native header-author rules, and only candidates for DOI,
  arXiv id and year (an explicit arXiv submission stamp may set the year). All
  values carry `ocr_*` sources and stay `needs_review`.
- `Workspace.refresh_metadata(..., ocr="auto"|"off"|"force")`. `auto` runs OCR
  only for papers that are neither corrected nor verified, whose title is not
  useful (or comes from the filename/arXiv id) or that have no authors, and
  whose first-page native text is missing or shorter than 150 characters before
  the abstract. `force` skips the native-text test. OCR runs before the online
  lookup; with `online=False` nothing touches the network.

## Verification record

The records below predate the bundled runtime. Current packaged verification is
in [bundled-ocr.md](bundled-ocr.md).

Automated (Linux, Python 3.14, Tesseract 5.5.3 with `eng` and `ita`):
`tests/test_ocr.py` (process-group kill, cancel before/during recognition,
timeouts, cache reuse and invalidation, missing engine/language, TSV
validation, rotated page, spaces and Unicode in paths, unchanged source hash,
and mock probe coverage verifying 4.1.1, 5.x, and 6.0 are accepted and
cacheable while 4.0 and 3.05 are rejected),
`tests/test_metadata.py` (title/author/year/DOI rules), `tests/test_library.py`
(provenance, protection of corrected/verified values) and
`tests/test_metadata_refresh.py` (modes, ordering, no HTTP, failures, cancel).
Tests that need Tesseract skip when it is missing. Full suite: 824 tests, OK
(8 skipped, none of them OCR tests on this host); `node --test`: 61 passed.
The OCR tests take about 7 s.

Tesseract 4.1.1 (2026-11-09): the stock `tesseract-ocr` package of an Ubuntu 22.04
container, invoked through a `PATH` wrapper by `ocr.recover` from a Linux source
checkout, was reported as ready (version 4.1.1 with `eng` listed) and recovered the title and both authors of a 1982 ADS page scan
from a path containing a space in 2.6 s.

Real scans (ADS page scans from a private library, copied read-only to a
disposable library; nothing committed), `refresh_metadata(online=False, ocr="auto")`:

| Paper | OCR ran | Result | Time | Rerun |
| --- | --- | --- | --- | --- |
| Edwin 1982 | yes | title (without subtitle) and both authors | 1.5 s | cache hit, 0.1 s |
| Dennis 1985 | yes | title and author | 1.6 s | cache hit |
| Hood 1979 | yes | full title, both authors | 1.5 s | cache hit |
| Reale 1994 | yes | title, three authors | 2.4 s | cache hit |
| Parker 1988 (has an embedded text layer) | no in `auto`; `force`: yes | title and author | 2.8 s | cache hit |
| Antolin 2018 (born digital) | no in `auto`; `force`: ran, nothing recognised | unchanged | 2.7 s | cache hit |

Peak resident memory of the OCR worker plus Tesseract: 98 to 139 MB
(worker 43 to 99 MB, Tesseract 52 to 62 MB); the Qt process itself is untouched.
Known limits: all-capital titles are kept as scanned, subtitles in ordinary
case at body size are dropped, and a mixed-case title at body size is not
recognised at all (born-digital papers with such titles keep their native
result). OCR character errors (for example `FaBio`) remain for the user to fix.

At the time of this historical record, frozen-host and Library-window checks
were pending. The subsequent [bundled OCR record](bundled-ocr.md) covers the
completed Linux frozen/container checks and real Library-window regression,
and identifies the remaining Windows/macOS and baseline checks.
