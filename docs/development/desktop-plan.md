# Desktop window plan

PaperAssist uses pywebview to show the existing Flask/PDF.js reader in a dedicated
window. Keep browser mode, managed PDF copies and explicit Save behavior.

This window is transitional: the native Qt/MuPDF application replaces it, and it is
removed with the web interface once the native one reaches parity (see the
specification's technical direction).

## Current source preview

- Launch with `paperassist desktop` or `paperassist-desktop` after installing the
  desktop extra. Plain `paperassist` continues to open the browser.
- Start an owned loopback server, or attach to the server already serving the
  selected library. Closing an attached window leaves that server running.
- Ask Save, Discard or Cancel for modified PDFs and comment drafts. Cancel and
  failed saves keep the window open. Only explicit Save writes PDF revisions.
- Allow closing a blank page or a page whose app failed to initialize.
- Drain requests and background jobs before releasing an owned library lock.
  SIGTERM also cleans up an owned server.
- Use the official legacy PDF.js build at the pinned version for WebView
  compatibility, and retain desktop view preferences at the same origin.

## Acceptance for the preview

Run the Python and Node suites plus the real-WebView smoke test described in
[verification](desktop-verification.md). Use temporary libraries and generated
PDFs; leave personal collections and production services alone.

The smoke test must render text and canvas, preserve edits after Cancel, save and
reopen a highlight, restore reading position, discard without a new revision,
close an absent app, and terminate the process. It must also attach to an existing
server without stopping it, and release an owned server after SIGTERM.

Interactively check pointer drawing/highlighting, native file/folder dialogs,
external links, keyboard shortcuts, clipboard and split view. Windows and macOS
remain unverified until these checks pass on their WebView engines.

## Next increments

1. Store view preferences by library and exact PDF identity independently of the
   window's local URL. Keep unsaved annotation edits in memory.
2. Deliver files from a second invocation to an existing window and focus it.
   Import or reuse managed copies; never change the supplied original.
3. Register optional PDF associations on each platform after file delivery works.
   Let users select their default reader through the operating system.
4. Build standalone artifacts for each supported OS/architecture. Include runtime
   prerequisites, assets and licenses; keep private state outside the bundle.

For each platform, test a clean installation without Python, offline reading,
explicit annotation save/reopen, upgrade without library loss and uninstall
without deleting the library. Signing and publication follow a separate release
step. A source preview does not establish standalone or cross-platform support.
