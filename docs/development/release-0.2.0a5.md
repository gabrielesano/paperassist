# Release acceptance: 0.2.0a5

This maintenance alpha adds automatic outer-margin line-number stripping
(`_extract_pages`, `_detect_line_numbers`, and `_chain_line_numbers`), more
resilient bibliography heading detection (`_find_heading`), bumps
`EXTRACTION_VERSION` to `6`, and adds host CA certificate bundle fallback on
Linux (`_fallback_ca_paths`, `configure_ssl_env`, and `_ssl_context`) for
standalone builds running on distributions whose OpenSSL directory differs from
the Ubuntu 22.04 build host.

Local verification passes all 65 tests in `tests.test_references`, all 193 tests
across `tests.test_retrieval`, `tests.test_crossref_metadata`,
`tests.test_arxiv_metadata`, `tests.test_references`, and
`tests.test_metadata_refresh`, and all 28 tests in `test_native_acquisition.py`.

Release preparation targets Linux x86_64, Windows x86_64 and Apple Silicon macOS.
Hosted CI, Build provenance and updated download checksums will be recorded
after hosted verification completes.
