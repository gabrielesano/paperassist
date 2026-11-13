# Packaging the native reader

For the single-file per-user Windows setup executable, see
[Windows packaging](windows/README.md).

For the Apple Silicon `.app` and drag-to-Applications `.dmg`, see
[macOS packaging](macos/README.md). The Linux installer workflow is documented below.

The current release is **0.2.0a3**, fixing background-job races, native window
lifecycle errors, foreign PDF annotation editing, citation resolution, metadata
refresh and Linux installer edge cases. Release notes are in
[0.2.0a3](../docs/releases/0.2.0a3.md); the
[bug audit record](../docs/development/bug-audit-2026-11.md) documents regression
coverage. Build commands below produce the current source version. See
[release acceptance](../docs/development/release-0.2.0a3.md) for the current
packages and [earlier maintenance verification](../docs/development/linux-alpha-maintenance.md)
for the historical `0.1.0a2` measurements and examples below.

PaperAssist provides two packaging tiers for Linux `x86_64`:
1. A PyInstaller one-dir application bundle (`dist/PaperAssist/`).
2. A standalone, self-extracting single-script installer (`dist/PaperAssist-<version>-linux-x86_64.sh`).

---

## 1. Building the Application Bundle

`packaging/paperassist.spec` builds a one-dir PyInstaller bundle of `python -m paperassist.native` (`packaging/entry.py` is its entry point). From the repository root, with CMake and a C/C++ compiler available:

```bash
python -m pip install "." "pyinstaller>=6.15,<7"
python packaging/ocr/build_ocr.py
pyinstaller packaging/paperassist.spec --noconfirm --distpath dist --workpath build
dist/PaperAssist/PaperAssist --self-test some.pdf   # exit code 0 = ok
```

`--self-test FILE.pdf` opens the file, draws page 1 through the render process with Qt's offscreen platform, and checks that the page is not blank. It runs completely headless without an active display server.

The manual CI workflow (`.github/workflows/build.yml`) runs this self-test on Linux, Windows, and macOS and uploads the resulting bundle artifacts.
Its optional `platform` input can rebuild one host without repeating the other
packages; for example, `gh workflow run build.yml -f platform=windows-latest`.

### Included offline OCR

Every standalone build includes the private OCR runtime produced by
`packaging/ocr/build_ocr.py`: pinned Tesseract 5 and Leptonica sources, with legacy
OCR, training tools, graphics, network/archive support and optional image codecs
disabled, plus only the English `tessdata_fast` LSTM model and TSV configuration.
Tesseract and Leptonica are statically linked together; compiler/system runtimes
are collected by PyInstaller as needed. No system OCR installation is used.

PyInstaller refuses missing, stale, modified or foreign-platform OCR inputs.
The runtime manifest records source URLs, SHA-256 hashes and the payload size per
platform. All three upstream licenses are included in the runtime and appended to
`THIRD-PARTY-NOTICES.txt`. See [the build and verification record](../docs/development/bundled-ocr.md).

`PaperAssist --self-test-ocr scan.pdf` exercises the bundled engine through the
real spawned worker. The release workflow runs `tests.test_frozen_ocr` against a
copied bundle with an empty `PATH`, foreign `TESSDATA_PREFIX` and a generated scan.
The test checks title/authors, original-file integrity and bundled notices.

### Bundle Filtering & Footprint

A Qt Widgets reader that renders its own PDF pages requires only a lean subset of Qt and system libraries:
- **Excluded Qt modules**: Qt QML, Quick, Qt translations, and designer plugins.
- **Excluded Qt plugin categories**: `imageformats`, `iconengines`, `tls`, `generic`, `networkinformation`, `egldeviceintegrations`, `designer`, `qmltooling`, `sqldrivers`.
- **Local file delivery**: QtNetwork supplies local sockets/named pipes; no HTTP server is started.
- **Kept platform plugins**: `xcb`, `wayland`, `windows`, `cocoa`, and `offscreen`.
- **Excluded theme plugins**: `libqgtk3` is excluded along with its deep GTK dependency tree; the lightweight FreeDesktop portal theme is retained.
- **Excluded system libraries**: GTK3 (`libgtk-3`, `libgdk`, `libcairo`, `libpango`), Kerberos, and extended X11 extensions.
- **Bundled database support**: `libsqlite3.so.0` is explicitly kept in the bundle payload (`_internal/libsqlite3.so.0`), ensuring Python's `sqlite3` module functions independently of whether the host system provides compatible SQLite packages.
- **Third-party notices**: `packaging/collect_notices.py` generates `dist/PaperAssist/THIRD-PARTY-NOTICES.txt` deterministically during the build, documenting PSF-2.0, LGPL-3.0, Apache-2.0, BSD-3-Clause, and public-domain licenses.

### Package Footprint & Sizes

