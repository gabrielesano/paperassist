# PyInstaller spec for the native reader (one-dir). Build from the repository root:
#   pyinstaller packaging/paperassist.spec --noconfirm --distpath dist --workpath build
# See packaging/README.md.
import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata
from paperassist import __version__

root = Path(SPECPATH).parent

# Refuse an incomplete or stale OCR runtime instead of publishing a partial app.
ocr_spec = importlib.util.spec_from_file_location('paperassist_build_ocr', root / 'packaging' / 'ocr' / 'build_ocr.py')
ocr_mod = importlib.util.module_from_spec(ocr_spec)
ocr_spec.loader.exec_module(ocr_mod)
ocr_root = root / 'build' / 'ocr' / 'runtime'
if not (ocr_root / 'manifest.json').is_file():
    raise RuntimeError('Build bundled OCR first: python packaging/ocr/build_ocr.py')
ocr_manifest = ocr_mod.verify_runtime(ocr_root)
ocr_executable = 'tesseract.exe' if sys.platform == 'win32' else 'tesseract'
ocr_data = [(str(ocr_root / name), str(Path('ocr') / Path(name).parent))
            for name in [*ocr_manifest['files'], 'manifest.json'] if name != ocr_executable]

# Load local collect_notices helper avoiding name collision with PyPA 'packaging'
notices_spec = importlib.util.spec_from_file_location(
    "paperassist_collect_notices", str(root / "packaging" / "collect_notices.py")
)
notices_mod = importlib.util.module_from_spec(notices_spec)
notices_spec.loader.exec_module(notices_mod)
generate_notices = notices_mod.generate_notices
generate_provenance = getattr(notices_mod, "generate_provenance", None)

# Generate THIRD-PARTY-NOTICES.txt deterministically into build directory
build_dir = root / "build" / "notices"
build_dir.mkdir(parents=True, exist_ok=True)
notices_path = build_dir / "THIRD-PARTY-NOTICES.txt"
generate_notices(notices_path, ocr_runtime=ocr_root)

# A Widgets reader that draws its own pages needs none of the following. PyInstaller's
# Qt hooks collect every plugin of the modules they see, so the unwanted files are
# filtered out of the analysis result here instead of being deleted from the bundle.
EXCLUDED_MODULES = ["PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick"]
EXCLUDED_PLUGIN_DIRS = {
    "imageformats", "iconengines", "tls", "generic", "networkinformation",
    "egldeviceintegrations", "designer", "qmltooling", "sqldrivers",
}
KEPT_PLATFORMS = ("xcb", "wayland", "windows", "cocoa", "offscreen")  # offscreen serves tests and --self-test
EXCLUDED_PLUGINS = ("libqgtk3",)  # the GTK theme drags in the GTK stack; the portal theme stays
EXCLUDED_QT_LIBRARIES = ("Qt6Network", "Qt6EglFS", "Qt6EglFs", "QtNetwork")  # Svg and OpenGL stay: the Wayland plugins link them

# Linux system libraries that only libqgtk3, the GTK theme plugin, needed: GTK itself, its
# drawing and accessibility stack, Kerberos and the X extensions beyond what Qt uses.
# Note: libsqlite3 is intentionally omitted from this exclusion list so Python's _sqlite3
# extension finds libsqlite3.so inside _internal, ensuring standalone execution without
# depending on host sqlite3 installations.
EXCLUDED_SYSTEM_LIBRARIES = (
    "libgtk-3", "libgdk", "libatk", "libatspi", "libcairo", "libpango", "libcloudproviders", "libtinysparql",
    "libglycin", "libjson-glib", "libepoxy", "libthai", "libdatrie", "libpixman", "libfribidi", "liblcms2",
    "libgio", "libgobject", "libgmodule", "libmount", "libblkid", "libselinux", "libseccomp",
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


def safe_copy_metadata(package_name):
    try:
        return copy_metadata(package_name)
    except Exception:
        return []


metadata_datas = (
    safe_copy_metadata("paperassist")
    + safe_copy_metadata("PySide6")
    + safe_copy_metadata("PySide6-Essentials")
    + safe_copy_metadata("PySide6_Essentials")
    + safe_copy_metadata("shiboken6")
    + safe_copy_metadata("pypdfium2")
    + safe_copy_metadata("pypdf")
)

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root / "src")],
    hiddenimports=["paperassist.native.worker", "paperassist.ocr"],
    excludes=EXCLUDED_MODULES,
    binaries=collect_dynamic_libs("pypdfium2_raw") + [(str(ocr_root / ocr_executable), 'ocr')],
    datas=collect_data_files("pypdfium2_raw")
          + metadata_datas
          + [(str(root / "LICENSE"), ".")]
          + [(str(notices_path), ".")]
          + ocr_data,
)
a.binaries = [entry for entry in a.binaries if not unwanted(entry)]
a.datas = [entry for entry in a.datas if not unwanted(entry)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="PaperAssist",
    console=sys.platform not in ("darwin", "win32"),
    icon=str(root / "build" / "icons" / "PaperAssist.ico") if sys.platform == "win32" else None,
    target_arch="arm64" if sys.platform == "darwin" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PaperAssist")

# Ensure top-level license, notice, and build provenance files exist at root of the bundle (dist/PaperAssist/)
bundle_dir = Path(coll.name)
if bundle_dir.is_dir():
    shutil.copy2(root / "LICENSE", bundle_dir / "LICENSE")
    shutil.copy2(notices_path, bundle_dir / "THIRD-PARTY-NOTICES.txt")
    if generate_provenance:
        generate_provenance(bundle_dir, root)

if sys.platform == "darwin":
    # Include provenance before BUNDLE seals its resources with an ad-hoc signature.
    app = BUNDLE(
        coll,
        [("BUILD-PROVENANCE.json", str(bundle_dir / "BUILD-PROVENANCE.json"), "DATA")],
        name="PaperAssist.app",
        icon=str(root / "build" / "icons" / "PaperAssist.icns"),
        bundle_identifier="org.paperassist.PaperAssist",
        version=re.match(r"\d+\.\d+\.\d+", __version__).group(),
        info_plist={
            "CFBundleVersion": os.environ.get("GITHUB_RUN_NUMBER", "1"),
            "CFBundleGetInfoString": f"PaperAssist {__version__}",
            "LSApplicationCategoryType": "public.app-category.productivity",
        },
    )
