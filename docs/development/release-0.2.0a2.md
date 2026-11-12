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

Versioned packages are rebuilt from the release source commit on Ubuntu 22.04,
Windows and Apple Silicon macOS. Publication requires successful build acceptance,
matching source provenance, and locally verified artifact checksums. Hosted run
IDs and package hashes are recorded after those checks complete.

Clean Ubuntu/Debian desktop trials, broader hardware/accessibility checks,
Authenticode, Developer ID/notarization and additional architectures remain
outside this alpha's acceptance. Installation does not change the user's default
PDF reader or research library.