- **Release Candidate (`0.1.0a2`)**:
  - **Uncompressed bundle (`dist/PaperAssist/`)**: `136,923,779` bytes (`130.58 MiB` across 212 files and 14 symlinks).
  - **Standalone installer (`dist/PaperAssist-0.1.0a2-linux-x86_64.sh`)**: `56,252,190` bytes (`53.65 MiB`).
  - **Embedded compressed payload**: `56,213,473` bytes (`53.61 MiB`, `tar.gz`, payload offset `38,717` bytes).
  - **Package SHA-256**: `42067eb133c8dd5e26ed7f6ed1e49545e43c97d5cd4da28959f9c8d3aff4fe0a`.
  - **Payload SHA-256**: `f6d5c6622a1f92af17a477238e71d9c6def6905f33f1fce74e69bdf4682dd5f2`.
  - **Provenance & Notices**:
    - `dist/PaperAssist/THIRD-PARTY-NOTICES.txt` (`18,162` bytes, SHA-256: `0be5411e205e8b31690a1509fb179418de5a81819bb28e07fca3dba9a64c44d3`)
    - `dist/PaperAssist/BUILD-PROVENANCE.json` (`2,535` bytes, SHA-256: `00e119aebcd0a6db7d0d77d9c7439d44de8f81845a7212fee3e6e90d07796ad5`)
- **Historical Review Evidence (`0.1.0a1`)**:
  - Uncompressed bundle (`dist/PaperAssist/`): `132 MiB` (`138,443,663` bytes across 211 files and 14 symlinks).
  - Standalone installer (`dist/PaperAssist-0.1.0a1-linux-x86_64.sh`): `56,238,650` bytes (`53.63 MiB`).
  - Embedded compressed payload: `56,211,680` bytes (`53.61 MiB`, `tar.gz`).
  - Package SHA-256: `8fbdf9e3bf42199cc19dc24858be6b13722fdf1d4952e2267d96087d4d4aa868`.

---

## 2. Building the Standalone Linux Installer

The installer generator packages the PyInstaller bundle and desktop assets into a standalone POSIX shell script:

```bash
python3 packaging/installer/build_installer.py
```

This outputs:
- `dist/PaperAssist-0.1.0a2-linux-x86_64.sh` (standalone installer script with embedded payload)
- `dist/PaperAssist-0.1.0a2-linux-x86_64.sh.sha256` (detached SHA-256 digest)
*(Note: `dist/PaperAssist-0.1.0a1-linux-x86_64.sh` is retained as historical review evidence).*

---

## 3. Supported Linux Systems & Baseline

### Architecture
- **Linux `x86_64`** (64-bit Intel/AMD).

### Baseline Distinction: Configured CI vs. Local Executed Build

- **Local build artifact (`dist/PaperAssist-0.1.0a2-linux-x86_64.sh`)**: Built and verified on **Fedora Linux 43 `x86_64`** (kernel `7.2.8`, `glibc 2.42`, `libstdc++` `GLIBCXX_3.4.34`). Because PyInstaller bundles host Python binaries and shared libraries (`libpython3.14.so.1.0`, `libtinfo.so.6`), this local artifact **strictly requires `glibc >= 2.42`** and cannot run on systems with older C runtimes. It has been verified locally on Fedora 43 under X11 (`Xvfb`) and Wayland (Mutter/Weston).
- **CI configuration (`.github/workflows/build.yml`)**: Configured to run on **`ubuntu-22.04`** (`glibc >= 2.35`), targeting broader distribution compatibility (Ubuntu 22.04+, Debian 12+, Fedora 40+) with an ELF `GLIBC <= 2.35` verification gate. **Important**: This is a target build configuration in CI; an Ubuntu 22.04 installer build has not been executed or tested on a clean Ubuntu/Debian installation in this offline local development environment. Binary compatibility with older baselines cannot be claimed solely on the basis of workflow configuration YAML.

### Empirical ELF GLIBC Floor of Local Artifact
An empirical `readelf -V` and `strings` audit of all ELF binaries in the candidate bundle (`dist/PaperAssist/`, recorded in `BUILD-PROVENANCE.json`) establishes:
- **PyInstaller Bootloader** (`dist/PaperAssist/PaperAssist`): requires `GLIBC_2.14`.
- **Bundled Shared Objects** (`_internal/`):
  - `GLIBC_2.42`: `_internal/libtinfo.so.6`
  - `GLIBC_2.39`: `_internal/libsystemd.so.0`
  - `GLIBC_2.38`: 21 system libraries including `libpython3.14.so.1.0`, `libcrypto.so.3`, `libX11.so.6`, `libdbus-1.so.3`
  - C++ ABI: `GLIBCXX_3.4.34`, `CXXABI_1.3.15`
Consequently, the local candidate artifact strictly requires **`glibc >= 2.42`** and is verified on Fedora Linux 43.

### Bundled vs. External Dependencies

#### Bundled Inside `_internal/`:
- CPython `3.14.7` runtime
- `PySide6-Essentials 6.12.0` and `Qt 6.12.0` (`xcb`, `wayland`, `offscreen` plugins, `libQt6Svg`, `libQt6OpenGL`)
- `PDFium 7520` (via `pypdfium2 5.14.0`)
- `pypdf 6.21.4`
- `libsqlite3.so.0` (public domain)
- OpenSSL (`libssl.so.3`, `libcrypto.so.3`)
- `fontconfig`, `freetype`, `harfbuzz`, `icu`, `dbus-1`, `xkbcommon`, and X11/XCB client support libraries

