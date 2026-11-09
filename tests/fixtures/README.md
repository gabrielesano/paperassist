# Synthetic PDF.js interoperability samples

`pdfjs-annotations.pdf` was saved by PaperAssist's former bundled PDF.js 6.4.299
through the real web UI in headless Brave, not by a substitute serializer.
It has three generated pages, five text highlights (one per toolbar color),
a black width-4 Ink stroke and a yellow width-12 freehand InkHighlight.
The last two pages have no annotations. It contains no private paper content.

`pdfjs-notes.json` is the actual notes API export for that exact synthetic
PDF: a two-line phrase comment on page 1, with version-1 PDF-space anchors.
Comments live in SQLite; they are not embedded in the PDF.

These are historical interoperability fixtures. Keep the saved bytes: they
verify that the native reader preserves annotations and notes created by the
retired web reader. Regeneration requires a historical web-reader checkout and a
disposable library; the current application has no PDF.js runtime. Never use real
papers or personal library data for fixtures.

See [the migration format record](../../docs/development/native-migration.md)
for exact dictionaries, identity rules and acceptance checks.
`test_native_fixtures.py` reads these bytes using pypdf and checks the matching
notes round trip through Library.
