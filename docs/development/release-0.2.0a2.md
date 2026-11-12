# Release acceptance: 0.2.0a2

The release combines reference-link reconciliation and the subsequent parser,
cross-page citation and acquisition-layout fixes. Implementation evidence is in
[the initial integration](reference-link-integration.md) and
[the follow-up record](reference-parser-followup.md).

The final local source verification covers 853 tests in 54 isolated modules,
with eight expected skips. Native windows, long-caption accessibility, four page
rotations, source-file integrity and read-only corpus comparisons were verified.
The six-paper development gold set reports 330 true positives, zero false positives
and nine false negatives. A fresh six-entry external sample reports 11/11 correct
citations without extras; these small samples do not establish complete coverage.

## Published source and hosted acceptance

The [0.2.0a2 alpha](https://github.com/gabrielesano/paperassist/releases/tag/v0.2.0a2)
uses tag `v0.2.0a2` at source commit
`6aefd3a107541b20945c2c91ac8dbcf8fb3989c6`. The subsequent documentation-only
acceptance update does not change the packaged source.

[Source CI run 43329871801](https://github.com/gabrielesano/paperassist/actions/runs/43329871801)
passed all four Ubuntu/Windows jobs with Python 3.11 and 3.13. Ubuntu runs report
877 tests with 50/51 skips; Windows runs report 875 tests with 90/91 skips.
The same four jobs also passed in
[tag CI run 43330676523](https://github.com/gabrielesano/paperassist/actions/runs/43330676523).

[Build run 43329886665](https://github.com/gabrielesano/paperassist/actions/runs/43329886665)
passed all three package jobs:

| Target | Automated acceptance |
| --- | --- |
| Ubuntu 22.04, x86_64 | Frozen rendering and OCR, installer integrity/install/uninstall, packaging safeguards and 43 frozen/installer tests; all 151 ELF files satisfy the GLIBC_2.35 ceiling |
| Windows, x86_64 | Frozen rendering and OCR, installed setup EXE, native GUI, upgrade, uninstall and data preservation |
| macOS, Apple Silicon | Native Cocoa suite (875 tests, 83 skips), frozen rendering and OCR, mounted DMG, copied application, ad-hoc signature and native file delivery |

All three package provenances report version `0.2.0a2`, source `6aefd3a` and a
clean checkout. Downloaded installer hashes match their build-generated checksum
files. OCR manifests identify Tesseract 5.5.4, the expected target architecture
and the pinned English model; third-party notices include the OCR licenses.
The downloaded Linux self-extracting installer also passes its integrity check.

## Download checksums

| Installer | Bytes | SHA-256 |
| --- | ---: | --- |
| `PaperAssist-0.2.0a2-linux-x86_64.sh` | 74,291,965 | `87c683580f180e8781a61b80904b21b1e4a97d8371d18c924aff1b1f714bf26e` |
| `PaperAssist-0.2.0a2-windows-x86_64-setup.exe` | 36,134,742 | `bc2968e12866c61b331953a51070d72b4b69a2dfd742c2e438cb1f65934b5fa3` |
| `PaperAssist-0.2.0a2-macos-arm64.dmg` | 39,224,888 | `1b078af9066ff7f3f81da7e3b95fd088307acdda36877cd65f4ede875f18c5d1` |

The release contains 18 assets: three installers and their individual checksums,
the Linux bundle archive, three build provenances, three OCR manifests, three
third-party notices, the GPL license and combined `SHA256SUMS`. The combined file
covers the other 17 assets. Existing alpha downloads remain unchanged.
GitHub's uploaded asset names, sizes and SHA-256 digests were compared against
all 18 local files before publication and matched exactly.

## Remaining scope

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization and additional architectures remain
outside this alpha's acceptance. Installation does not change the user's default
PDF reader or research library.
