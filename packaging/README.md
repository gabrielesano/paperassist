# Packaging the native reader

`paperassist.spec` builds a one-dir PyInstaller bundle of `python -m paperassist.native`
(`entry.py` is its entry point). From the repository root, in an environment with the
`native` extra:

```bash
python -m pip install ".[native]" "pyinstaller>=6.15,<7"
pyinstaller packaging/paperassist.spec --noconfirm --distpath dist --workpath build
dist/PaperAssist/PaperAssist --self-test some.pdf   # PaperAssist.exe on Windows; exit code 0 = ok
```

`--self-test FILE.pdf` opens the file, draws page 1 through the render process with Qt's
offscreen platform and checks that the page is not blank, so it needs no display. The
manual workflow `.github/workflows/build.yml` does exactly this on Linux, Windows and
macOS and uploads the bundle.

What the spec leaves out (a Widgets reader that draws its own pages needs none of it):
Qt QML, Quick and Network, Qt translations, the plugins `imageformats`, `iconengines`,
`tls`, `generic`, `networkinformation` and `egldeviceintegrations`, the GTK theme plugin
with the GTK libraries it pulls in, and every platform plugin except xcb, wayland,
windows, cocoa and offscreen. The filter lives in the spec because PyInstaller's Qt
hooks collect all plugins of the modules they find. On Linux it takes the bundle from
213 MB to about 173 MB installed (about 61 MB as tar + zstd -19).

Notes:

- The render process uses multiprocessing's `spawn` context. `main()` calls
  `multiprocessing.freeze_support()` first and the spec lists `paperassist.native.worker`
  as a hidden import, which is all PyInstaller needs to start the child on every OS.
- The console is kept (`console=True`) so that the self-test can print. A release build
  for Windows and macOS would set `console=False` and wrap the result in an `.app`
  (macOS) or an installer; neither is done yet, nor signing or notarization.
- Build Linux bundles on the oldest distribution you want to support: PyInstaller copies
  the build machine's system libraries (glibc stays external).
- MuPDF is AGPL-3.0: a distributed bundle must offer the corresponding source.
