# Native desktop integration plan

The Qt/PDFium reader replaces the transitional Flask/PDF.js/pywebview application.
Native parity is accepted in [the migration inventory](native-migration.md); the
web stack is retired. `paperassist` and `python -m paperassist` launch the native
reader. `paperassist desktop` and `paperassist-desktop` remain native aliases;
`paperassist serve` explains that the web reader has been retired. CLI import
continues to use the shared library backend and the existing process lock.

The old WebView acceptance record in [desktop-verification.md](desktop-verification.md)
is historical. Saved PDF revisions, sidecar notes, reading positions and preferences
remain supported, with PDF.js-generated fixtures retained for interoperability.

## External opening in 0.2.0a1

All normal file entry points use managed library copies: command-line PDF
arguments, Open, Quick view and Finder/Dock file-open events. Identical content
reuses its catalog identity, corrected metadata, notes and latest saved revision.
An already open document is selected without reloading its unsaved annotations
or comment draft. Explicit Save writes a library revision and preserves the
external source. Files inside another library, changed managed originals and
older managed revisions are rejected with an explanation; open their owning
library through Preferences instead.

Each library's process lock protects one owner. A second invocation sends
absolute local paths (including Unicode, spaces and local file URLs) to that
owner and exits after acknowledgement. Starting without files activates it.
Different libraries can have independent windows, selected with `--data-dir` or
the saved library choice. Launch files are consumed once, so switching libraries
does not import them again. Imports run as background workspace jobs. A closing
window rejects delivery rather than acknowledging work that would be lost.

Delivery uses Qt local sockets/named pipes with user-only access, bounded JSON
messages and timeouts, never TCP or HTTP. On POSIX the socket lives in a private
0700 directory because macOS ignores Unix socket permissions. Only a process
holding the library lock may replace a stale endpoint. Windows grants the owner
foreground permission before delivery; Qt requests activation on other desktops.
Compositor focus restrictions still require interactive desktop checks.

## PDF registrations

- Linux: the per-user desktop entry declares `application/pdf` and quoted `%F`
  file arguments. Installation and removal refresh the desktop database when
  its tool is available. Existing default-reader preferences are preserved.
- Windows: an unchecked setup task adds the per-user `PaperAssist.PDF` ProgID,
  OpenWithProgids and application capabilities. Uninstall removes those owned
  entries. Setup never writes the `.pdf` default value or UserChoice.
- macOS: the application declares the PDF UTI with Viewer role and Alternate
  rank. Finder/Dock events are buffered until the library window is ready.

Choose a default through the operating system if desired. QtNetwork is included
for local delivery; QML, Quick and TLS/network-information plugins stay excluded.

## Verification

`test_file_opening` checks managed-copy identity, revisions, foreign libraries,
source integrity and per-file failures. `test_native_instance` covers framing,
invalid/oversized requests, aliases, receiver errors, Finder event buffering and
dirty-pane reuse. `test_native_file_delivery` runs two real application owners
and repeated CLI invocations, checking routing, deduplication, unsaved ink and a
comment draft. Lifecycle tests cover lock-startup races and library switches.

Platform acceptance extends these source checks to the installed Windows shell
command and registry cleanup, the copied macOS app with cold/warm LaunchServices
file events, and the Linux bundle/desktop entry. Current run results and remaining
manual checks are recorded in [file-opening acceptance](file-opening.md).

Signing, notarization and interactive desktop/hardware coverage remain separate
follow-ups.
