# Release acceptance: 0.2.0a6

The [0.2.0a6 alpha](https://github.com/gabrielesano/paperassist/releases/tag/v0.2.0a6)
adds the native PDF right-click context menu (`PdfView.contextMenuEvent`,
`_build_context_menu`, `comment_requested`), excludes outer-margin and two-column
gutter line numbers from multi-line text selection and highlighting
(`detect_margin_line_numbers`), and resolves the Windows Python 3.13 Shiboken
`QAccessibleInterface` wrapper lifetime issue in `test_native_accessibility.py`.

## Verification status

Local Python test suites (`test_native_editing.py`, `test_native_core.py`,
`test_native_accessibility.py`, `test_library_window.py`) and virtual desktop
(`Xvfb` + `xdotool`) verification of the native context menu passed prior to
release tagging.

Build identifiers, package provenance, checksums and publication status will be
recorded after hosted verification.
