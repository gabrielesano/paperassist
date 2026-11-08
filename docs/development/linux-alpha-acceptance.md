# PaperAssist Linux Alpha Acceptance Report (`0.1.0a2`)

Historical report: the independent follow-up found additional maintenance and CI
audit defects in this candidate. **0.1.0a3** supersedes it; see the
[maintenance verification](linux-alpha-maintenance.md). The a2 installer and
review evidence remain unchanged.

**Release Version**: Candidate `0.1.0a2`

**Target Architecture**: Linux `x86_64`

**Candidate Package**: `dist/PaperAssist-0.1.0a2-linux-x86_64.sh` (56,252,190 bytes, 53.65 MiB)

**Candidate Package SHA-256**: `42067eb133c8dd5e26ed7f6ed1e49545e43c97d5cd4da28959f9c8d3aff4fe0a`

**Embedded Payload**: 56,213,473 bytes (53.61 MiB, offset 38,717 bytes)

**Embedded Payload SHA-256**: `f6d5c6622a1f92af17a477238e71d9c6def6905f33f1fce74e69bdf4682dd5f2`

**One-Dir Bundle**: `dist/PaperAssist/` (136,923,779 bytes / 130.58 MiB across 212 files and 14 symlinks)

**Historical Review Evidence Retained**: `dist/PaperAssist-0.1.0a1-linux-x86_64.sh` (56,238,650 bytes, SHA-256: `8fbdf9e3bf42199cc19dc24858be6b13722fdf1d4952e2267d96087d4d4aa868`)

**Status**: **ALL ACCEPTANCE TESTS PASSING (62/62 total tests across 5 test suites on Fedora 43)**

---

## 1. Executive Summary & Verification Methodology

This report documents the verification framework, independent review audit findings, and formal acceptance results for PaperAssist Linux `x86_64` standalone candidate `0.1.0a2`.

### 1.1 Independent Review Audit of `0.1.0a1`

On 2026-11-07, an independent review (`tmp/qa/release-review/assessment.md`) evaluated the initial package (`dist/PaperAssist-0.1.0a1-linux-x86_64.sh`, SHA-256 `8fbdf9e3bf42199cc19dc24858be6b13722fdf1d4952e2267d96087d4d4aa868`). That review reproduced two release-blocking installer defects and identified methodological verification gaps:

1. **Release Blocker 1 (High): Deletion of unowned adjacent backup directories**

   The `auto_recover` routine ran before library-target validation and ownership checks. Pointing the installer at an existing library folder correctly aborted installation, but immediately deleted all adjacent `<install-dir>.backup.*` directories without checking transaction ownership.
2. **Release Blocker 2 (High): Interrupted repair destroyed valid prior installation backup**

   Interruption after moving staging into place but before writing `.paperassist-install-manifest` left `<install-dir>` without a manifest. Running `--repair` deleted the valid backup directory merely because the target folder existed without a manifest, destroying the user's recoverable previous installation.
3. **Verification Gap 1 (Medium): In-process source testing conflated with frozen executable acceptance**

   Test cases in `tests/test_final_linux_acceptance.py` instantiated `MainWindow`, `RenderClient`, and `Workspace` inside the `.venv` test process rather than exercising the installed frozen executable. Furthermore, documentation claimed "sharp tiles" / "tiled rendering" when `view.py` actually rasterizes budgeted whole-page bitmaps.
4. **Verification Gap 2 (Medium): Development host isolation conflated with clean-OS distribution compatibility**

   Sanitizing `HOME` and `PATH` on the Fedora 43 development host tested checkout/.venv isolation, not clean operating system installation across baseline distributions (`glibc >= 2.35`). Host-bundled binaries link `glibc 2.42` symbols, preventing execution on Ubuntu 22.04 or Debian 12.

### 1.2 Corrections & Architecture in Candidate `0.1.0a2`

Candidate `0.1.0a2` resolves all review findings:

1. **Hardened Transaction & Recovery Protocol**:
   - **Step A (Immediate Destination Rejection)**: Destination safety is validated first. Any target containing `library.sqlite3` or a non-directory aborts immediately with zero side effects before any cleanup, recovery, or lock creation.
   - **Step C (Foreign Directory Protection)**: Existing non-empty directories without a valid manifest are preserved and refused.
   - **Step D (Concurrency Locking)**: Active operations acquire `<install-dir>.lock/owner` with PID tracking, preventing concurrent processes from corrupting or deleting active transactions.
   - **Explicit Transaction Markers**: Directories are managed exclusively via explicit markers (`.paperassist-install-manifest`, `.paperassist-staging-marker`, `.paperassist-backup-marker`, `.paperassist-rollback/marker`). Unowned adjacent `.backup.*` or `.staging.*` directories are never touched.
   - **Post-Swap Crash Recovery**: The installation manifest is written only at the final commit point. If interrupted post-swap, `auto_recover()` and `--repair` restore the valid prior installation backup.
