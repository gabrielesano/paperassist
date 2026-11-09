# Web reader retirement

After native parity acceptance, the transitional web reader, Flask HTTP server,
pywebview window and web-only folder picker are removed from the source tree.
The PDF.js vendor tree, npm manifests, JavaScript tests and Node CI job are also
removed. The retired web assets occupied 7,984,915 bytes in the previous tree.

The default dependencies are pypdf, PySide6-Essentials and pypdfium2 (plus their
own dependencies). The existing Python-version guard for PySide6 is retained.
The former `native` and `desktop` extras are no longer needed; install with
`python -m pip install -e .`. Build and CI workflows use the same dependencies.

`paperassist` and `python -m paperassist` now open the native reader, forwarding
PDF paths, `--data-dir` and `--self-test`. `python -m paperassist.native` remains
available. `paperassist desktop` and `paperassist-desktop` are native aliases.
`paperassist serve` exits with an explanation of the replacement command.
Command-line import remains available in source installations.

Existing libraries require no migration. The `.server.lock` filename is retained
so older installations and current CLI imports coordinate with the native app.
Historical PDF.js-generated PDF and notes fixtures remain as compatibility tests.
Settings and folder-check regressions now exercise Workspace directly; HTTP-only
tests are retired. Partial preference migration remains covered by Workspace tests.

## Verification

On Linux with Python 3.14.7, PySide6-Essentials 6.12.0, pypdfium2 5.14.0 and
pypdf 6.21.4:

- Focused CLI, settings, bootstrap, Workspace and inbox checks: 69 tests passed.
- Full suite: 805 tests, successful with 29 skips, in 82.710 seconds. This includes
  native lifecycle subprocesses, annotation/comment compatibility and real Wayland
  compositor checks. The Wayland checks required execution outside the sandbox.
- Reinstalled the editable package and checked both console entry points and
  the main module's version/help output.
- Built and installed a wheel into an isolated target directory. Inspected its
  metadata and contents: native requirements only, no extras and no web assets,
  server, WebView host or old folder-picker module. The wheel was 206,008 bytes.
- From that installed wheel, ran the main entry point's rendering self-test with
  imports of Flask, Werkzeug, pywebview, qtpy and PyQt explicitly blocked. Result:
  `self-test ok: 3 pages, page 1 is 612x792, 59 words, 4.36% dark pixels`.
- `git diff --check` passed.

The wheel check reused the installed native dependencies; it was not a clean-OS
installation trial. No standalone release was rebuilt or published in this change.
Windows and macOS execution remains subject to the existing CI and platform checks.
