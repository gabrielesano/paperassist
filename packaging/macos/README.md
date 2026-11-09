# macOS Apple Silicon package

PaperAssist is distributed as `PaperAssist-<version>-macos-arm64.dmg`, containing
`PaperAssist.app` and an Applications shortcut. Open the disk image, drag the app
to Applications, eject the disk, and launch PaperAssist from Applications. Python
and a Terminal command are not required. Intel Macs are outside this package's
scope.

The current test build uses an ad-hoc application signature, verified during
packaging and again after copying out of the mounted image. It is not Developer
ID signed or notarized. Downloaded applications can therefore be blocked by
Gatekeeper. A normal trusted distribution needs Developer ID signing and Apple's
notarization service; creating the DMG does not supply either of those.

## Build on Apple Silicon

From the repository root, with the native dependencies, PyInstaller, CMake and
the Xcode command-line C/C++ tools installed:

```sh
python packaging/desktop/generate_icons.py --output-dir build/icons --icns build/icons/PaperAssist.icns
python packaging/ocr/build_ocr.py
pyinstaller packaging/paperassist.spec --noconfirm --distpath dist --workpath build
python packaging/macos/build_dmg.py
python -m unittest tests.test_macos_bundle -v
```

The app includes the native Cocoa executable, render worker, Python, Qt, PDFium,
license notices, and build provenance. The app identifier is
`org.paperassist.PaperAssist`. Finder's short version is numeric; the full alpha
version is recorded in the app information and About dialog. GitHub's workflow
run number supplies the bundle build number.

The Build workflow creates crisp 16–1024 pixel icon representations, builds only
the arm64 app, creates a compressed read-only disk image, and emits a detached
SHA-256 file. The `PaperAssist-macOS` workflow artifact contains the DMG, checksum,
notices, and provenance.

## Verification and limits

The macOS acceptance tests check application identity, arm64 architecture,
resources and signature. They mount the DMG read-only, copy the app to a path
with spaces, detach the image, verify the copied signature, and render a PDF
through the copied executable in an isolated home outside the checkout.

The copied app is also launched through LaunchServices with a native Cocoa
window. The test checks window ownership and dimensions through CoreGraphics,
and confirms that the renderer runs from the copied frozen executable. Both a
normal app launch and cold/warm Finder-style file events are exercised, along
with second-process CLI delivery to the existing owner. PDF imports are deduplicated
managed copies, and the returned LaunchServices PID must identify the same owner. SIGTERM must
close the app and leave the source PDF, stored original, and unsaved-revision
directory unchanged. The catalog gains only the expected managed imports;
reading positions may be persisted. These checks do not automate every editing
workflow or establish compatibility with older macOS versions.

Updating means quitting PaperAssist and replacing its app in Applications.
Removing the app does not remove research data. The default library is
`~/Library/Application Support/PaperAssist`; preferences are in
`~/Library/Preferences/PaperAssist/config.json`. A library selected elsewhere stays
at its selected location. The package does not register itself as the default PDF
viewer. Its PDF document type uses Viewer role and Alternate rank; choose a
default through Finder yourself. File events are buffered until the library
window can receive them.

References: [PyInstaller macOS bundles](https://pyinstaller.org/en/stable/spec-files.html#spec-file-options-for-a-macos-bundle),
[Apple software distribution](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution),
[Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
