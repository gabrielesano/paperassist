# Release acceptance: 0.2.0a5

The [0.2.0a5 alpha](https://github.com/gabrielesano/paperassist/releases/tag/v0.2.0a5)
adds automatic outer-margin line-number stripping (`_extract_pages`,
`_detect_line_numbers`, and `_chain_line_numbers`), more resilient bibliography
heading detection (`_find_heading`), bumps `EXTRACTION_VERSION` to `6`, and adds
host CA certificate bundle fallback on Linux (`_fallback_ca_paths`,
`configure_ssl_env`, and `_ssl_context`) for standalone builds running on
distributions whose OpenSSL directory differs from the Ubuntu 22.04 build host.

## Published source and hosted acceptance

Tag `v0.2.0a5` points to source commit
`a696fa053cd4f408110aa575144ce997a74fbce7`. The subsequent acceptance
documentation update does not change the packaged source.

Local verification passes all 65 tests in `tests.test_references`, all 193 tests
across `tests.test_retrieval`, `tests.test_crossref_metadata`,
`tests.test_arxiv_metadata`, `tests.test_references`, and
`tests.test_metadata_refresh`, and all 28 tests in `test_native_acquisition.py`.

[Source CI run 43975764048](https://github.com/gabrielesano/paperassist/actions/runs/43975764048)
and [tag CI run 43975770082](https://github.com/gabrielesano/paperassist/actions/runs/43975770082)
passed on Ubuntu and Windows.

[Build run 43975845324](https://github.com/gabrielesano/paperassist/actions/runs/43975845324)
passed all three package jobs (`ubuntu-22.04`, `windows-latest`, and `macos-latest`),
covering frozen rendering, bundled OCR, installer self-tests/install/uninstall,
ELF `GLIBC_2.35` auditing on Linux, and the native Cocoa suite and mounted DMG
checks on Apple Silicon macOS.

All three package provenances report version `0.2.0a5`, source `a696fa0` and a
clean checkout. Downloaded installer hashes match their build-generated checksum
files, and the downloaded Linux installer passes its embedded integrity check
(`--verify`, payload SHA-256 `2477049157bb9ce43f610ffc20cf5a9ae856a7c160b74f9cf5871ac65ee4066f`).

## Download checksums

| Installer | Bytes | SHA-256 |
| --- | ---: | --- |
| `PaperAssist-0.2.0a5-linux-x86_64.sh` | 74,315,839 | `dd1b35be0cb12345a2387e4e2826063d35717e5268ee2864e6a262343922b169` |
| `PaperAssist-0.2.0a5-windows-x86_64-setup.exe` | 36,144,393 | `30021c159c53716133d0662bbd64ba1cd56add941fc8670d1acd5ee9522a9c5c` |
| `PaperAssist-0.2.0a5-macos-arm64.dmg` | 39,274,311 | `20bf2f486a1c6f7d2f5589028aa7ee07acbc84b3fba9cea1526794180ee4223f` |

The release contains 18 assets: three installers and their individual checksums,
the Linux bundle archive, three build provenances, three OCR manifests, three
third-party notices, the GPL license and combined `SHA256SUMS`. The combined file
covers the other 17 assets and was verified locally before upload.

## Remaining scope

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization and additional architectures remain
outside this alpha's acceptance. Libraries and saved revisions remain compatible
with `0.2.0a4`; installation does not change the default PDF reader or library.
