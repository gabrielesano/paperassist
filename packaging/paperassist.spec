# PyInstaller spec for the native reader (one-dir). Build from the repository root:
#   pyinstaller packaging/paperassist.spec --noconfirm --distpath dist --workpath build
# See packaging/README.md.
from pathlib import Path

root = Path(SPECPATH).parent

# A Widgets reader that draws its own pages needs none of the following. PyInstaller's
# Qt hooks collect every plugin of the modules they see, so the unwanted files are
# filtered out of the analysis result here instead of being deleted from the bundle.
EXCLUDED_MODULES = ["PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick"]
EXCLUDED_PLUGIN_DIRS = {"imageformats", "iconengines", "tls", "generic", "networkinformation", "egldeviceintegrations"}
KEPT_PLATFORMS = ("xcb", "wayland", "windows", "cocoa", "offscreen")  # offscreen serves tests and --self-test
EXCLUDED_PLUGINS = ("libqgtk3",)  # the GTK theme drags in the GTK stack; the portal theme stays
EXCLUDED_QT_LIBRARIES = ("Qt6Network", "Qt6EglFS", "Qt6EglFs", "QtNetwork")  # Svg and OpenGL stay: the Wayland plugins link them
# Linux system libraries that only libqgtk3, the GTK theme plugin, needed: GTK itself, its
# drawing and accessibility stack, Kerberos and the X extensions beyond what Qt uses.
EXCLUDED_SYSTEM_LIBRARIES = (
    "libgtk-3", "libgdk", "libatk", "libatspi", "libcairo", "libpango", "libcloudproviders", "libtinysparql",
    "libglycin", "libjson-glib", "libepoxy", "libthai", "libdatrie", "libpixman", "libfribidi", "liblcms2",
    "libgio", "libgobject", "libgmodule", "libmount", "libblkid", "libselinux", "libseccomp", "libsqlite3",
    "libkrb5", "libgssapi", "libk5crypto", "libkeyutils", "libcom_err",
    "libXcomposite", "libXcursor", "libXdamage", "libXfixes", "libXi.", "libXinerama", "libXrandr", "libXrender",
)


def unwanted(entry):
    dest = entry[0].replace("\\", "/")
    parts = dest.split("/")
    name = parts[-1]
    if "PySide6" in parts and "translations" in parts:
        return True
    if "plugins" in parts:
        folder = parts[parts.index("plugins") + 1]
        if folder == "platforms":
            return not any(kept in name for kept in KEPT_PLATFORMS)
        return folder in EXCLUDED_PLUGIN_DIRS or name.startswith(EXCLUDED_PLUGINS)
    if name.removeprefix("lib").startswith(EXCLUDED_QT_LIBRARIES):
        return True
    return len(parts) == 1 and name.startswith(EXCLUDED_SYSTEM_LIBRARIES)


a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root / "src")],
    hiddenimports=["paperassist.native.worker"],  # imported by the render process, not by the UI start
    excludes=EXCLUDED_MODULES,
)
a.binaries = [entry for entry in a.binaries if not unwanted(entry)]
a.datas = [entry for entry in a.datas if not unwanted(entry)]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PaperAssist", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="PaperAssist")
