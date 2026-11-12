# Hosted release acceptance

This record preserves the earlier bundled-OCR development runs. Current
published packages and acceptance results are in
[the 0.2.0a2 release record](release-0.2.0a2.md).

## Scope

GitHub-hosted runners supply the target operating systems without requiring a
maintainer-owned Windows or Mac device. Build, copied-bundle OCR, installer and
native-window checks are automated. Interactive desktop exploration, hardware
coverage and screen-reader interoperability remain distinct manual follow-up.

The existing `0.1.0a4` release downloads predate bundled OCR. Artifacts from the
runs below are development candidates with the current source version, not a new
release and not replacements for existing release assets. Assign a new version
and rebuild before publishing the next alpha.

## First complete bundled OCR build

[Build run 42661875263](https://github.com/gabrielesano/paperassist/actions/runs/42661875263)
passed all three jobs on commit `ce055a2`, using a clean checkout on each host:

| Host | Verified automatic checks |
| --- | --- |
| Ubuntu 22.04.5, x86_64, Python 3.13.18 | Source OCR build, frozen rendering, copied-bundle OCR, installer integrity/install/render/uninstall, packaging safeguards and 42 frozen/installer/desktop tests |
| Windows Server 2025, x86_64, Python 3.13.17 | Source OCR build, frozen rendering, copied-bundle OCR, actual setup EXE installation, visible GUI and render worker, busy-maintenance rejection, upgrade/uninstall and data preservation |
| macOS 26.7.2, Apple Silicon, Python 3.13.17 | Source OCR build, frozen rendering, copied-bundle OCR, DMG mounting and application copy, ad-hoc signature verification, LaunchServices/native window, frozen worker and clean close |

Copied-bundle OCR passed on all three hosts with an empty `PATH`, a foreign
`TESSDATA_PREFIX`, and spaces/Unicode in the copied path and scanned PDF name.
The input hash was checked and no user library was created. Windows uses exit
status because the windowed executable has no Python console output; Linux and
macOS also assert the recognized title and authors.

The Ubuntu ELF gate inspected 149 files and passed with maximum **GLIBC_2.35**.
Provenance also records **GLIBCXX_3.4.30**. This establishes the build's configured
ELF baseline, not a clean Ubuntu/Debian graphical desktop trial.

Downloaded manifests from that run report these runtime-input sizes (excluding
`manifest.json`; the model, executable, TSV config and licenses are included):

| Platform | Runtime bytes | MiB | Manifest bytes |
| --- | ---: | ---: | ---: |
| Ubuntu x86_64 | 5,623,619 | 5.36 | 2,090 |
| Windows x86_64 | 8,555,907 | 8.16 | 2,151 |
| macOS arm64 | 5,763,835 | 5.50 | 2,090 |

These identify the source-built runtime inputs before PyInstaller collection and
platform signing. They are not installer size deltas. Windows includes the static
MSVC runtime; Linux/macOS collect the required compiler runtimes with the app.

The downloaded candidate artifacts from this specific run are:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PaperAssist-0.1.0a4-linux-x86_64.sh` | 73,025,675 | `1f1ca81313a2ba3ae28027324dff82f67a2af24f2115fc605397898f6eb764bc` |
| `PaperAssist-0.1.0a4-windows-x86_64-setup.exe` | 34,317,574 | `f90f611cd05a67a378dadcb7fa8380ff7a4456a3ef0093c1f1fb5c734df49d4f` |
| `PaperAssist-0.1.0a4-macos-arm64.dmg` | 37,835,412 | `f72f8ada6848f47fdeff5de5ca705255ea69ed8ba6604df343c34990ab44a255` |

Those hashes do not describe the older public/private release assets or later
workflow runs, even when the development filenames share the alpha version.

## Installed-package OCR

Commit `1160a67` extends the checks beyond the copied one-dir bundle:

- Linux invokes OCR through the installed launcher with system engine/model
  lookup disabled, before uninstalling the temporary installation.
- Windows invokes OCR from the actual setup installation, checks the synthetic
  source bytes and verifies the existing library/configuration sentinels remain
  unchanged before continuing GUI, upgrade and uninstall acceptance.
- macOS invokes OCR from the app copied out of the mounted DMG, after signature
  verification, with no system engine/model lookup; it checks the recovered title
  and original scan bytes before the LaunchServices checks.

[Build run 42663515822](https://github.com/gabrielesano/paperassist/actions/runs/42663515822)
passed all three jobs on `1160a67`, including these installed-package OCR checks.
The Linux installed launcher recovered 86 words and the expected title/authors;
the Ubuntu gate again inspected 149 ELF files with maximum GLIBC_2.35. Windows
installation/GUI/upgrade/uninstall and macOS mounted-DMG/copy/launch/close checks
also passed with the OCR commands included. Its development artifacts supersede
the first run for testing; the earlier artifact hashes above remain historical.

The Ubuntu-built installer from the first run was also installed locally into a
private temporary home on Fedora 43. Its installed launcher recovered the title
and both authors with engine/model lookup disabled, preserved the original PDF,
created no library and uninstalled successfully. This is additional execution of
the Ubuntu artifact on Fedora, not an Ubuntu desktop trial.

## Native keyboard and desktop test corrections

The previous macOS source suite used Qt's offscreen backend. Its logs exposed
platform-dependent Enter activation and a shortcut-help assertion that expected
literal `Ctrl+0` instead of native `Cmd` notation.

Commit `604ab64` explicitly handles Return and keypad Enter for Library selection
and reference-card expansion, with one activation per key event. Empty or
multiple Library selections still open nothing. The help assertion reads Qt's
native key notation, and the macOS source workflow uses Cocoa to test actual
activation and popup focus.

The Cocoa run then exposed assumptions about the available screen size and
native control fonts. Commit `cfa2e02` keeps the behavioral assertions while using
actual reader space for side-by-side tests, the configured application-font
baseline for interface zoom, and the pane's minimum pixel height for splitter
bounds. Preference persistence is tested independently of a layout button that
is correctly disabled when the reader is narrow. The five adjusted regressions
passed locally; exact font/size restoration tests remain unchanged.

[macOS run 42664263077](https://github.com/gabrielesano/paperassist/actions/runs/42664263077)
passed on `cfa2e02` with Cocoa: 815 tests in 122.305 seconds, 80 skips. The source
suite's unavailable-dependency and platform-specific skips remain explicit; the
separate Build workflow verifies the compiled private OCR runtime.
[CI run 42664163422](https://github.com/gabrielesano/paperassist/actions/runs/42664163422)
passed the four Ubuntu/Windows jobs on Python 3.11 and 3.13 at the same commit.

## Manual follow-up

- Clean Ubuntu/Debian desktop trials, including the installed GUI, dialogs,
  clipboard and the supported X11/Wayland environments.
- Interactive Windows/macOS reading and editing across window sizes, display
  scaling, native focus and everyday upgrade paths; older OS versions and other
  architectures remain unverified.
- Signing for broader distribution: Authenticode on Windows and Developer ID /
  notarization on macOS. Current ad-hoc signing tests do not establish Gatekeeper
  or SmartScreen trust for downloaded releases.
- OS PDF associations wait for verified native file delivery and managed-library
  ownership behavior; they are not enabled by passing build checks.
