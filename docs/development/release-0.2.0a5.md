# Release acceptance: 0.2.0a5

This maintenance alpha adds margin line-number detection and stripping during
reference PDF text extraction (`_extract_pages`, `_detect_line_numbers`, and
`_chain_line_numbers`), more resilient bibliography heading detection
(`_find_heading`), and bumps `EXTRACTION_VERSION` to `6` so cached analyses
refresh automatically.

Local verification passes all 65 tests in `tests.test_references` (including
new regression tests in `LineNumberAndHeadingTests` for continuous/pagewise
margin line numbers, y-axis tick non-interference, and heading variants/fallback)
and all 31 tests in `test_native_citation*.py`.

Release preparation targets Linux x86_64, Windows x86_64 and Apple Silicon macOS.
The hosted Build workflow verifies rendering, bundled OCR, installation and
platform package checks. Source CI runs Ubuntu and Windows with Python 3.11 and
3.13, and the macOS build runs the native Cocoa suite.

Build identifiers, package provenance, checksums and publication status will be
recorded after hosted verification.
