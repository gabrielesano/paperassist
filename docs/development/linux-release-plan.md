# First standalone Linux release

Historical release plan: standalone Linux, Windows and Apple Silicon macOS
packages have since been published. Current work concerns the next alpha with
bundled OCR and native desktop integration; see [the roadmap](../roadmap.md) and
[host acceptance](host-acceptance.md). Completion and pending-status statements
below describe their original workstream, not the current release state.

Follow-up 2026-11-08: **0.1.0a3** addresses the remaining maintenance and ELF
audit findings. See [current verification](linux-alpha-maintenance.md). The a1/a2
artifacts are preserved; the a2 workstream records below remain historical.

Updated 2026-11-07 for release candidate `0.1.0a2`. Target confirmed by the user: install
PaperAssist on Linux with one command, without requiring the user to install Python.

These are ordered work packages for upcoming sessions, not time estimates.
Ask the user about the open product choices before implementing their dependent
steps. Update completed tasks with the tested commit, commands and outcomes.

## Starting point

- Native parity: 104 accepted rows, five host-only exclusions and no open rows in
  [the acceptance inventory](native-migration.md). That Linux acceptance record
  does not establish release portability.
- Commit `ae1754d` contains the baseline native implementation.
- [CI run 42364131549](https://github.com/gabrielesano/paperassist/actions/runs/42364131549)
  passed all five jobs on Python 3.11, 3.13, and Node.
- [PyInstaller packaging](../../packaging/README.md) and a single-script per-user
  installer generator exist.
- **Independent Review of Alpha `0.1.0a1` (`tmp/qa/release-review/assessment.md`)**:
  Independent review of `0.1.0a1` (`dist/PaperAssist-0.1.0a1-linux-x86_64.sh`, SHA-256
  `8fbdf9e3bf42199cc19dc24858be6b13722fdf1d4952e2267d96087d4d4aa868`, retained as
  review evidence) reproduced two release-blocking recovery defects:
  1. `auto_recover` deleted unowned adjacent `.backup.*` directories before library-target
     validation.
  2. `--repair` deleted the valid prior backup after an interrupted post-swap/pre-manifest
     upgrade.
  The review also identified verification gaps:
  1. Acceptance reported source-level in-process widget tests (`MainWindow`, `RenderClient`,
     `Workspace` in `.venv`) alongside black-box process checks, and claimed "tiled rendering"
     when `view.py` rasterizes budgeted whole-page bitmaps.
  2. A sanitized `HOME`/`PATH` environment on the Fedora 43 development host was reported
     as clean-system distribution verification, conflating host environment isolation on
     Fedora 43 (`glibc 2.42`) with clean Ubuntu/Debian target compatibility (`glibc >= 2.35`).
- Candidate **`0.1.0a2`** resolves these defects, hardens installer recovery, adds explicit
  transaction markers and concurrency locking, and establishes rigorous verification boundaries.

## Session 1 — Restore green CI

Completed 2026-11-07 on commit `f73ce91`.

- [x] Refresh the latest run and reproduce its failures before changing code.
  The 3.11 cause came from the CI log and code: before Python 3.12,
  `Path.iterdir()` lists through `os.listdir`, so the test's injected `os.scandir`
  failure reached the dialog's pre-scan but not the importer. The Windows failure
  was reproduced on Linux by a regression test with wide font metrics.
- [x] Fix the unreadable-selection regression test on Python 3.11. The importer
  and the Library dialog now share one `os.scandir` traversal
  (`library.pdf_files`); the assertion is unchanged and passes on 3.11 and 3.14.
- [x] Fix Windows layout at 150% interface size. A fixed 160 px column minimum
  hid the panel's own size hint from the splitter; the side columns now widen to
  their controls, including an expanded reference card.
- [x] Run focused regressions, then verify all configured CI jobs. Locally: 619
  Python tests on 3.14 and 3.11, 61 Node tests, and the native window under Xvfb.
  [CI run 42364131549](https://github.com/gabrielesano/paperassist/actions/runs/42364131549)
  passed all five jobs: Ubuntu and Windows on Python 3.11 and 3.13 (619 tests
  each), and Node. Both 150% layout tests ran and passed on Windows. That run
  tested the same source tree before agent instruction files were removed from
  the history, which changed every commit hash.

The same commit escapes `&` in Qt labels built from titles, starts find from the
current page, and delivers SIGTERM through a wakeup socket instead of polling.

Acceptance: the repository CI matrix is green. Linux release scope does not
silently change the existing CI requirements.

## Session 2 — Confirm the release target and rebuild

Completed 2026-11-07 for `0.1.0a1` review baseline and `0.1.0a2` candidate rebuild.

- [x] Confirm the distribution format with the user: autonomous PyInstaller
  one-dir bundle packaged into a self-extracting single-script per-user installer
  (`.sh`), with Flatpak deferred.
- [x] Confirm the initial CPU architecture and supported distributions/baseline.
  Selected architecture: `x86_64`. Established and verified baseline distinctions:
  the local build artifact is compiled on Fedora 43 (`glibc 2.42`, `libstdc++
  GLIBCXX_3.4.34`) and verified under X11/Xvfb and Mutter/Weston Wayland; automated
  CI (`.github/workflows/build.yml`) is configured for Ubuntu 22.04 (`glibc >= 2.35`).
  *(Note: An Ubuntu 22.04 installer build has not been executed or tested in this offline
  development environment; older-baseline compatibility remains unexecuted).*
- [x] Rebuild the current commit, including Python 3.14.7, PySide6-Essentials 6.10.3 /
  Qt 6.10.3 (`xcb`, `wayland`, `offscreen`), PDFium 7520 via pypdfium2 5.7.0,
  pypdf 6.10.2, About dialog metadata, desktop assets, and third-party notices.
  Bundled `libsqlite3.so.0` inside `_internal/` to eliminate external SQLite version
  dependencies. Deterministically generated `dist/PaperAssist/THIRD-PARTY-NOTICES.txt`
  (`18,162` bytes, SHA-256: `0be5411e205e8b31690a1509fb179418de5a81819bb28e07fca3dba9a64c44d3`)
  and `dist/PaperAssist/BUILD-PROVENANCE.json` (`2,535` bytes, SHA-256:
  `00e119aebcd0a6db7d0d77d9c7439d44de8f81845a7212fee3e6e90d07796ad5`).
  Conducted empirical ELF audit: `GLIBC_2.14` bootloader floor; bundle links `GLIBC_2.42`
  via `_internal/libtinfo.so.6` and `libpython3.14.so.1.0`.
- [x] Run the frozen renderer self-test and the real application. Recorded sizes:
  - Candidate `0.1.0a2` uncompressed bundle: `136,923,779` bytes (`130.58 MiB`, 212 files / 14 symlinks)
  - Candidate `0.1.0a2` installer: `56,252,190` bytes (`53.65 MiB`, payload `56,213,473` bytes, offset `38,717` bytes, SHA-256: `42067eb133c8dd5e26ed7f6ed1e49545e43c97d5cd4da28959f9c8d3aff4fe0a`)
  - Historical review evidence `0.1.0a1` retained: `56,238,650` bytes, SHA-256: `8fbdf9e3bf42199cc19dc24858be6b13722fdf1d4952e2267d96087d4d4aa868`.

Acceptance: an identifiable candidate build (`0.1.0a2`) runs outside the checkout without
a system Python installation. Architecture: Linux `x86_64`. Local build baseline: `glibc >= 2.42`.

## Session 3 — Install, update and uninstall

Completed 2026-11-07 for `0.1.0a1`; recovery hardening applied and verified for `0.1.0a2`.

- [x] Implement the agreed one-command installation path. Built
  `packaging/installer/build_installer.py` producing a self-extracting standalone
  POSIX shell installer (`PaperAssist-0.1.0a2-linux-x86_64.sh`). Installs per-user
  without `sudo` into `${XDG_DATA_HOME:-~/.local/share}/paperassist-app`, maintaining
  strict separation from user libraries (`~/.local/share/paperassist`) and
  configuration (`~/.config/paperassist/config.json`).
- [x] Install a native launcher wrapper (`~/.local/bin/paperassist`), desktop entry
  (`paperassist.desktop`), and scalable SVG + 256x256 PNG icons. Verified startup
  from application launcher and terminal from arbitrary working directories.
  Implemented foreign-file protection: the installer detects unowned pre-existing
  launchers and aborts before touching unmanaged files.
- [x] Verify downloads/checksums and make interrupted installation or update
  recoverable. Added pre-extraction payload size and SHA-256 validation (`--verify`),
  detached checksum file (`.sha256`), and unified `dist/SHA256SUMS`.
  **Recovery protocol verified for `0.1.0a2`**:
  - Step A: Target validation executes first; target containing `library.sqlite3` or
    non-directory is immediately rejected with zero side effects.
  - Step C: Foreign non-empty directories lacking valid manifests are protected and refused.
  - Step D: Concurrency lock (`<install-dir>.lock/owner` with PID) prevents concurrent
    invocations from deleting or interfering with active backups/staging.
  - Explicit markers: `.paperassist-install-manifest`, `.paperassist-staging-marker`,
    `.paperassist-backup-marker`, `.paperassist-rollback/marker`. Unowned adjacent `.backup.*`
    or `.staging.*` directories are never touched.
  - Manifest created only at final commit point; post-swap interruption safely restores
    prior installation via `auto_recover()` / `--repair`.
- [x] Support explicit upgrades and uninstall only installation-owned files;
  preserve PDFs, annotations, notes, settings and the selected library. Upgrades
  stage and validate in isolation, back up the prior installation, and swap
  atomically with automatic rollback on error. Added uninstaller
  (`~/.local/share/paperassist-app/uninstall.sh` and `--uninstall`). Covered by
  extended regression suite: **19/19 tests passed** in `tests/test_linux_installer.py`.

Acceptance: one documented command installs a usable native app. Repeating it,
upgrading, repairing, and uninstalling have tested, predictable outcomes.

## Session 4 — Complete PDF opening and desktop integration

Status: Intentionally deferred for subsequent release work.

- [ ] Verify the native end-user command and remove ambiguity with the transitional
  web command. Decide the source CLI transition before changing existing commands.
- [ ] Implement file delivery from a second invocation to the appropriate existing
  native instance and focus it. Define behavior when another library owns a file
  or the current document has unsaved edits; ask about genuinely new UX choices.
- [ ] Verify all external-file entry points, including file arguments and Open:
  import or reuse managed copies, preserve supplied originals, and retain explicit
  Save and existing unsaved edits. Do not assume the loose-file prototype provides
  the managed-library guarantees required for desktop PDF handling.
- [ ] Register optional PDF handling after file delivery is verified. Let users
  choose their default reader through the operating system.

Note: Desktop PDF file association (`MimeType=application/pdf` in `paperassist.desktop`)
and single-instance IPC file handoff from desktop file managers remain intentionally
omitted in this alpha to ensure managed-library guarantees are carefully preserved.
The launcher starts PaperAssist cleanly from desktop application menus and CLI.

Acceptance: opening a PDF through the desktop reaches the correct native reader,
keeps source bytes intact and does not create conflicting editable owners. See
the remaining integration requirements in [the desktop plan](desktop-plan.md).

## Session 5 — Test clean Linux installations

Status: Fedora 43 (`glibc 2.42`) isolated environment testing complete; clean older-distribution baseline testing pending.

- [ ] Install on clean systems covering the agreed distribution baseline and
  architectures, without Python, the source checkout or development libraries.
  *(Note: Testing in a sanitized HOME/PATH environment on the Fedora 43 development host
  tests checkout/.venv isolation on Fedora 43 `glibc 2.42`; it does NOT establish clean-OS
  compatibility on older distribution baselines such as Ubuntu 22.04 or Debian 12
  with `glibc >= 2.35`. Testing on an actual clean system with an older glibc baseline
  remains unexecuted).*
- [x] Check desktop launch, file/folder dialogs, clipboard, system-browser links,
  fonts and rendering under the supported X11/Wayland environments. Verified under
  X11 (Xvfb) and Wayland (Mutter 49.8 headless, Weston 14.0.2) on Fedora 43: window
  creation, input widgets (`FindBar`, `PageField`, `ReferencesPanel` filter, `Comments` text
  editor), focus restoration (`Escape`), clipboard, shortcuts, and dialogs.
  Investigated upstream Qt 6.12 Wayland `QWaylandTextInputv3::disableSurface`
  warning trace, confirmed zero functional impact, implemented surgical filter in
  `src/paperassist/native/wayland.py`, and verified with `tests/test_native_wayland.py`
  (documented in `docs/development/wayland-investigation.md`).
- [x] Exercise import/reimport, local citation consultation, quick-view promotion,
  selection, ink/highlights/comments, Save/reopen and Save/Discard/Cancel on exit
  and SIGTERM. Check offline reading and recoverable retrieval failures. Verified
  via in-process widget unit tests and black-box process-level execution (`tests/test_final_linux_acceptance.py`:
  16/16 passed, comprising 12 black-box frozen executable tests and 4 source-level widget regressions).
  (Note: `view.py` rasterizes budgeted whole-page bitmaps, scaling down when necessary to observe the
  page cache budget; it does not use a tiled renderer).
- [x] Test upgrade, failed upgrade and uninstall with a disposable populated
  library. Verify retained data and original hashes; record limitations and
  failures honestly before accepting the build. Validated that SQLite catalog, original
  PDFs (SHA-256 unaltered), and user configuration are preserved across upgrades and uninstalls.
  Verified recovery fixes preventing deletion of unowned directories and safeguarding prior
  backups during interrupted upgrades (19/19 tests passed in `tests/test_linux_installer.py`).

Measured runtime performance on installed frozen binary:
- Cold `--self-test`: `536.9 ms` (warm: `538.4 ms`)
- GUI startup to mapped X11 window: `519.8 ms`
- Combined memory PSS: `124.37 MiB` at 100% zoom; `189.02 MiB` at 400% zoom (well within < 450 MiB budget).

Acceptance: Repeatable installation-to-uninstall records exist for Linux `x86_64` on
Fedora 43 (`glibc >= 2.42`). Verification on older distribution baselines (`glibc >= 2.35`)
awaits clean-OS target testing.

## Session 6 — Prepare and publish the alpha

Candidate `0.1.0a2` preparation completed; publication deferred pending user authorization.

- [ ] Confirm the release audience and download access: private alpha evaluation
  (`0.1.0a2`) for Linux `x86_64`. Public repository hosting and release publication
  remain intentionally deferred pending user authorization and baseline verification.
- [x] Set an alpha version and keep About, package metadata, artifact names and
  the release tag consistent. Set `__version__ = "0.1.0a2"`, matching About dialog
  and installer filename (`PaperAssist-0.1.0a2-linux-x86_64.sh`). Detached checksums
  (`PaperAssist-0.1.0a2-linux-x86_64.sh.sha256`, `dist/SHA256SUMS`), notices
  (`dist/PaperAssist/THIRD-PARTY-NOTICES.txt`), and source availability aligned.
  *(Prior artifact `PaperAssist-0.1.0a1-linux-x86_64.sh` retained as historical review evidence).*
- [x] Document the real installation/update/uninstall commands, supported targets,
  library location, backups and known limits. Do not publish placeholder commands.
  Documentation updated in `packaging/README.md`, `packaging/installer/README.md`,
  and `README.md`.
- [ ] Prepare reviewable release artifacts and notes, obtain authorization for
  publication, then publish. Candidate `0.1.0a2` artifact prepared:
  `dist/PaperAssist-0.1.0a2-linux-x86_64.sh` (`56,252,190` bytes, SHA-256:
  `42067eb133c8dd5e26ed7f6ed1e49545e43c97d5cd4da28959f9c8d3aff4fe0a`), verified
  via `--verify` and test suites. Publication deferred pending user authorization.

Acceptance: an intended recipient can install and launch the alpha with the
documented command (`sh ./PaperAssist-0.1.0a2-linux-x86_64.sh`), without Python
or access to a development checkout on supported systems (`glibc >= 2.42`).

## Subsequent work

- Completed after native parity: retire the transitional web interface, HTTP
  server and pywebview host; preserve CLI import, library data and the existing
  process lock. Source launches now default to the native reader.
- Set up an automated containerized build environment (e.g., Ubuntu 22.04 / `glibc 2.35`)
  to produce and verify portable installer packages for older Linux distributions.
- Extend standalone release verification to Windows/macOS, including installers,
  platform file delivery, signing/notarization and upgrade/uninstall behavior.
- Keep OCR, advanced search and bibliographic integrations in the later product
  backlog unless actual use establishes a need.
