# Release acceptance: 0.2.0a3

This maintenance release addresses the consolidated bug audit and follow-up
foreign-PDF and comment-geometry cases. The
[audit record](bug-audit-2026-11.md) maps each fix to its regression coverage and
records local native-window checks and synthetic reader measurements.

Release preparation targets Linux x86_64, Windows x86_64 and Apple Silicon macOS.
The hosted Build workflow verifies native rendering, bundled offline OCR,
installer behavior and platform-specific package checks before uploading assets.
Source CI covers Ubuntu and Windows with Python 3.11 and 3.13; the macOS build
also runs the native Cocoa suite.

Build run identifiers, package provenance, checksums and publication status will
be recorded after the hosted builds and artifact verification finish.

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization and additional architectures remain
outside this alpha's acceptance.
