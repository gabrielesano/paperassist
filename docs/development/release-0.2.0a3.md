# Release acceptance: 0.2.0a3

The [0.2.0a3 maintenance alpha](https://github.com/gabrielesano/paperassist/releases/tag/v0.2.0a3)
addresses the consolidated bug audit and follow-up foreign-PDF and comment-geometry
cases. The [audit record](bug-audit-2026-11.md) maps the fixes to their regression
coverage and records local native-window checks and synthetic reader measurements.

## Published source and hosted acceptance

Tag `v0.2.0a3` points to source commit
`8b537dce8777bc92d9501853d3db49fbfd98bb56`. It contains the bug fixes, seven
follow-up regressions, and native button sizing and Cocoa layout-test corrections
found during release verification. The subsequent acceptance documentation update
does not change the packaged source.

[Source CI run 43430132603](https://github.com/gabrielesano/paperassist/actions/runs/43430132603)
and [tag CI run 43430945573](https://github.com/gabrielesano/paperassist/actions/runs/43430945573)
passed all four Ubuntu/Windows jobs with Python 3.11 and 3.13. Source CI reports
934 tests on Ubuntu with 50/51 skips, and 932 tests on Windows with 92/93 skips.

[Build run 43430232034](https://github.com/gabrielesano/paperassist/actions/runs/43430232034)
passed all three package jobs:

| Target | Automated acceptance |
| --- | --- |
| Ubuntu 22.04, x86_64 | Frozen rendering and bundled OCR, installer integrity/install/uninstall, packaging safeguards and 45 frozen/installer tests; all 151 ELF files satisfy the GLIBC_2.35 ceiling |
| Windows, x86_64 | Frozen rendering and bundled OCR, installed setup EXE, native GUI, upgrade, uninstall and data preservation |
| macOS, Apple Silicon | Native Cocoa suite (932 tests, 85 skips), frozen rendering and bundled OCR, mounted DMG, copied application, ad-hoc signature and native file delivery |

All three package provenances report version `0.2.0a3`, source `8b537dc` and a
clean checkout. Downloaded installer hashes match their build-generated checksum
files. OCR manifests identify Tesseract 5.5.4 and the expected target architecture;
third-party notices include the OCR licenses. The downloaded Linux installer
also passes its embedded integrity check.

The final local full run reports 934 tests, 21 skips and the two known version
assertions against the old `0.2.0a1` local bundle. The fresh hosted Linux bundle
passes its frozen-bundle suite. No source or packaging failures remain in the
published candidate. The first build attempt exposed two Cocoa layout assertions;
both were corrected and verified in the successful replacement build above.

## Download checksums

| Installer | Bytes | SHA-256 |
| --- | ---: | --- |
| `PaperAssist-0.2.0a3-linux-x86_64.sh` | 74,305,189 | `1f8c493f13aa29c3c524a6f2831d879b591176adc2bc822d26362b29b902630d` |
| `PaperAssist-0.2.0a3-windows-x86_64-setup.exe` | 36,143,112 | `1dfdef50cba6c06202df379a2aee6492c4e9d75d87ee9528cbffeade65bdcaf0` |
| `PaperAssist-0.2.0a3-macos-arm64.dmg` | 39,181,249 | `82a5da12ad27f9d50938ccd240ea6a0ccd6d114dd32f9ea6c4b4627a1afe08a3` |

The release contains 18 assets: three installers and their individual checksums,
the Linux bundle archive, three build provenances, three OCR manifests, three
third-party notices, the GPL license and combined `SHA256SUMS`. The combined file
covers the other 17 assets. Existing alpha releases remain unchanged.
GitHub's uploaded asset names, sizes and SHA-256 digests were compared against
all 18 locally validated files before publication and matched exactly.

## Remaining scope

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization and additional architectures remain
outside this alpha's acceptance. Installation does not change the user's default
PDF reader or research library.
