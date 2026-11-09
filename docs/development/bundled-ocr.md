# Minimal bundled OCR

## Build and runtime

`packaging/ocr/build_ocr.py` builds a private Tesseract runtime for each release
host. It downloads official, version-pinned sources and the English model, checks
their SHA-256 digests, builds out of tree with CMake, and publishes only the runtime
files into ignored `build/ocr/runtime`.

Inputs:

- [Tesseract 5.5.4](https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.4),
  Apache-2.0, source SHA-256
  `a8dbf8b8c8ee427ff6ad1d99116502feaf59440af1adb893caf60a6576826cf4`.
- [Leptonica 1.87.0](https://github.com/DanBloomberg/leptonica/releases/tag/1.87.0),
  BSD-2-Clause, source SHA-256
  `fa2b40c5caea96d1bb93a97486262aed8731b69ce25a84a6bf5d25323e33f631`.
- [tessdata_fast 4.1.0 English model](https://github.com/tesseract-ocr/tessdata_fast/tree/4.1.0),
  Apache-2.0, model SHA-256
  `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`.

The build disables Tesseract's legacy engine, training tools, tests, graphical
debugger, OpenMP, libcurl, libarchive and TIFF support. Host-specific CPU tuning
is disabled; the upstream SIMD implementations keep their runtime CPU detection.
Leptonica disables zlib, PNG, GIF, JPEG, TIFF, WebP and OpenJPEG. The two libraries
are linked statically into the private executable. Compiler/OS runtimes remain
dynamic where appropriate and are collected by PyInstaller; Windows uses the
static MSVC runtime. No external OCR installation or model download is needed.

The final runtime contains the executable, `eng.traineddata`, the TSV config,
three license texts and a manifest. The manifest records inputs, architecture,
platform, profile, file sizes and hashes. PyInstaller verifies the input runtime
before collection and refuses incomplete, changed or foreign-host inputs. Build
workflows upload the manifest alongside the platform artifacts. The source and
model license texts are also appended to `THIRD-PARTY-NOTICES.txt`.

The existing OCR pipeline renders bounded grayscale PGM images in a spawned
worker, runs the private executable without a shell, then parses TSV. Its existing
pixel/page/time/output limits and cancellation remain in effect. On Windows,
subprocesses do not create console windows and cancellation uses the absolute
system `taskkill.exe` path. The UI process does not load Tesseract libraries.

Frozen apps find OCR under `sys._MEIPASS/ocr`, always use that engine and its
English model, and ignore a foreign `TESSDATA_PREFIX`. Their child keeps access to
the bundled compiler runtimes. A damaged installation produces a repair message;
it cannot silently fall back to a system engine. The cache format version is
bumped and distinguishes bundled from system engines, in addition to the PDF
hash, engine version, model hashes and render configuration.

Editable source installations prefer `build/ocr/runtime`. Source installations
without that build retain compatibility with system Tesseract 4.1+; this fallback
is not available to frozen apps.

## Commands

Install CMake and a C/C++ toolchain on the build host, then:

```sh
python packaging/ocr/build_ocr.py
pyinstaller packaging/paperassist.spec --noconfirm --distpath dist --workpath build
python -m unittest tests.test_frozen_ocr -v
```

`--jobs`, `--work-dir` and `--output-dir` support local build setups. Rebuild on
each target OS/architecture; do not reuse a runtime from another release host.

`PaperAssist --self-test-ocr scan.pdf` checks the bundled runtime through the real
spawned worker and reports recognized words and provisional title/authors. It
uses a temporary cache and does not open or create a user library or write a PDF
revision. The render-only `--self-test` remains available.

## Verification, 2026-11-09

Local build: Linux x86_64, Fedora 43, GCC 15.3.1, Python 3.14.7, CMake 3.31.11,
PyInstaller 6.22.3, Qt 6.12.0 and PDFium through pypdfium2 5.14.0.

| Runtime part | Bytes |
| --- | ---: |
| Stripped Tesseract executable, including static Leptonica | 1,377,688 |
| English model | 4,113,088 |
| TSV configuration | 22 |
| Three licenses | 24,237 |
| Manifest | 2,090 |
| Total runtime payload | 5,517,125 (5.26 MiB) |
| Standalone gzip-compressed runtime tar | 2,662,716 (2.54 MiB) |

The compressed runtime tar is an isolated size measurement, not the measured
delta of a complete installer. Windows/macOS sizes will be recorded by their
build manifests after those builds execute.

- Built the minimal engine and the complete frozen app locally. `ldd` shows only
  compiler/math/C runtimes for the OCR executable, with no codec, networking,
  archive, OpenMP, external Tesseract or external Leptonica libraries.
- Copied the frozen app outside the checkout to a path with spaces and Unicode.
  With an empty `PATH` and foreign model directory, it recovered 86 words,
  `Adiabatic Heating of Coronal Loops`, and `Ada Lovelace; Grace Hopper` from a
  generated image-only PDF. The original PDF hash was unchanged and no library
  was created. The copied bundle also contained all OCR licenses and only the
  English model.
- Repeated that frozen command in an offline Fedora 43 container with no system
  Tesseract executable or model directory. The image included Qt's standard OS
  prerequisites (OpenGL/EGL, xkbcommon, fontconfig and D-Bus), not OCR packages.
  The app and synthetic input were mounted read-only. The same title and authors
  were recovered with exit code zero.
- One sampled frozen self-test took 1.226 seconds. Peak combined process-tree PSS
  was 134.04 MiB, including the command's parent process and workers, sampled
  every 20 ms. This is a single synthetic page, not a corpus performance claim.
- Full regression suite: 814 tests in 89.067 seconds, successful with 20 skips.
  This includes frozen bundle and OCR checks, cancellation, timeout, cache,
  missing-language, rotated-page and source-integrity regressions, plus actual
  Wayland compositor checks outside the sandbox.
- An additional native Library regression was then added and passed with all
  23 tests in `test_native_metadata.py`: the real menu starts OCR, closing the
  Library window leaves it running, metadata updates on completion, and an open
  reader retains its unsaved highlight and original PDF/revision state.

## Hosted acceptance after the local build

The subsequent [host acceptance record](host-acceptance.md) supersedes the
pending-build status below: the first hosted Build run passed on Ubuntu 22.04,
Windows and Apple Silicon macOS, including copied-bundle OCR and platform package
checks. It records the per-platform sizes and the separate installed-OCR checks.

## Original release limits at local implementation

The local executable is a Fedora-host build. It does not establish compatibility
with the Ubuntu 22.04 release baseline. The Build workflow compiles the OCR engine
on Ubuntu 22.04, Windows and Apple Silicon macOS and runs the copied-bundle OCR
check on each host; those remote builds have not been executed for this change.
Windows/macOS sizes, installed-app behavior, Windows DLL/Unicode behavior and
Finder launches therefore remain host acceptance work. No new release has been
published.
