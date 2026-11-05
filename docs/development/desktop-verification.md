# Desktop preview verification

Linux x86_64, Python 3.14.7, pywebview 6.2.1, Qt 6.12.0 and QtWebEngine Chromium
140. Tests use generated PDFs, temporary libraries and private temporary profiles.
Windows/macOS remain unverified.
Results: 275 Python tests, 55 Node tests and the native smoke test passed.
Chromium < 148 uses native selection to avoid full-page selection tint.
Pointer highlighting at display scale 1.5 and 250% zoom was checked with real X input.
Qt may print "Release of profile requested but WebEnginePage still not deleted" on
exit: pywebview leaves its link-routing page alive. Profile data still persists.

```bash
.venv/bin/python -m unittest discover -s tests -q
node --test tests/*.test.mjs
xvfb-run -a env QT_QPA_PLATFORM=xcb .venv/bin/python tests/desktop_webview_smoke.py
```

The native smoke test exercises the real application and PDF.js worker:

- Canvas/text rendering, native Cancel preserving unsaved edits, explicit Save.
- A fresh process reopens the saved highlight and restores the reading position.
- Discard creates no PDF revision; original bytes remain unchanged.
- A blank page and a missing app bridge can close without trapping the process.
- An attached window leaves its existing server running.
- SIGTERM releases an owned listener, library lock and server marker.

The suites also check failed saves, drafts, missing desktop dependencies,
protected folder selection, library switching and request/job draining.
SIGTERM follows the active Ctrl+C handler: raising directly inside Qt's event
callback reproduced a SIGABRT before server cleanup.
Annotation creation in the smoke test uses PDF.js storage; drawing gestures,
native dialogs, browser launching, keyboard/clipboard and split workflows still
need interactive checks.

The modern PDF.js build failed on `Map.getOrInsertComputed` in Chromium 140.
The [official legacy build](../../src/paperassist/web/vendor/pdfjs/README.md) at
the same pinned version fixes this. Upstream artifacts remain byte-identical,
including Webpack whitespace. Run diff whitespace checks excluding vendor files.

See [desktop-plan.md](desktop-plan.md) for remaining work. File associations,
standalone packaging and clean-machine platform tests are subsequent increments.
