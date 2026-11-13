# Release acceptance: 0.2.0a4

This maintenance alpha fixes toolbar overflow geometry and focus styling in
narrow windows. A toolbar-specific proxy raises the extension extent to at least
24 logical pixels. Its lifetime extends through widget-action teardown, then
deferred deletion releases it after the toolbar closes.

Local verification passes 13 focused toolbar, navigation and theme tests and
nine Xcb native-window tests, including process shutdown. Regression cases cover
main and Quick view toolbars at 80%, 100% and 150% interface zoom, hidden actions,
stable focus geometry and style cleanup.

Release preparation targets Linux x86_64, Windows x86_64 and Apple Silicon macOS.
The hosted Build workflow verifies rendering, bundled OCR, installation and
platform package checks. Source CI runs Ubuntu and Windows with Python 3.11 and
3.13, and the macOS build runs the native Cocoa suite.

Build identifiers, package provenance, checksums and publication status will be
recorded after hosted verification.