#### External Shared Libraries Provided by Host OS:
The host operating system and graphics stack must provide:
- **Base C runtime**: `ld-linux-x86-64.so.2`, `libc.so.6`, `libm.so.6`, `libdl.so.2`, `libpthread.so.0`
- **Graphics / OpenGL loader**: `libEGL.so.1`, `libGL.so.1`, `libGLX.so.0`, `libGLdispatch.so.0`
- **Wayland client ABI** (when running under Wayland): `libwayland-client.so.0`, `libwayland-cursor.so.0`, `libwayland-egl.so.1`
- **X11 client ABI** (when running under X11): `libxcb.so.1`

---

## 4. Installer Commands & Verification

### Verify Package Integrity
```bash
# Check detached sha256 checksum file
sha256sum -c PaperAssist-0.1.0a2-linux-x86_64.sh.sha256

# Verify payload integrity using the installer script
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh --verify
```

### Install Per-User (No `sudo` Required)
```bash
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh
```
Installs the application bundle to `~/.local/share/paperassist-app`, creates a CLI wrapper in `~/.local/bin/paperassist`, and installs `paperassist.desktop` with application icons.

### Launching
- **Application Menu**: Launch **PaperAssist** from your desktop launcher.
- **Terminal**: Run `paperassist` (or `~/.local/share/paperassist-app/PaperAssist`).

### Upgrades & Transaction Architecture
```bash
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh
```
Upgrades follow a strict transactional protocol:
- **Destination Validation**: Pre-flight validation confirms `--install-dir` is not a user library (`library.sqlite3`), aborting with zero side effects before any cleanup, recovery, or lock creation.
- **Foreign Directory Check**: Pre-existing non-empty directories without a valid manifest are preserved and refused.
- **Concurrency Locking**: Operations acquire `<install-dir>.lock/owner` with PID tracking, preventing concurrent processes from corrupting in-progress transactions.
- **Marker Validation**: All transaction directories use explicit markers (`.paperassist-staging-marker`, `.paperassist-backup-marker`, `.paperassist-rollback/marker`). Unowned adjacent `.backup.*` or `.staging.*` folders are never touched.
- **Atomic Swap & Deferred Manifest**: Staged files are swapped into place; `.paperassist-install-manifest` is written only after full integration completes.

### Repair Interrupted Installation
```bash
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh --repair
```
If an installation or upgrade is interrupted at any cut point (including post-swap/pre-manifest), `auto_recover` and `--repair` restore the valid prior installation from backup rather than destroying it. Verified across 19/19 test cases in `tests/test_linux_installer.py`.

### Uninstall
```bash
~/.local/share/paperassist-app/uninstall.sh
# or:
sh ./PaperAssist-0.1.0a2-linux-x86_64.sh --uninstall
```
Uninstalls exclusively application-owned files. User libraries, PDFs, annotations, comments, and settings are preserved.

---

## 5. Data Separation & Locations

The installer guarantees complete separation between software binaries and user data:

| Component | Filesystem Location | Touched on Upgrade / Uninstall? |
| --- | --- | --- |
| **Application Bundle** | `${XDG_DATA_HOME:-~/.local/share}/paperassist-app` | Yes (managed by installer) |
| **CLI Launcher** | `${XDG_BIN_HOME:-~/.local/bin}/paperassist` | Yes (managed by installer) |
| **Desktop Entry & Icons** | `${XDG_DATA_HOME:-~/.local/share}/applications/paperassist.desktop`, `.../icons/...` | Yes (managed by installer) |
| **User Library (PDFs, DB)** | `${XDG_DATA_HOME:-~/.local/share}/paperassist` (or custom location) | **No (never touched)** |
| **User Configuration** | `${XDG_CONFIG_HOME:-~/.config}/paperassist/config.json` | **No (never touched)** |

---

## 6. Known Limitations of Alpha (`0.1.0a2`)

1. **Historical PDF integration gap**: resolved in 0.2.0a1. The desktop entry declares `application/pdf` and `%F`; delivery reuses the selected library owner. Default-reader settings remain under user control.
2. **Background Updates**: Automatic background updates are not included in this release.
3. **Foreign File Protection**: If an unrelated file already exists at `~/.local/bin/paperassist` (such as from a previous `pipx` install) or destination directories, the installer validates ownership and refuses to overwrite foreign or unmanaged files.
4. **Wayland Text-Input Trace**: Upstream Qt 6.12 text-input race condition warnings (`QWaylandTextInputv3::disableSurface: Trying to disable ... but 0x0 is focused`) are surgically filtered at runtime without masking errors (`PAPERASSIST_RAW_QT_LOGS=1` disables filtering; see `docs/development/wayland-investigation.md`).
5. **Distribution Baseline**: The local build artifact requires `glibc >= 2.42` (Fedora 43). Testing on older glibc baselines (Ubuntu 22.04 / glibc 2.35) requires a containerized or clean-OS build environment.
