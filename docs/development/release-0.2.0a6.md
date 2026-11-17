# Release acceptance: 0.2.0a6

The [0.2.0a6 alpha](https://github.com/gabrielesano/paperassist/releases/tag/v0.2.0a6)
adds the native PDF right-click context menu (`PdfView.contextMenuEvent`,
`_build_context_menu`, `comment_requested`), excludes outer-margin and two-column
gutter line numbers from multi-line text selection and highlighting
(`detect_margin_line_numbers`), and resolves the Windows Python 3.13 Shiboken
`QAccessibleInterface` wrapper lifetime issue in `test_native_accessibility.py`.

## Published source and hosted acceptance

Tag `v0.2.0a6` points to source commit
`f718b99e9eb961807c1f6292188258f234608337`. The subsequent acceptance
documentation update does not change the packaged source.

Local verification passes all 28 tests in `tests.test_native_editing`, all 31
tests in `tests.test_native_core`, all 50 tests in `tests.test_library_window`,
and virtual desktop (`Xvfb` + `xdotool`) verification of the native PDF
context menu.

[Source CI run 44147552905](https://github.com/gabrielesano/paperassist/actions/runs/44147552905)
and [tag CI run 44147558598](https://github.com/gabrielesano/paperassist/actions/runs/44147558598)
passed on Ubuntu and Windows (Python 3.11 and 3.13).

[Build run 44147560248](https://github.com/gabrielesano/paperassist/actions/runs/44147560248)
passed all three package jobs (`ubuntu-22.04`, `windows-latest`, and `macos-latest`),
covering frozen rendering, bundled OCR, installer self-tests/install/uninstall,
ELF `GLIBC_2.35` auditing on Linux, and the native Cocoa suite and mounted DMG
checks on Apple Silicon macOS.

All three package provenances report version `0.2.0a6`, source `f718b99` and a
clean checkout. Downloaded installer hashes match their build-generated checksum
files, and the downloaded Linux installer passes its embedded integrity check
(`--verify`, payload SHA-256 `6cbf1176ccffe9d89d4d30569549a0323ff5ce482c402fd44dd56b49fdaef4fb`).

## Download checksums

| Installer | Bytes | SHA-256 |
| --- | ---: | --- |
| `PaperAssist-0.2.0a6-linux-x86_64.sh` | 74,320,748 | `eb8966c45b6a236ab7301c6523edda3f1e771982eab222e35b5e97e59b9d0788` |
| `PaperAssist-0.2.0a6-windows-x86_64-setup.exe` | 36,156,494 | `3327f59aff5f4e646dab7a6d2f3ac6a4a97d23492926dbd3419efe56ad005341` |
| `PaperAssist-0.2.0a6-macos-arm64.dmg` | 39,290,459 | `d217c67228bf04a977529db475eb81e1ccf466b472c6ea02b8b5d527479bed8f` |

The release contains 18 assets: three installers and their individual checksums,
the Linux bundle archive, three build provenances, three OCR manifests, three
third-party notices, the GPL license and combined `SHA256SUMS`. The combined file
covers the other 17 assets and was verified locally before upload.

## Remaining scope

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization and additional architectures remain
outside this alpha's acceptance. Libraries and saved revisions remain compatible
with `0.2.0a5`; installation does not change the default PDF reader or library.
