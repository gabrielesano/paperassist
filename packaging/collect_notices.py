#!/usr/bin/env python3
"""Collect and format third-party license notices for PaperAssist standalone distribution.

Invoked during standalone packaging (or standalone CLI) to produce THIRD-PARTY-NOTICES.txt.
"""
import importlib.util
import io
import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from paperassist import __version__

PACKAGING_DIR = Path(__file__).resolve().parent
ROOT = PACKAGING_DIR.parent
LICENSES_DIR = PACKAGING_DIR / "licenses"


def _load_license(filename: str) -> str:
    return (LICENSES_DIR / filename).read_text(encoding="utf-8").strip()


def _load_audit_glibc():
    spec = importlib.util.spec_from_file_location("paperassist_audit_glibc", PACKAGING_DIR / "audit_glibc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get_python_license() -> str:
    """Extract Python PSF-2.0 license using the standard library."""
    try:
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            license()
        finally:
            sys.stdout = old_stdout
        out = buf.getvalue().strip()
        if out:
            return out
    except Exception:
        pass
    return "PSF LICENSE AGREEMENT FOR PYTHON (see https://docs.python.org/3/license.html)"


def _get_dist_license(dist_name: str) -> str:
    """Read license text from an installed distribution metadata."""
    try:
        dist = metadata.distribution(dist_name)
    except Exception:
        return ""

    for file in (dist.files or []):
        name = file.name.upper()
        if name in ("LICENSE", "LICENSE.TXT", "COPYING", "COPYING.TXT"):
            try:
                content = dist.read_text(str(file))
                if content:
                    return content.strip()
            except Exception:
                continue
    return ""


def _platform_library_details() -> tuple[str, str, str, str]:
    """Return (qt_libs_summary, qt_bundle_dirs, pdfium_lib, pdfium_plat_dir) for current platform."""
    machine = platform.machine().lower()
    is_arm64 = machine in ("arm64", "aarch64")
    if sys.platform.startswith("linux"):
        return (
            "(including libQt6Core, libQt6Gui, libQt6Widgets, libQt6DBus, libQt6OpenGL,\n"
            "libQt6Svg, libQt6WaylandClient, libQt6WlShellIntegration, libQt6XcbQpa,\n"
            "libpyside6, and libshiboken6)",
            "_internal/ and _internal/PySide6/Qt/lib/",
            "libpdfium.so",
            "linux_arm64" if is_arm64 else "linux_x64",
        )
    if sys.platform == "darwin":
        return (
            "(including QtCore, QtGui, QtWidgets, QtNetwork, QtOpenGL, QtSvg,\n"
            "libpyside6, and libshiboken6)",
            "Contents/Frameworks/PySide6/Qt/lib/",
            "libpdfium.dylib",
            "darwin_arm64" if is_arm64 else "darwin_x64",
        )
    return (
        "(including Qt6Core.dll, Qt6Gui.dll, Qt6Widgets.dll, Qt6Network.dll,\n"
        "Qt6OpenGL.dll, Qt6Svg.dll, pyside6, and shiboken6)",
        "_internal/PySide6/",
        "pdfium.dll",
        "win_arm64" if is_arm64 else "win_x64",
    )


def generate_notices(output_path: Path | None = None, *, ocr_runtime: Path | None = None) -> str:
    """Generate the complete THIRD-PARTY-NOTICES.txt content."""
    sections = []
    qt_libs_summary, qt_bundle_dirs, pdfium_lib, pdfium_plat_dir = _platform_library_details()

    # Header
    sections.append(
        "================================================================================\n"
        "PaperAssist Third-Party Software Notices and Licenses\n"
        "================================================================================\n\n"
        "PaperAssist is distributed under the GNU General Public License v3.0 or later\n"
        "(GPL-3.0-or-later). The standalone application bundle contains runtime libraries\n"
        "and components created by third parties, distributed under their respective\n"
        "open-source licenses as documented below.\n"
    )

    # 1. Python / CPython Runtime
    py_ver = sys.version.split()[0]
    sections.append(
        "--------------------------------------------------------------------------------\n"
        f"Python / CPython Runtime ({py_ver})\n"
        "License: Python Software Foundation License Version 2 (PSF-2.0)\n"
        "Website: https://www.python.org/\n"
        "--------------------------------------------------------------------------------\n\n"
        + _get_python_license()
        + "\n"
    )

    # 2. Qt for Python (PySide6 & shiboken6) and Qt 6 Runtime Libraries
    pyside_ver = "unknown"
    for pkg in ("PySide6", "PySide6-Essentials"):
        try:
            pyside_ver = metadata.version(pkg)
            break
        except Exception:
            pass

    sections.append(
        "--------------------------------------------------------------------------------\n"
        f"Qt for Python (PySide6 {pyside_ver}), shiboken6, and Qt 6 Shared Libraries\n"
        "License: GNU Lesser General Public License version 3 (LGPL-3.0-only)\n"
        "Website: https://www.qt.io/ / https://wiki.qt.io/Qt_for_Python\n"
        "--------------------------------------------------------------------------------\n\n"
        "PaperAssist dynamically links to Qt 6 and Qt for Python shared libraries\n"
        f"{qt_libs_summary}.\n\n"
        "In accordance with Section 4 of the GNU Lesser General Public License (LGPL v3):\n"
        "- Users are free to modify or replace these Qt shared libraries located in the\n"
        f"  bundle directory ({qt_bundle_dirs}) and run PaperAssist\n"
        "  with the modified libraries.\n"
        "- Source code for Qt 6 is available from the Qt Project at:\n"
        "    https://code.qt.io/cgit/qt/qtbase.git/\n"
        "- Source code for PySide6 is available at:\n"
        "    https://code.qt.io/cgit/pyside/pyside-setup.git/\n\n"
        + _load_license("LGPL-3.0.txt")
        + "\n"
    )

    # 3. PDFium and pypdfium2
    pdfium2_ver = "unknown"
    try:
        pdfium2_ver = metadata.version("pypdfium2")
    except Exception:
        pass

    pdfium2_lic = _get_dist_license("pypdfium2")
    if not pdfium2_lic:
        pdfium2_lic = (
            "pypdfium2 is licensed under Apache-2.0 or BSD-3-Clause at your option.\n\n"
            + _load_license("Apache-2.0.txt")
            + "\n\n"
            + _load_license("BSD-3-Clause.txt")
        )

    sections.append(
        "--------------------------------------------------------------------------------\n"
        f"PDFium via pypdfium2 ({pdfium2_ver})\n"
        "License: Apache-2.0 / BSD-3-Clause\n"
        "Website: https://pdfium.googlesource.com/pdfium/ / https://github.com/pypdfium2-team/pypdfium2\n"
        "--------------------------------------------------------------------------------\n\n"
        "PDFium is Google's open-source PDF rendering engine. The prebuilt binary\n"
        f"{pdfium_lib} is distributed via pypdfium2 and links/bundles several components.\n"
        "Detailed individual build notices for all PDFium build dependencies (Abseil,\n"
        "AGG, fast_float, FreeType, ICU, LCMS, libjpeg-turbo, libopenjpeg, libpng, simdutf,\n"
        "zlib) are provided in the bundle payload at:\n"
        f"  _internal/pypdfium2-*.dist-info/licenses/data/{pdfium_plat_dir}/BUILD_LICENSES/\n\n"
        + pdfium2_lic
        + "\n"
    )

    # 4. pypdf
    pypdf_ver = "unknown"
    try:
        pypdf_ver = metadata.version("pypdf")
    except Exception:
        pass

    pypdf_lic = _get_dist_license("pypdf")
    if not pypdf_lic:
        pypdf_lic = (
            "Copyright (c) 2006-2008, Mathieu Fenniak\n"
            "Some contributions copyright (c) 2007, Ashish Kulkarni\n"
            "Some contributions copyright (c) 2014, Steve Witham\n\n"
            + _load_license("BSD-3-Clause.txt")
        )

    sections.append(
        "--------------------------------------------------------------------------------\n"
        f"pypdf ({pypdf_ver})\n"
        "License: BSD-3-Clause\n"
        "Website: https://github.com/py-pdf/pypdf\n"
        "--------------------------------------------------------------------------------\n\n"
        + pypdf_lic
        + "\n"
    )

    # 5. Bundled Linux System & C/C++ Shared Libraries
    if sys.platform.startswith("linux"):
        sections.append(_load_license("linux-system-libraries.txt") + "\n")

    if ocr_runtime is not None:
        manifest = json.loads((ocr_runtime / "manifest.json").read_text(encoding="utf-8"))
        for name, license_name, license_id in (
            ("tesseract", "LICENSE.tesseract", "Apache-2.0"),
            ("leptonica", "LICENSE.leptonica", "BSD-2-Clause"),
            ("tessdata_fast", "LICENSE.tessdata_fast", "Apache-2.0"),
        ):
            source = manifest["sources"][name]
            sections.append(
                f"\nBundled OCR: {name} {source['version']}\nLicense: {license_id}\n"
                f"Source: {source['url']}\nSource SHA-256: {source['sha256']}\n\n"
                + (ocr_runtime / license_name).read_text(encoding="utf-8")
            )
    content = "\n".join(sections)
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
    return content


def generate_provenance(bundle_dir: Path, repo_root: Path | None = None) -> dict:
    """Generate BUILD-PROVENANCE.json recording build environment, commit, and ELF audit."""
    if repo_root is None:
        repo_root = ROOT

    bundle_dir = Path(bundle_dir)

    # 1. Git commit & dirty status
    git_commit = "unknown"
    git_dirty = False
    git_dirty_count = 0
    git_dirty_files = []
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            git_commit = out
    except Exception:
        pass

    try:
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
        dirty_lines = [line.strip() for line in status_out if line.strip()]
        git_dirty_count = len(dirty_lines)
        git_dirty = git_dirty_count > 0
        git_dirty_files = dirty_lines
    except Exception:
        pass

    # 2. Host platform & OS release & glibc
    build_platform = platform.platform()

    os_release_data = {}
    for os_release_path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        if os_release_path.is_file():
            try:
                for line in os_release_path.read_text(encoding="utf-8").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        os_release_data[k.strip()] = v.strip().strip("\"'")
                break
            except Exception:
                pass

    os_release = {
        "PRETTY_NAME": os_release_data.get("PRETTY_NAME", platform.system()),
        "ID": os_release_data.get("ID", platform.system().lower()),
        "VERSION_ID": os_release_data.get("VERSION_ID", platform.release()),
    }

    host_glibc = "unknown"
    try:
        host_glibc = os.confstr("CS_GNU_LIBC_VERSION")
    except Exception:
        if sys.platform.startswith("linux"):
            try:
                import ctypes
                libc = ctypes.CDLL(None)
                gnu_get_libc_version = libc.gnu_get_libc_version
                gnu_get_libc_version.restype = ctypes.c_char_p
                host_glibc = f"glibc {gnu_get_libc_version().decode('ascii')}"
            except Exception:
                pass

    # 3. Empirical ELF audit across bundle (Linux only; single pass via audit_glibc)
    elf_info = {
        "bootloader_max_glibc": None,
        "bundle_max_glibc": None,
        "bundle_max_glibc_files": [],
        "bundle_max_glibcxx": None,
        "bundle_max_cxxabi": None,
        "external_shared_libraries": [],
    }
    if sys.platform.startswith("linux") and bundle_dir.is_dir():
        elf_info = _load_audit_glibc().inspect_bundle_elfs(bundle_dir)

    provenance = {
        "version": __version__,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "git_dirty_count": git_dirty_count,
        "git_dirty_files": git_dirty_files,
        "build_platform": build_platform,
        "os_release": os_release,
        "host_glibc": host_glibc,
        "python_version": sys.version.split()[0],
        **elf_info,
    }

    prov_json = json.dumps(provenance, indent=2) + "\n"
    if bundle_dir.is_dir():
        (bundle_dir / "BUILD-PROVENANCE.json").write_text(prov_json, encoding="utf-8")
        internal_dir = bundle_dir / "_internal"
        if internal_dir.is_dir():
            (internal_dir / "BUILD-PROVENANCE.json").write_text(prov_json, encoding="utf-8")

    return provenance


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate third-party notices and build provenance")
    parser.add_argument("target", nargs="?", default=None, help="Target path for notices")
    parser.add_argument("--provenance-bundle", type=Path, default=None, help="Bundle directory to inspect and generate BUILD-PROVENANCE.json for")
    args = parser.parse_args()

    if args.provenance_bundle:
        prov = generate_provenance(args.provenance_bundle, ROOT)
        print(f"Generated BUILD-PROVENANCE.json for {args.provenance_bundle} (bundle max glibc: {prov.get('bundle_max_glibc')})")

    target = Path(args.target) if args.target else ROOT / "THIRD-PARTY-NOTICES.txt"
    generate_notices(target)
    print(f"Generated notices in {target}")
