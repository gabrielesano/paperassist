# Tests

Use focused unittest tests for library integrity, deterministic metadata, citation
analysis and API behavior. Generated fixtures use temporary directories. The private
PDF corpus stays outside Git. Browser checks exercise real PDF.js rendering and
annotation save/reopen paths rather than replacing them with mocks.

The opt-in desktop smoke test needs the `desktop` extra and a graphical session.
On Linux it can run on an isolated X11 virtual display:

```bash
xvfb-run -a env QT_QPA_PLATFORM=xcb .venv/bin/python tests/desktop_webview_smoke.py
```

It uses generated PDFs and private temporary libraries/profiles. It checks real
rendering, Cancel/Save/Discard, highlight save/reopen, view persistence, blank and
missing-app close, existing-server attachment and SIGTERM cleanup. Annotations
use PDF.js storage; gestures, dialogs and other platforms need interactive QA.
