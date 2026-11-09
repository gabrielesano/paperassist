# Windows single-file installer

The `PaperAssist-Windows` Build artifact contains
`PaperAssist-<version>-windows-x86_64-setup.exe` and its SHA-256 file. Run the EXE
and follow the setup wizard. Python is included; no development environment or
manual runtime archive extraction is needed.

The installer uses Inno Setup 6.3+ in per-user mode, without requesting
administrator privileges. The default application folder is
`%LOCALAPPDATA%\Programs\PaperAssist`. A Start menu shortcut is created, and a
desktop shortcut is optional. The installed reader is a windowed application and
does not create a console. Windows Settings lists PaperAssist as an installed app
with an uninstaller.

## Data and updates

The application folder is separate from the default research library
(`%APPDATA%\PaperAssist`) and preferences
(`%LOCALAPPDATA%\PaperAssist\config.json`). A chosen library elsewhere stays at
its chosen location. Setup refuses library targets and nonempty unmanaged
folders. Uninstallation removes only recorded installed files; additional files
are retained. No recursive deletion of libraries, originals, working revisions,
or preferences is configured.

To update, close PaperAssist and run the newer installer. A stable application ID
keeps the install location and uninstall entry consistent. Setup and uninstall
refuse to proceed while a native reader process holds the application mutex;
they do not force-close unsaved documents. Inno Setup's normal file replacement
and rollback mechanisms handle installation. Obsolete files from earlier builds
may remain until uninstallation; upgrades do not recursively purge `_internal`.

The alpha installer is not Authenticode-signed. Reputation and publisher trust
on downloaded installers are not established by a successful CI run. The optional
**Add PaperAssist to Open with for PDF files** task registers a current-user PDF
handler and capabilities. Setup never replaces the default `.pdf` handler or
UserChoice; uninstallation removes the owned registrations. Subsequent launches
deliver PDFs to the existing owner of the selected library, importing managed
copies and preserving unsaved edits and external originals.

## Building and verification

On Windows x64, install the native dependencies, PyInstaller, Inno Setup 6.3+,
CMake and the Visual Studio C/C++ build tools.
Then run from the checkout root:

```powershell
python packaging/desktop/generate_icons.py --output-dir build/icons --ico build/icons/PaperAssist.ico
python packaging/ocr/build_ocr.py
pyinstaller packaging/paperassist.spec --noconfirm --distpath dist --workpath build
python packaging/windows/build_installer.py
python -m unittest tests.test_windows_installer -v
```

The tests require a disposable Windows account without an existing PaperAssist
installation or shortcuts; otherwise they skip to protect the existing setup.
They check target rejection, install and repeated upgrade in a path with spaces,
the current-user uninstall registry entry, shortcut targets, a console-free PE
executable, PDF rendering, a real visible GUI, and the installed render-worker
path. Setup and uninstall must refuse the running GUI. A normal window close is
followed by upgrade and uninstall. Tests also execute the registered PDF command,
check duplicate reuse in the existing process, preserve source originals and
manual metadata, and verify registry cleanup without changing PDF defaults.
Reading positions and explicitly imported papers are expected library changes;
upgrade and uninstall preserve that resulting library, preferences and an unowned
file inside the application directory byte for byte.

The source checkout tests skip this acceptance suite until an installer exists.
The Build workflow runs it against the actual installer before uploading the EXE,
checksum, notices, and application provenance. The installer also carries Inno
Setup's license in `INNO-SETUP-LICENSE.txt`.

References: [Inno Setup per-user mode](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm),
[application mutex](https://jrsoftware.org/ishelp/topic_setup_appmutex.htm),
[uninstall log behavior](https://jrsoftware.org/ishelp/topic_appendnotes.htm).
