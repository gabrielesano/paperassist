# Roadmap

The product contract is in [specification.md](specification.md). Verification
records are in [development/](development/).

## Current work

Native parity is accepted: 104 rows and five host-only exclusions in the
[Linux migration inventory](development/native-migration.md). Standalone Linux,
Windows and Apple Silicon macOS packages already exist. The web stack is retired
and minimal offline OCR is included in new builds.

1. Prepare the next alpha release with bundled OCR. Set a new release version,
   rebuild and publish the verified artifacts. The
   existing `0.1.0a4` release downloads predate bundled OCR and are not replaced by
   CI artifacts. See [host acceptance](development/host-acceptance.md).
2. Implement native file delivery from a second invocation to the correct existing
   window, focusing it and preserving unsaved edits. Define the managed-library
   behavior before registering optional PDF associations on each platform. See
   [the desktop integration plan](development/desktop-plan.md).
3. Keep manual desktop acceptance as an explicit follow-up: clean Ubuntu/Debian
   GUI trials and interactive Windows/macOS reading, editing, focus and display
   checks. Hosted build and installer tests cover automated acceptance; untested
   environments remain documented rather than blocking local development.
4. Before broader distribution, address Windows signing and macOS Developer ID
   signing/notarization, and verify additional OS/architecture targets as needed.

## Completed transitions and automated acceptance

- Native source entry point, with CLI import, the existing library process lock
  and PDF.js annotation/note interoperability retained.
- Retirement of the web reader, Flask server, pywebview host, PDF.js vendor tree,
  npm manifests and Node CI job. See [web retirement](development/web-retirement.md).
- Minimal Tesseract 5, static Leptonica, the English LSTM model, runtime manifest
  and bundled license notices. No system OCR installation is used by frozen apps.
- Hosted Ubuntu 22.04, Windows and Apple Silicon macOS builds and copied-bundle
  OCR checks passed in [Build run 42661875263](https://github.com/gabrielesano/paperassist/actions/runs/42661875263).
  The Ubuntu bundle passes the GLIBC_2.35 gate. Per-platform payload sizes and
  the scope of installation/GUI checks are recorded in
  [host acceptance](development/host-acceptance.md).
- Installed-launcher/setup/DMG OCR checks passed on all three hosts in
  [Build run 42663515822](https://github.com/gabrielesano/paperassist/actions/runs/42663515822).
  The native Cocoa suite and the four Ubuntu/Windows Python CI jobs also passed
  after the keyboard and desktop-test corrections; see the acceptance record.

## Regression acceptance sequence

- Bulk import and reimport a real collection without duplicates or lost unresolved files.
- Open a library paper, annotate, save, close and reopen without losing changes.
- Consult a reference below the primary document, then promote it into a separate tab.
- Verify local citation ranking and visibly partial results against manually checked examples.
- Exercise local-file reuse, download selection, recoverable failures and search fallback.
- Check accessibility, narrow-window layout, active-document focus, and English UI.
- Verify application close/reopen, one owner per library and platform installation.
- Measure memory, render time and interface stalls with the reference papers.

## Later extensions

An OCR text layer for search and selection, external-file indexing, broader
citation-format coverage, advanced search, annotation export and bibliographic
integrations follow actual observed needs. A Rust implementation stays deferred;
see the [native stack decision](development/native-stack.md). LLMs are not used
for importing or organizing the local collection.
