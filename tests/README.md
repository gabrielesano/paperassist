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

Native accessible names and focus traversal are checked by
`test_native_accessibility.py`. Three focus-restoration checks require a real
window manager; offscreen Qt skips them because it cannot establish window
activation. On Linux with Mutter installed, run them in an isolated session:

```bash
task_runtime=$(mktemp -d /tmp/paperassist-focus-XXXXXX)
env XDG_RUNTIME_DIR="$task_runtime" QT_QPA_PLATFORM=wayland PAPERASSIST_WINDOW_MANAGER_TEST=1 \
  dbus-run-session -- mutter --wayland --headless --virtual-monitor 1600x1000 --no-x11 -- \
  .venv/bin/python -m unittest discover -s tests -p test_native_accessibility.py -v
```

The session exits with the test process and uses disposable PDFs and libraries.
The checks query Qt's computed accessibility names; they do not establish
screen-reader interoperability or Windows/macOS behavior.

`test_native_lifecycle.py` uses disposable real-app subprocesses for switch
recovery and POSIX SIGTERM. It drives actual Save/Discard/Cancel dialogs, checks
that Cancel retains edits and drafts, and checks job draining, renderer exit,
library locks and original PDF bytes. The subprocess driver is
`native_lifecycle_driver.py`; it is not a standalone production entry point.
