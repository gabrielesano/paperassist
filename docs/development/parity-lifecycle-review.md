# Native keyboard and desktop lifecycle review

Reviewed 2026-11-07 on Linux/XCB with disposable PDFs, configuration and libraries. The native window is a Qt Widgets host; browser and pywebview mechanics do not carry over. The `paperassist desktop` command still launches the transitional pywebview host; the specification (lines 215–220) and roadmap (lines 16–21) explicitly retain it until native parity. Native is launched with `python -m paperassist.native`. No production code was changed in this review.

## Keyboard rows

- **Mod+F, Enter/Shift+Enter, Escape:** Verified native equivalent. `native/window.py:75` targets the active pane; `native/pane.py:21–46` handles stepping and retains the field text on close. In a real XCB window, Ctrl+F focused the quick pane's field and Escape returned focus to its view with `nanoflares` retained. `tests/test_native_ui.py:203–228` tests Enter and Shift+Enter, and lines 301–310 test Find and Escape.
- **Tab, activation, dialog Escape and focus:** Partly verified. Real XCB window: Tab moved from a PDF view to the splitter handle, Shift+Tab returned; the shortcut help dialog closed on Escape. The bare Xvfb display has no window manager, so on closing that dialog neither the parent nor any widget was active; this cannot establish normal desktop focus restoration. Qt's standard widgets implement their activation behavior; representative dialog activation and restored focus still require a desktop with a window manager. Existing keyboard suite exercises modal and controls. Qt QAccessible reports no accessible Name for the visible enabled QTabBar close button (its tooltip says Close Tab); all other visible buttons and line edits in the tested two-pane window had names. This is a concrete accessibility issue.
- **Browser-reserved commands:** Not applicable to the native host. Native intentionally binds Ctrl+W and Alt+Left (`native/window.py:79–80`); dynamically generated shortcut help includes toolbar/menu commands (`native/shortcuts.py:63–81`). The browser-specific prohibition should not be imposed on a standalone Qt window.

## Desktop-window rows

- **Window/profile:** Native `MainWindow` has initial 1400×900 and minimum 800×600 (`native/window.py:49–50`), derives its title and dirty marker from active paper state (`native/window.py:328–360`), and renders selectable text. Persistent webview profile is not applicable; native preferences are stored via `settings.preferences()` (`native/window.py:47`). Actual platform window sizing/state remains governed by the window manager.
- **Close and duplicate requests:** Native uses a synchronous Qt modal equivalent, not an asynchronous web close promise (`native/window.py:376–424`). Real XCB test injected another `window.close()` by QTimer while the unsaved-changes QMessageBox was open: only one prompt appeared, second close returned, and Cancel kept the window visible. Save failure and close-during-save are covered in `tests/test_native_ui.py:372–386, 686–699`; all-pane Cancel is covered at lines 433–444. The generic case of external close events on other window managers is outside this Linux check.
- **Server ownership/attach:** Not applicable: the native host starts no HTTP server. The native single-owner guarantee remains: `native/app.py:56–66` holds `library_lock` across `Workspace` lifetime and closes the workspace before releasing it; contention is refused at lines 84–87 rather than attaching to an HTTP host. `tests/test_native_preferences.py:599–664` covers lock contention and switching.
- **Switch, jobs and termination:** Normal close holds the lock through workspace cancellation and executor shutdown (`workspace.py:45–48`); native switch reopens in the same process (`native/app.py:78–96`), and `tests/test_native_preferences.py:577–664` covers switching and lock transfer. Two outstanding guarantees: (1) native has no old-library fallback if the target cannot reopen after a switch: it warns and returns on `LibraryInUse`/`LibraryMissing`; (2) native has no SIGTERM handler to run cleanup. A disposable native Qt process with an admitted two-second Workspace job, RenderClient and process lock was sent SIGTERM: exit -15, cleanup `finally` did not run, job did not finish, renderer exited and OS released the lock. Thus graceful draining is absent, although this run did not leave a renderer alive or touch unrelated processes. Other startup errors can propagate; do not infer recovery from the normal switch test.
- **Folder chooser:** HTTP serialization/bridge is not applicable. Native uses synchronous `QFileDialog.getExistingDirectory` (`native/preferences_dialog.py:138–144`), cancel leaves the field unchanged and selection only populates the field; persistence requires separate Save or Use. The real Qt chooser was exercised: Cancel retained the field; accepting a directory populated it without changing the stored setting. The harness accepted the current directory; its attempt to select a disposable child programmatically did not take effect. No native chooser failure was observed; HTTP chooser-unavailable error handling is not a native contract. Import chooser is a separate explicit import action (`native/library_dialog.py:186–193`).
- **Optional dependency:** The web desktop host has a useful pywebview dependency message (`desktop.py:104`), but the new native entry point imports PySide6 before argument parsing (`native/__main__.py:7`, `native/app.py:7`). `PYTHONPATH=src .venv/bin/python -S -m paperassist.native --help` produced a `ModuleNotFoundError: No module named 'PySide6'` traceback. A useful native dependency message remains work; the separate browser CLI does not import native at startup (`cli.py:31–32, 66–67`).
- **pywebview RPC and web security machinery:** Not applicable to Qt; no RPC, webview profile, beforeunload, or HTTP origin bridge exists in the native reader. PDF selection is provided directly by the native reader.