2. **Strictly Separated Verification Tiers**:
   - **Tier 1: Installed-Executable Black-Box Process Acceptance** (12 black-box tests in `tests/test_final_linux_acceptance.py` driving ONLY `~/.local/bin/paperassist` -> `~/.local/share/paperassist-app/PaperAssist` via `Xvfb`, `xdotool`, `libX11` `XGetImage`, `mutter --wayland --headless`, `/proc/<pid>/exe`, `/proc/<pid>/smaps_rollup`, and on-disk SQLite/working inspection).
   - **Tier 2: Supplementary Source-Level In-Process Qt Widget Regressions** (4 tests explicitly designated as `.venv` in-process unit tests).
   - **Tier 3: Compatibility Baseline Accounting** (explicitly distinguishing Fedora 43 `glibc 2.42` local execution from unexecuted Ubuntu 22.04 CI targets).
3. **Accurate Viewer Architecture Documentation**:
   - PaperAssist's native viewer (`src/paperassist/native/view.py`) **does not use a tiled renderer**.
   - It rasterizes budgeted whole-page bitmaps via PDFium, scaling down only when necessary to fit the configured `PageCache` budget.

---

## 2. Test Environment & Build Provenance

### 2.1 Build Provenance (`BUILD-PROVENANCE.json`)

```json
{
  "version": "0.1.0a2",
  "git_commit": "be66815",
  "git_dirty": true,
  "git_dirty_count": 53,
  "build_platform": "Linux-7.2.8-100.fc43.x86_64-x86_64-with-glibc2.42",
  "host_glibc": "glibc 2.42",
  "python_version": "3.14.7",
  "bootloader_max_glibc": "GLIBC_2.14",
  "bundle_max_glibc": "GLIBC_2.42",
  "bundle_max_glibc_files": [
    "_internal/libtinfo.so.6"
  ],
  "bundle_max_glibcxx": "GLIBCXX_3.4.34",
  "bundle_max_cxxabi": "CXXABI_1.3.15",
  "external_shared_libraries": [
    "ld-linux-x86-64.so.2",
    "libEGL.so.1",
    "libGL.so.1",
    "libc.so.6",
    "libdl.so.2",
    "libm.so.6",
    "libpthread.so.0",
    "libwayland-client.so.0",
    "libwayland-cursor.so.0",
    "libwayland-egl.so.1",
    "libxcb.so.1"
  ]
}
```

### 2.2 Empirical ELF GLIBC Floor Analysis

Audited across all ELF binaries in `dist/PaperAssist/` via `readelf -V` and `strings`:
- **PyInstaller Bootloader** (`dist/PaperAssist/PaperAssist`): requires `GLIBC_2.14`.
- **Bundled Libraries** (`_internal/`):
  - `GLIBC_2.42`: `_internal/libtinfo.so.6`
  - `GLIBC_2.39`: `_internal/libsystemd.so.0`
  - `GLIBC_2.38`: 21 system libraries (`libpython3.14.so.1.0`, `libcrypto.so.3`, `libX11.so.6`, `libdbus-1.so.3`, etc.)
  - C++ ABI: `GLIBCXX_3.4.34`, `CXXABI_1.3.15`
- **Baseline Reality**: The local candidate package **strictly requires `glibc >= 2.42`** and is verified on Fedora Linux 43.
- **CI Portable Target**: `.github/workflows/build.yml` (`build-linux`) configures an Ubuntu 22.04 build (`glibc >= 2.35`) with an ELF verification gate (`GLIBC <= 2.35`), but this workflow has not been executed in this offline local development environment.

---

## 3. Automated Acceptance Test Results

All test suites pass completely across **62 automated tests**:

| Test Suite | Tests Passed | Pass Rate | Scope |
|:---|:---|:---|:---|
| `tests/test_final_linux_acceptance.py` | **16 / 16** | **100%** | 12 Black-Box Frozen Binary Tests + 4 Source-Level Regressions |
| `tests/test_linux_installer.py` | **19 / 19** | **100%** | Full install/upgrade/repair lifecycle, 6 cut points, PID lock |
| `tests/test_desktop_entry.py` | **10 / 10** | **100%** | Freedesktop desktop-file-validate, icons, Exec quoting |
| `tests/test_frozen_bundle.py` | **9 / 9** | **100%** | Bundle integrity, license notices, ELF GLIBC audit, provenance |
| `tests/test_native_wayland.py` | **8 / 8** | **100%** | Headless Wayland Mutter session, warning filter verification |
| **Total Acceptance Pass** | **62 / 62** | **100%** | **Green across all modules** |

