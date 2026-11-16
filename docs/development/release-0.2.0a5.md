# Release acceptance: 0.2.0a5

The [0.2.0a5 alpha](https://github.com/gabrielesano/paperassist/releases/tag/v0.2.0a5)
adds automatic outer-margin line-number stripping (`_extract_pages`,
`_detect_line_numbers`, and `_chain_line_numbers`), more resilient bibliography
heading detection (`_find_heading`), and bumps `EXTRACTION_VERSION` to `6` so
cached analyses refresh automatically.

## Published source and hosted acceptance

Tag `v0.2.0a5` points to source commit
`318221a4665f0c863903c3acfaa024d53b3443dd`. The subsequent acceptance
documentation update does not change the packaged source.

Local verification passes all 65 tests in `tests.test_references` (including
regression coverage in `LineNumberAndHeadingTests` for continuous/pagewise
margin line numbers, y-axis tick non-interference, and heading variants/fallback)
and all 31 tests in `test_native_citation*.py`.

[Source CI run 43972682540](https://github.com/gabrielesano/paperassist/actions/runs/43972682540)
and [tag CI run 43972682470](https://github.com/gabrielesano/paperassist/actions/runs/43972682470)
passed on Ubuntu and Windows.

[Build run 43972771738](https://github.com/gabrielesano/paperassist/actions/runs/43972771738)
passed all three package jobs (`ubuntu-22.04`, `windows-latest`, and `macos-latest`),
covering frozen rendering, bundled OCR, installer self-tests/install/uninstall,
ELF `GLIBC_2.35` auditing on Linux, and the native Cocoa suite and mounted DMG
checks on Apple Silicon macOS.

All three package provenances report version `0.2.0a5`, source `318221a` and a
clean checkout. Downloaded installer hashes match their build-generated checksum
files, and the downloaded Linux installer passes its embedded integrity check
(`--verify`, payload SHA-256 `b99f385f2cbad9fc8674c0b3065680004367a11878ff22c88b5dc50fa536f1e7`).

## Download checksums

| Installer | Bytes | SHA-256 |
| --- | ---: | --- |
| `PaperAssist-0.2.0a5-linux-x86_64.sh` | 74,320,510 | `3b4513ef843cda58c8ff8dd3b920e47ae45a77b53ae0e835543ffd2119575a38` |
| `PaperAssist-0.2.0a5-windows-x86_64-setup.exe` | 36,145,291 | `fd0d4563e13bcb761a5722fa967e95e0112be553f3850ddd75c78ff8417fd110` |
| `PaperAssist-0.2.0a5-macos-arm64.dmg` | 39,271,018 | `c1cd14c8116fd32e0c7691d9aa368e2d6a0e28583c1be2ecfe0e48c2ee40cc1c` |

The release contains 18 assets: three installers and their individual checksums,
the Linux bundle archive, three build provenances, three OCR manifests, three
third-party notices, the GPL license and combined `SHA256SUMS`. The combined file
covers the other 17 assets and was verified locally before upload.

## Remaining scope

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization and additional architectures remain
outside this alpha's acceptance. Libraries and saved revisions remain compatible
with `0.2.0a4`; installation does not change the default PDF reader or library.
