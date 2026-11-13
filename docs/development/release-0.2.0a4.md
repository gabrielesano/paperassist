# Release acceptance: 0.2.0a4

The [0.2.0a4 alpha](https://github.com/gabrielesano/paperassist/releases/tag/v0.2.0a4)
fixes toolbar overflow visibility in narrow windows, including the second paper
in side-by-side Quick view. Qt reserves at least 24 logical pixels for the button;
a transparent resting border preserves icon space on focus. The proxy style
outlives widget-action teardown and is deleted after the toolbar closes.

## Published source and hosted acceptance

Tag `v0.2.0a4` points to source commit
`1bc25c289d2de5f33a2d4a6da2d7217bfcc53f5b`. The subsequent acceptance
documentation update does not change the packaged source.

Local verification passes 13 focused toolbar, navigation and theme tests and
nine Xcb native-window tests, including process shutdown. Regression cases cover
main and Quick view toolbars at 80%, 100% and 150% interface zoom, hidden actions,
stable focus geometry and style cleanup.

[Source CI run 43437265036](https://github.com/gabrielesano/paperassist/actions/runs/43437265036)
and [tag CI run 43437919270](https://github.com/gabrielesano/paperassist/actions/runs/43437919270)
passed all four Ubuntu/Windows jobs with Python 3.11 and 3.13. Source CI reports
936 tests on Ubuntu with 50/51 skips and 934 tests on Windows with 92/93 skips.

[Build run 43437349753](https://github.com/gabrielesano/paperassist/actions/runs/43437349753)
passed all three package jobs:

| Target | Automated acceptance |
| --- | --- |
| Ubuntu 22.04, x86_64 | Frozen rendering and bundled OCR, installer integrity/install/uninstall, packaging safeguards and 45 frozen/installer tests; all 151 ELF files satisfy the GLIBC_2.35 ceiling |
| Windows, x86_64 | Frozen rendering and bundled OCR, installed setup EXE, native GUI, upgrade, uninstall and data preservation |
| macOS, Apple Silicon | Native Cocoa suite (934 tests, 85 skips), frozen rendering and bundled OCR, mounted DMG, copied app, ad-hoc signature and native file delivery |

All three package provenances report version `0.2.0a4`, source `1bc25c2` and a
clean checkout. Downloaded installer hashes match their build-generated checksum
files. OCR manifests identify Tesseract 5.5.4 and the expected architecture;
third-party notices include the OCR licenses. The downloaded Linux installer
also passes its embedded integrity check.

## Download checksums

| Installer | Bytes | SHA-256 |
| --- | ---: | --- |
| `PaperAssist-0.2.0a4-linux-x86_64.sh` | 74,304,650 | `97c689d4b4a67bb0066bf02679283f1620889778367b2cbcfd5d88098f91e228` |
| `PaperAssist-0.2.0a4-windows-x86_64-setup.exe` | 36,135,019 | `e92a56a6dff10dd10701166ee750c1ff07eea53f53ca3e76abd038a9706164b7` |
| `PaperAssist-0.2.0a4-macos-arm64.dmg` | 38,989,644 | `01ed3047ff75ca4a7694f67a8454dd27f0c0350d59e7dc22f6f21de0009ca7c9` |

The release contains 18 assets: three installers and their individual checksums,
the Linux bundle archive, three build provenances, three OCR manifests, three
third-party notices, the GPL license and combined `SHA256SUMS`. The combined file
covers the other 17 assets. Existing alpha releases remain unchanged.
GitHub's uploaded asset names, sizes and SHA-256 digests were compared against
all 18 locally validated files before publication and matched exactly.

## Remaining scope

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization and additional architectures remain
outside this alpha's acceptance. Libraries and saved revisions remain compatible
with `0.2.0a3`; installation does not change the default PDF reader or library.