### 3.1 Independent Review Defect Reproduction Benchmarks

Reproduced and verified using `tmp/qa/release-review/reproduce_recovery.py`:
- `tmp/qa/release-review/recovery-findings.json` (baseline `0.1.0a1`): **REPRODUCED FAILURES** (unowned adjacent backup deleted, interrupted post-swap repair deleted valid backup).
- `tmp/qa/release-review/recovery-findings-0.1.0a2.json` (candidate `0.1.0a2`): **BOTH DEFECTS VERIFIED RESOLVED**:
  - `rejected_library_target`: Target refused, foreign backup intact (`exit_code: 1`, `backup_intact: true`, `sentinel_intact: true`).
  - `post_swap_interruption`: Recovered prior installation (`repair_exit_code: 0`, `target_restored: true`, `self_test_exit_code: 0`).

---

## 4. Runtime Performance & Memory Measurements

### 4.1 Installed Frozen Application Binary (`~/.local/share/paperassist-app/PaperAssist`)

All measurements below were recorded against the **installed frozen binary** running as an independent process on Linux `x86_64` (Fedora 43), with memory sampled from `/proc/<pid>/smaps_rollup`:

| Scenario | Target Budget | Baseline (`0.1.0a1`) | Candidate (`0.1.0a2`) | Status |
|:---|:---|:---|:---|:---|
| **Cold `--self-test`** (spawn render process + render page 1 headless) | < 5,000 ms | 543.2 ms | **536.9 ms** | **PASS** (9.3x under budget) |
| **Warm `--self-test`** (subsequent execution) | < 2,000 ms | 548.7 ms | **538.4 ms** | **PASS** (3.7x under budget) |
| **GUI Startup to Mapped & Rendered X11 Window** | < 2,000 ms | N/A (in-process) | **519.8 ms** | **PASS** (3.8x under budget) |
| **Idle Memory (100% Zoom)**: UI Process | — | 116.99 MiB RSS | **117.14 MiB RSS / 98.28 MiB PSS** | **PASS** |
| **Idle Memory (100% Zoom)**: Worker Child Process | — | 38.80 MiB RSS | **39.34 MiB RSS / 26.09 MiB PSS** | **PASS** |
| **Combined System Footprint (100% Zoom)** | < 350 MiB PSS | 122.10 MiB PSS | **124.37 MiB PSS** | **PASS** (2.8x under budget) |
| **Memory Under 400% Zoom (Whole-Page Bitmaps)**: UI Process | — | 181.88 MiB RSS | **161.32 MiB RSS / 142.46 MiB PSS** | **PASS** |
| **Memory Under 400% Zoom (Whole-Page Bitmaps)**: Worker Child | — | 47.29 MiB RSS | **59.80 MiB RSS / 46.56 MiB PSS** | **PASS** |
| **Combined System Footprint (400% Zoom)** | < 450 MiB PSS | 179.72 MiB PSS | **189.02 MiB PSS** | **PASS** (2.4x under budget) |

### 4.2 Supplementary Source-Process Instrumentation (`.venv`)

Measured via in-process `RenderClient` signals:
- **Worker page render latency (100% Zoom, `page_ready`)**: `0.90 ms` (Target: < 500 ms)
- **Worker page render latency (400% Zoom, `page_ready`)**: `14.65 ms` (Target: < 1,000 ms)
- **Citation `locate` worker latency**: `13.17 ms` (Target: < 200 ms)

---

## 5. Acceptance Verification Checklist

### Requirement 1: Clean Environment Installation, Desktop Entry & Terminal Launch
- [x] **Standalone payload integrity**: Exact byte size (`56,213,473` bytes) and SHA-256 digest (`f6d5c662...`) validated prior to creating or modifying any target directories (`--verify`).
- [x] **Zero sudo requirement**: Standard user-space installation to `~/.local/share/paperassist-app` and launcher in `~/.local/bin/paperassist` without root privileges.
- [x] **Spaced directory support**: Installation into custom paths containing spaces succeeds without escaping errors in launchers or manifests.
- [x] **Desktop integration**: `paperassist.desktop` complies with Freedesktop Desktop Entry 1.0 specifications and passes `desktop-file-validate` without errors. Scalable SVG and 256x256 PNG icons installed cleanly.
- [x] **Attribution & Notices**: `LICENSE` (GPL-3.0-or-later), `THIRD-PARTY-NOTICES.txt` (`18,162` bytes), and `BUILD-PROVENANCE.json` (`2,535` bytes) verified in the installation root.
- [x] **CLI Wrapper**: `~/.local/bin/paperassist --version` outputs `PaperAssist 0.1.0a2`.
- [ ] **Clean-OS Target Baseline Compatibility**: Local artifact verified on Fedora 43 (`glibc >= 2.42`); verification on a clean Ubuntu 22.04 / Debian 12 target system (`glibc >= 2.35`) remains unexecuted.

