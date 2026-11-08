# PaperAssist Linux Installer

Current candidate: **0.1.0a3**. See [maintenance verification](../../docs/development/linux-alpha-maintenance.md)
for current artifacts, first-install recovery, concurrency tests, and remaining
compatibility limits. The a2 artifact inventory below is historical.

This directory provides the generator and runtime templates for PaperAssist's
self-extracting Linux installer (`PaperAssist-<version>-linux-x86_64.sh`).

## Overview

The installer packages a PyInstaller one-dir application bundle (`dist/PaperAssist`)
and desktop integration assets into a single standalone POSIX shell script with an
embedded compressed `tar.gz` payload.

Key design principles:
- **Zero sudo requirement**: PaperAssist installs per user in standard user directories without administrator privileges.
- **Strict data and application separation**:
  - App files: `${XDG_DATA_HOME:-$HOME/.local/share}/paperassist-app`
  - User library: `${XDG_DATA_HOME:-$HOME/.local/share}/paperassist`
  - User configuration: `${XDG_CONFIG_HOME:-$HOME/.config}/paperassist/config.json`
  Installation, upgrade, or uninstallation **never** touches user libraries, PDFs, annotations, comments, or settings.
- **Pre-execution integrity verification**: Both the exact byte size and SHA-256 digest of the compressed payload are verified before any destination directory is created or modified.
- **Atomic upgrades with automatic rollback**: Upgrades extract and validate the new payload in an isolated staging directory, back up existing installations, and perform an atomic swap. If writing or validating any component fails, rollback restores the previous working installation.
- **Transactional recovery guarantees (`0.1.0a3` protocol)**:
  - **Step A: Immediate destination rejection**: Destination safety is validated first. If `--install-dir` points to a PaperAssist library (containing `library.sqlite3`), a non-directory, or an unsafe path, the installer aborts immediately with zero side effects before any cleanup, recovery, or lock creation.
  - **Step C: Foreign unmanaged directory check**: If the target directory exists and is non-empty but lacks a valid `.paperassist-install-manifest`, the installer refuses to overwrite or touch it.
  - **Step D: Concurrency locking**: Install, repair, and uninstall require `flock` (normally provided by util-linux) and hold an exclusive lock on the persistent `<install-dir>.lock.flock` file throughout maintenance. The file must remain in place so contenders always lock the same inode. A `<install-dir>.lock/owner` PID marker also lets newer releases detect an active older installer; stale markers are reclaimed while holding the flock. Concurrent maintenance aborts without modifying the active operation.
  - **Explicit ownership markers**:
    - `.paperassist-install-manifest`: Identifies fully committed, verified installations.
    - `.paperassist-staging-marker`: Identifies in-progress extraction staging folders.
    - `.paperassist-backup-marker`: Identifies owned installation backup folders.
    - `.paperassist-rollback/marker`: Retains launcher and desktop integration state for complete rollback.
    Unowned adjacent `<install-dir>.backup.*` and `<install-dir>.staging.*` directories without matching ownership markers are strictly preserved and never touched.
  - **Post-swap / pre-manifest interruption recovery**: If `<install-dir>` exists without a committed manifest and an owned `<install-dir>.backup.<pid>` exists (e.g., interrupted by `SIGKILL` after directory swap), `auto_recover()` and `--repair` remove the incomplete target and restore the valid prior backup (including launcher/desktop/icons via `.paperassist-rollback`).
  - **Verified against review defects**: Rejection without side effects (`rejected_library_target`) and crash recovery (`post_swap_interruption`) are verified fixed in `0.1.0a2` (`tmp/qa/release-review/recovery-findings-0.1.0a2.json`), with 19/19 passing tests in `tests/test_linux_installer.py`.
- **Foreign file protection**: Existing unowned CLI launchers (`~/.local/bin/paperassist`), desktop entries, non-empty directories, or pre-existing icons are detected before making changes; the installer refuses to overwrite foreign or unmanaged files.

## Target Systems & Compatibility Baseline

- **Local Candidate Artifact (`dist/PaperAssist-0.1.0a2-linux-x86_64.sh`)**:
  - Package Size: `56,252,190` bytes (`53.65 MiB`)
  - Package SHA-256: `42067eb133c8dd5e26ed7f6ed1e49545e43c97d5cd4da28959f9c8d3aff4fe0a`
  - Embedded Payload Size: `56,213,473` bytes (`53.61 MiB`, offset `38,717` bytes)
  - Embedded Payload SHA-256: `f6d5c6622a1f92af17a477238e71d9c6def6905f33f1fce74e69bdf4682dd5f2`
  - Uncompressed Bundle (`dist/PaperAssist/`): `136,923,779` bytes (`130.58 MiB`)
  - Local Host Environment: Fedora Linux 43 `x86_64` (`Linux-7.2.8-100.fc43.x86_64-x86_64-with-glibc2.42`, Python `3.14.7`, PyInstaller `6.22.3`).
  - Empirical ELF `GLIBC_*` Floor: PyInstaller bootloader requires `GLIBC_2.14`, while bundled system shared objects (`_internal/libtinfo.so.6`, `_internal/libpython3.14.so.1.0`) require `GLIBC_2.42`. Consequently, this local build strictly requires **`glibc >= 2.42`** and runs on Fedora 43+ or equivalent rolling distributions.
- **CI Build Configuration (`.github/workflows/build.yml`)**:
  - Configured for `ubuntu-22.04` (`glibc >= 2.35`) in the `build-linux` job with an ELF `GLIBC <= 2.35` verification gate.
  - Notice: This CI job is configured in repository workflow YAML, but has not yet been executed in GitHub Actions or a local Ubuntu 22.04 / Debian 12 container/VM in this offline development environment. Compatibility with older distributions cannot be assumed until an Ubuntu 22.04 build is executed and verified on a clean target OS.
*(Historical Review Evidence: `dist/PaperAssist-0.1.0a1-linux-x86_64.sh` [56,238,650 bytes, SHA-256: `8fbdf9e3bf42199cc19dc24858be6b13722fdf1d4952e2267d96087d4d4aa868`] is retained in `dist/` alongside `tmp/qa/release-review/recovery-findings.json`).*

## CLI Usage

### Building the Installer

```bash
# Package the built PyInstaller bundle
python3 packaging/installer/build_installer.py
```

Options:
- `--bundle-dir DIR`: Directory of PyInstaller one-dir bundle (default: `dist/PaperAssist`).
- `--desktop-dir DIR`: Directory containing desktop files and icons (default: `packaging/desktop`).
- `--output-dir DIR`: Output directory for generated `.sh` and `.sha256` (default: `dist`).
- `--output FILE`: Exact output file path.
- `--version VERSION`: Application version (default: read from `src/paperassist/__init__.py`).
- `--app-name NAME`: Application name (default: `PaperAssist`).

### Running the Generated Installer

```bash
# Verify integrity without making changes
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh --verify

# Standard user installation
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh

# Custom installation directories
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh --install-dir "$HOME/apps/paperassist" --bin-dir "$HOME/bin"

# Headless / server installation (no desktop entry or icons)
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh --no-desktop

# Repair an interrupted installation
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh --repair

# Uninstall
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh --uninstall
# Or invoke the uninstaller in the app directory:
~/.local/share/paperassist-app/uninstall.sh
```