## Commands and scope

A private Xvfb/XCB run of `/tmp/parity_lifecycle_check.py` used real `MainWindow`, `RenderClient`, synthetic PDFs and QTest events for focus, Find, dialog Escape and duplicate close. `/tmp/parity_picker_check.py` exercised chooser cancellation and selection. `/tmp/parity_sigterm_check.py` exercised a disposable subprocess, library lock, worker and job. The shortcut suite `tests/test_native_shortcuts.py` passed 18 tests under Xvfb using `/tmp/parity_shortcuts_runner.py`, which explicitly activates each window before QTest events. The unmodified suite fails under bare Xvfb because there is no window manager to activate windows; do not treat that result as an application regression. Scripts in `/tmp` are ephemeral QA artifacts. No macOS or Windows behavior was tested.

## Remaining work

The native acceptance gaps identified in this review are resolved in the
follow-ups below. Platform release checks remain separate.

## Follow-up: accessibility and dialog focus

Resolved on 2026-11-07. Tab close buttons now expose "Close <paper title>" through
Qt accessibility, including after tab renaming and reordering. The local-PDF
picker has named search/results controls, and the downloaded-PDF chooser has a
named results list. Checks query computed QAccessible names, not only the widget
properties.

A private Mutter 49.8 Wayland session supplied a real window manager. Escape
returned focus to the document after Preferences, About, Help, Sources, Library,
acquisition, local-link and downloaded-file dialogs; nested metadata editing
returned to the Library table. A separate button check exposed Preferences keeping
focus on its hidden Close button after Enter/Space. Preferences now records its
invoking control and explicitly restores it on completion. If that control was
deleted (for example the temporary tab-rename editor), it focuses the active PDF
or the app menu when no PDF is open.

All seven checks in `test_native_accessibility.py` passed under Mutter/Wayland,
including computed names, Tab/Shift+Tab, Escape, focused-button Enter/Space,
rename/reorder and the deleted-editor fallback. Three window-activation tests
are opt-in and skip under offscreen Qt. `tests/README.md` documents reproduction.
This supersedes the earlier bare-Xvfb focus-verification limit. Actual
screen-reader integration and Windows/macOS behavior are outside these checks.

## Follow-up: library-switch recovery and SIGTERM

Resolved on 2026-11-07. Failed target startup now reports the reason, restores the
previous saved library choice, and reopens it. Tests cover a missing, locked and
corrupt target, plus both libraries being unavailable. Recovery is attempted once;
missing saved libraries are never silently replaced by empty ones. The normal
Workspace close and process-lock lifetime remain in force.

The user explicitly chose Save/Discard/Cancel for SIGTERM. The signal handler
records a request; a Qt timer routes it through the normal window-close guard.
Repeated signals while its prompt runs are coalesced. Cancel retains PDF edits and
comment drafts. Discard publishes no PDF revision or draft note. Save writes only
after the explicit choice. The handler stays installed through job cancellation/
draining and renderer cleanup, then restores the preceding handler even on error.

Seven lifecycle tests passed within the full Python suite (607 tests, four skips).
Six checks also passed on XCB/Xvfb before the final double-unavailable regression
was added. Real-app subprocesses drove actual modal choices and verified immutable
originals, saved notes/revisions, duplicate requests, lock retention while a job
finishes and renderer exit. The real-window recovery probe disconnected a target
after the Preferences confirmation and observed the old library reopen.
POSIX signal checks ran on Linux; Windows/macOS behavior was not exercised.

## Follow-up: native dependency diagnostics

Resolved on 2026-11-07. The native entry point probes Qt Widgets and PDFium after
multiprocessing's frozen-worker bootstrap and before importing application code.
Missing modules and native library import/load errors produce a concise stderr
message and exit status 1. Source runs show `python -m pip install -e ".[native]"`
from the project checkout and the browser command; frozen builds recommend
reinstalling a complete application. No browser fallback starts automatically.
Internal application import errors are not masked as installation failures.

Seven bootstrap tests cover missing Qt/PDFium, ImportError/OSError, the frozen
message, frozen-worker ordering and internal errors. Fresh processes deliberately
block native imports without uninstalling packages: native startup reports the
problem, while browser CLI help and the real HTTP backend remain usable.
The original `python -S -m paperassist.native --help` probe now exits with a clean
missing-Qt diagnostic. With dependencies available, the native entry point's
renderer self-test passes on the synthetic three-page annotation fixture.
The complete Python suite passes: 614 tests, four skips. This closes the last
native acceptance row; packaging and cross-platform release verification remain
separate work.