### Requirement 2: Real GUI Acceptance of Installed Frozen Binary
- [x] **Headless Wayland compatibility**: Black-box frozen application verified under GNOME Mutter 49.8 headless session (`mutter --wayland --headless`). Parent and render worker processes start and map surfaces.
- [x] **Surgical Wayland warning filter**: Stderr logs confirm zero instances of upstream Qt 6 text-input focus warnings (`QWaylandTextInputv3::disableSurface`), preventing log pollution while retaining critical diagnostics.
- [x] **Headless X11 compatibility**: Verified under `Xvfb` via `xvfb-run -a` with `QT_QPA_PLATFORM=xcb`.
- [x] **Offline reading & byte-for-byte source preservation**: Source PDFs and `originals/` copies maintain byte-for-byte identical SHA-256 digests throughout reading and editing.
- [x] **Reading position restoration**: Reading positions (page number and scroll/zoom state) are stored upon close and reliably restored upon reopening.
- [x] **Strict explicit-Save rule**: No modifications are written to disk automatically. Unsaved edits trigger the Save/Discard/Cancel dialog:
  - **Cancel**: keeps document tab and edits open.
  - **Save**: writes revision to `working/<doc>-<rev>.pdf`, persists comments in SQLite, and leaves original PDF untouched.
  - **Discard**: closes document without committing changes.
- [x] **400% zoom rendering**: Rasterizes budgeted whole-page bitmaps sharply without blank frames, reducing scale if needed to observe `PageCache` budget constraints. (Does not use a tiled renderer).
- [x] **Independent split-reader navigation**: Page navigation, zoom, and find controls in the primary toolbar and second paper toolbar act exclusively on their respective viewport without cross-talk.
- [x] **Citation handling**:
  - In-text citation `[1]` (with matching local paper): opens the cited paper in Quick View.
  - In-text citation `[2]` (missing local paper): reveals and expands the reference card in the References panel without navigating the primary PDF away from the reading position.

### Requirement 3: Upgrade, Failed Upgrade Rollback, and Clean Uninstall
- [x] **Populated library preservation**: User library `${XDG_DATA_HOME}/paperassist` (`originals/`, `working/`, `library.sqlite3`, `settings.json`) and configuration `${XDG_CONFIG_HOME}/paperassist/config.json` are preserved across upgrades, failed upgrades, and uninstallation.
- [x] **Atomic upgrade**: Upgrades stage and validate in isolation, swap atomically, and update manifest while leaving user library digests identical.
- [x] **Integrity abort on truncated package**: Attempting an upgrade with a truncated installer fails before modifying installed files.
- [x] **Fault-injected rollback**: Injecting failure during atomic swap invokes rollback, restoring the previous working installation.
- [x] **Step A target validation**: Installer refuses foreign targets (such as library directories) with zero side effects before any cleanup or lock creation.
- [x] **Ownership marker verification**: Only directories bearing explicit ownership markers (`.paperassist-staging-marker`, `.paperassist-backup-marker`) are eligible for cleanup. Foreign adjacent directories are strictly preserved.
- [x] **Concurrency lock**: `<install-dir>.lock/owner` prevents concurrent installer processes from interfering with active backups.
- [x] **Post-swap recovery**: Interruption between directory swap and manifest creation is cleanly recovered by restoring the prior valid backup.
- [x] **Complete uninstallation**: Running `uninstall.sh` removes the application bundle, CLI launcher, desktop entry, and icons, leaving user data and configuration intact and identical.

---

## 6. Acceptance Sign-Off & Status

- **Baseline `0.1.0a1` Evaluation**: **REJECTED for Release** due to reproduced recovery defects and verification conflation. Package retained in `dist/` as review evidence.
- **Candidate `0.1.0a2` Evaluation**: **ACCEPTED (Green) on Fedora 43 (`x86_64`, `glibc >= 2.42`)**
  - All 62 tests passing across 5 test suites.
  - Both release-blocking recovery defects resolved and verified.
  - Genuine frozen-application black-box process tests and separate procfs measurements established.
  - Clean older-baseline distribution testing (`glibc >= 2.35` / Ubuntu 22.04+) remains unexecuted pending an older-baseline build.
  - Final publication remains deferred pending user authorization.
