# First standalone Linux release

Updated 2026-11-07. Target confirmed by the user: install PaperAssist on Linux
with one command, without requiring the user to install Python.

These are ordered work packages for upcoming sessions, not time estimates.
Ask the user about the open product choices before implementing their dependent
steps. Update completed tasks with the tested commit, commands and outcomes.

## Starting point

- Native parity: 104 accepted rows, five host-only exclusions and no open rows in
  [the acceptance inventory](native-migration.md). That Linux acceptance record
  does not establish release portability.
- Commit `ae1754d` contains the current implementation. The last local full suite
  passed 614 Python tests with four skips; 61 Node tests passed. Window-manager
  focus checks passed separately under Mutter/Wayland.
- [CI for that commit](https://github.com/gabrielesano/paperassist/actions/runs/42358543088)
  passed Ubuntu/Python 3.13 and Node, but failed Ubuntu/Python 3.11 and both Windows
  jobs. Session 1 fixed them; CI is green again from commit `f73ce91`.
- [PyInstaller packaging](../../packaging/README.md) and a manual three-platform
  build workflow already exist. Earlier bundle measurements and successful builds
  must not be presented as verification of the latest commit.
- The distribution format is still undecided. A PyInstaller bundle with a
  per-user installer is the proposed first-release route. Flatpak remains an
  alternative, with additional runtime, permission and repository integration.

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

- [ ] Confirm the distribution format with the user: autonomous bundle plus
  per-user installer, or Flatpak. If Flatpak is chosen, replace the bundle-specific
  installer tasks below with manifest, runtime, portal/permission and repository
  tasks; retain the functional and data-preservation checks.
- [ ] Confirm the initial CPU architecture and supported distributions/baseline.
  Proposed initial architecture: x86_64. Establish a compatible build environment
  rather than assuming a bundle built on the newest distribution runs everywhere.
- [ ] Rebuild the current commit, including Python, Qt, PDFium, annotation support,
  About metadata, assets and required license notices. Retain the render-process
  bootstrap and verify the native dependency diagnostics in the frozen program.
- [ ] Run the frozen renderer self-test and the real application. Record artifact
  size, startup time, memory and responsiveness with the reference scenario.

Acceptance: an identifiable build runs outside the checkout without a system
Python installation. Record its commit, architecture, baseline and measurements.

## Session 3 — Install, update and uninstall

- [ ] Implement the agreed one-command installation path. For the bundle route,
  install per user without administrator privileges and keep program files
  separate from the library and configuration directories.
- [ ] Install a native launcher, application icon and desktop-menu entry. Verify
  startup from the menu and terminal, including when the working directory is
  unrelated to the installation. Handle existing unrelated launchers safely.
- [ ] Verify downloads/checksums and make interrupted installation or update
  recoverable. Keep a working installation available if an update fails.
- [ ] Support explicit upgrades and uninstall only installation-owned files;
  preserve PDFs, annotations, notes, settings and the selected library.

Acceptance: one documented command installs a usable native app. Repeating it,
upgrading and uninstalling have tested, predictable outcomes. Automatic background
updates are a separate product choice, not assumed for the first alpha.

## Session 4 — Complete PDF opening and desktop integration

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

Acceptance: opening a PDF through the desktop reaches the correct native reader,
keeps source bytes intact and does not create conflicting editable owners. See
the remaining integration requirements in [the desktop plan](desktop-plan.md).

## Session 5 — Test clean Linux installations

- [ ] Install on clean systems covering the agreed distribution baseline and
  architectures, without Python, the source checkout or development libraries.
- [ ] Check desktop launch, file/folder dialogs, clipboard, system-browser links,
  fonts and rendering under the supported X11/Wayland environments.
- [ ] Exercise import/reimport, local citation consultation, quick-view promotion,
  selection, ink/highlights/comments, Save/reopen and Save/Discard/Cancel on exit
  and SIGTERM. Check offline reading and recoverable retrieval failures.
- [ ] Test upgrade, failed upgrade and uninstall with a disposable populated
  library. Verify retained data and original hashes; record limitations and
  failures honestly before accepting the build.

Acceptance: repeatable installation-to-uninstall records exist for the supported
targets. Fix release-blocking failures and rerun the affected checks.

## Session 6 — Prepare and publish the alpha

- [ ] Confirm the release audience and download access: private testing or public
  distribution. Choose artifact hosting that those users can access.
- [ ] Set an alpha version and keep About, package metadata, artifact names and
  the release tag consistent. Include checksums, notices and source availability.
- [ ] Document the real installation/update/uninstall commands, supported targets,
  library location, backups and known limits. Do not publish placeholder commands.
- [ ] Prepare reviewable release artifacts and notes, obtain authorization for
  publication, then publish. Verify the exact published one-command installation
  from a clean user account.

Acceptance: an intended recipient can install and launch the published alpha
with the documented command, without Python or access to a development checkout.

## Subsequent work

- Retire the transitional web interface, HTTP server and pywebview host through
  a deliberate migration; preserve required library/import commands and data.
- Extend standalone release verification to Windows/macOS, including installers,
  platform file delivery, signing/notarization and upgrade/uninstall behavior.
- Keep OCR, advanced search and bibliographic integrations in the later product
  backlog unless actual use establishes a need.
