# Tests

Run the Python and native Qt suite from the repository root:

```bash
python -m pip install -e .
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
python -m paperassist --self-test tests/fixtures/pdfjs-annotations.pdf
```

Use focused unittest tests for library integrity, deterministic metadata, citation
analysis and native UI behavior. Generated fixtures use temporary directories. The
private PDF corpus stays outside Git. The retired web reader's synthetic PDF and
notes fixtures remain to verify annotation and comment compatibility. Node,
PDF.js, Flask and pywebview are no longer needed to run or test the application.

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
