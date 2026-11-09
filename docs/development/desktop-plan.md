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

## Remaining integration work

1. Deliver files from a second invocation to an existing native window and focus
   it. Preserve unsaved edits and define managed-library behavior before enabling
   operating-system PDF associations. The current loose-file mode is separate
   from managed-library import and can replace the explicitly opened file on Save.
2. Register optional PDF associations on each platform after file delivery works.
   Let users select their default reader through the operating system.
3. Complete standalone release verification on supported OS/architecture targets.
   Include runtime prerequisites and licenses; keep private state outside bundles.

For each platform, test a clean installation without Python, offline reading,
explicit annotation save/reopen, upgrade without library loss and uninstall
without deleting the library. Follow the current platform packaging records for
build and installation status. Signing and publication remain separate release
steps.
