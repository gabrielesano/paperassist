"""Current Linux x86_64 alpha installation and GUI acceptance test suite.

This suite is strictly partitioned into two distinct test classes:
1. TestInstalledFrozenExecutableAcceptance:
   Genuine black-box acceptance tests driving ONLY the real installed frozen binary
   (~/.local/bin/paperassist -> ~/.local/share/paperassist-app/PaperAssist) installed from
   the current dist/PaperAssist-<version>-linux-x86_64.sh into disposable directories.
   All interactions are driven externally via the Linux OS, procfs, X11 (Xvfb, xdotool, ctypes libX11),
   and Wayland (Mutter/Weston). Under no circumstances are MainWindow or RenderClient instantiated
   in this test class.

2. TestSourceLevelWidgetRegressions:
   Supplementary source-level (.venv) widget unit and regression tests exercising in-process
   PySide6 widgets and internal RenderClient latency/cache budgets. These are explicitly labeled
   as source-process regressions and never attributed to the frozen executable.
"""

import ctypes
import ctypes.util
import hashlib
import io
import json
import os
import re
import shutil
import shlex
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from paperassist import __version__

# Add installer directory to sys.path so synthetic packages can be built
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packaging" / "installer"))
from build_installer import build_installer

REAL_INSTALLER = REPO_ROOT / "dist" / f"PaperAssist-{__version__}-linux-x86_64.sh"
REAL_CHECKSUM = REAL_INSTALLER.with_name(REAL_INSTALLER.name + ".sha256")
SAMPLE_PDF = REPO_ROOT / "tests" / "fixtures" / "pdfjs-annotations.pdf"

try:
    from test_native_support import needs_qt, PySide6, pdf_available
except ModuleNotFoundError:
    from tests.test_native_support import needs_qt, PySide6, pdf_available

if pdf_available:
    from pypdf import PdfWriter, PdfReader
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, TextStringObject

if pdf_available and PySide6:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QListView
    from paperassist import references
    from paperassist.native.engine import Pdf, Rect
    from paperassist.native.positions import current_page
    from paperassist.native.renderer import RenderClient
    from paperassist.native.rules import CANCEL, DISCARD, SAVE
    from paperassist.native.window import MainWindow
    from paperassist.workspace import Workspace


# ---------------------------------------------------------------------------
# libX11 ctypes bindings for pixel inspection under X11 / Xvfb
# ---------------------------------------------------------------------------
_libx11_path = ctypes.util.find_library("X11") or "/usr/lib64/libX11.so.6"
try:
    _libx11 = ctypes.cdll.LoadLibrary(_libx11_path)
    _libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    _libx11.XOpenDisplay.restype = ctypes.c_void_p
    _libx11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    _libx11.XCloseDisplay.restype = ctypes.c_int
    _libx11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    _libx11.XDefaultRootWindow.restype = ctypes.c_ulong
    _libx11.XGetImage.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int
    ]
    _libx11.XGetImage.restype = ctypes.c_void_p
    _libx11.XGetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    _libx11.XGetPixel.restype = ctypes.c_ulong
    _libx11.XDestroyImage.argtypes = [ctypes.c_void_p]
    _libx11.XDestroyImage.restype = ctypes.c_int
    _X11_AVAILABLE = True
except (OSError, AttributeError):
    _libx11 = None
    _X11_AVAILABLE = False


def read_smaps_rollup(pid: int) -> dict[str, int]:
    """Read Linux kernel smaps_rollup to obtain RSS, PSS, and dirty pages in kB."""
    result: dict[str, int] = {}
    path = Path(f"/proc/{pid}/smaps_rollup")
    if not path.is_file():
        return result
    try:
        for line in path.read_text(encoding="ascii", errors="replace").splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val_parts = parts[1].strip().split()
                if val_parts and val_parts[0].isdigit():
                    result[key] = int(val_parts[0])
    except OSError:
        pass
    return result


def read_child_pids(pid: int) -> list[int]:
    """Return all child PIDs belonging to the given PID via procfs."""
    try:
        with open(f"/proc/{pid}/task/{pid}/children", "r", encoding="ascii") as fp:
            return [int(token) for token in fp.read().split()]
    except OSError:
        pass
    return []


def record_tree_hashes(folder: Path) -> dict[str, str]:
    """Record SHA-256 digests of all files in a folder keyed by relative path."""
    hashes: dict[str, str] = {}
    for p in folder.rglob("*"):
        if p.is_file():
            hashes[str(p.relative_to(folder))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def create_scientific_citation_pdf(path: Path, pages: int = 4) -> tuple[Path, bytes]:
    """Create a scientific PDF with body text citing [1] and [2] and standard bibliography links.

    Uses generous line spacing (40 pt) so citation link bounding boxes strictly enclose only
    their respective citation text ('[1]' or '[2]') without capturing adjacent line words.
    """
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Courier"),
    }))
    for number in range(pages):
        lines = (
            [
                f"Page {number + 1}. Research investigation and findings.",
                "Previous work by Smith et al. [1] and Jones et al. [2] examined similar properties.",
                "This study provides quantitative evaluation and comparative benchmarks.",
            ]
            if number < pages - 1
            else [
                "References",
                "[1] Smith, J. 2020, Nature Physics, 10, 100",
                "[2] Jones, A. 2021, Physical Review B, 15, 200",
            ]
        )
        page = writer.add_blank_page(612, 792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        spacing = 40 if number < pages - 1 else 20
        stream.set_data((f"BT /F1 12 Tf 50 700 Td {spacing} TL " + " T* ".join(f"({line}) Tj" for line in lines) + " ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    raw_io = io.BytesIO()
    writer.write(raw_io)
    raw_bytes = raw_io.getvalue()

    # Discover coordinates of [1] and [2] on page 0
    pdf = Pdf(raw_bytes)
    words = [w for w in pdf.words(0) if w[4].rstrip(".") in ("[1]", "[2]")]
    pdf.close()

    annot_writer = PdfWriter()
    annot_writer.append(io.BytesIO(raw_bytes))
    annot_writer.add_named_destination("cite.1", pages - 1)
    annot_writer.add_named_destination("cite.2", pages - 1)

    for x0, y0, x1, y1, text, _ in words:
        clean_key = text.rstrip(".")
        dest_name = "cite.1" if clean_key == "[1]" else "cite.2"
        annot_writer.add_annotation(0, DictionaryObject({
            NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (x0 - 2, 792 - y1 - 2, x1 + 2, 792 - y0 + 2)),
            NameObject("/Dest"): TextStringObject(dest_name),
        }))

    final_io = io.BytesIO()
    annot_writer.write(final_io)
    final_bytes = final_io.getvalue()
    path.write_bytes(final_bytes)
    return path, final_bytes


# ===========================================================================
# 1. Genuine Installed Frozen Executable Acceptance (Black-Box external X11/Wayland)
# ===========================================================================
@unittest.skipUnless(sys.platform.startswith("linux"), "Installed Linux acceptance requires Linux")
@unittest.skipUnless(REAL_INSTALLER.is_file(), "Current Linux installer is not available")
class TestInstalledFrozenExecutableAcceptance(unittest.TestCase):
    """Genuine black-box acceptance tests executing the actual installed frozen binary.

    All tests in this class execute ~/.local/bin/paperassist (which runs
    ~/.local/share/paperassist-app/PaperAssist) installed from the current installer.
    The test runner process unsets PYTHONPATH/PYTHONHOME and sanitizes PATH so no repository
    or virtual environment sources can be imported by the application.
    """

    xvfb_proc = None
    xvfb_display = None
    x11_handle = None

    @classmethod
    def setUpClass(cls):
        """Start a dedicated headless Xvfb display server for external X11 automation."""
        if not shutil.which("Xvfb") or not shutil.which("xdotool"):
            raise unittest.SkipTest("Xvfb and xdotool are required for installed frozen executable acceptance")

        # Pick an unused display number between :130 and :190
        cls.xvfb_display = None
        for d in range(130, 190):
            if not Path(f"/tmp/.X11-unix/X{d}").exists():
                cls.xvfb_display = f":{d}"
                break
        if cls.xvfb_display is None:
            cls.xvfb_display = ":199"

        cls.xvfb_proc = subprocess.Popen(
            ["Xvfb", cls.xvfb_display, "-screen", "0", "1400x900x24", "-ac", "+extension", "GLX", "+render", "-noreset"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
        if cls.xvfb_proc.poll() is not None:
            raise RuntimeError(f"Failed to start Xvfb on {cls.xvfb_display}")

        if _X11_AVAILABLE:
            cls.x11_handle = _libx11.XOpenDisplay(cls.xvfb_display.encode("ascii"))

    @classmethod
    def tearDownClass(cls):
        """Cleanly terminate Xvfb server and close libX11 display handle."""
        if cls.x11_handle and _X11_AVAILABLE:
            _libx11.XCloseDisplay(cls.x11_handle)
            cls.x11_handle = None
        if cls.xvfb_proc:
            cls.xvfb_proc.terminate()
            try:
                cls.xvfb_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cls.xvfb_proc.kill()
            cls.xvfb_proc = None

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

        self.clean_home = self.tmp_dir / "home"
        self.clean_home.mkdir(parents=True)
        self.env = {
            "HOME": str(self.clean_home),
            "PATH": "/usr/bin:/bin",
            "DISPLAY": self.xvfb_display,
            "QT_QPA_PLATFORM": "xcb",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "LC_ALL": "C.UTF-8",
            "TERM": "xterm",
        }
        for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            self.env.pop(k, None)
        subprocess.run(
            ["xdotool", "keyup", "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Super_L", "Super_R"],
            env=self.env,
            capture_output=True,
        )

    def _sample_region_hash(self, x: int, y: int, w: int, h: int, step: int = 10) -> int:
        """Sample pixels from X11 root window via ctypes libX11 and compute a stable hash."""
        if not self.x11_handle or not _X11_AVAILABLE:
            return 0
        root = _libx11.XDefaultRootWindow(self.x11_handle)
        img = _libx11.XGetImage(self.x11_handle, root, x, y, w, h, ~0, 2)
        if not img:
            return 0
        try:
            pixels = [_libx11.XGetPixel(img, i, j) for j in range(0, h, step) for i in range(0, w, step)]
            return hash(tuple(pixels))
        finally:
            _libx11.XDestroyImage(img)

    def _count_dark_pixels(self, x: int, y: int, w: int, h: int, step: int = 4) -> int:
        """Count non-white pixels in a bounding box via ctypes libX11."""
        if not self.x11_handle or not _X11_AVAILABLE:
            return 0
        root = _libx11.XDefaultRootWindow(self.x11_handle)
        img = _libx11.XGetImage(self.x11_handle, root, x, y, w, h, ~0, 2)
        if not img:
            return 0
        try:
            dark = 0
            for j in range(0, h, step):
                for i in range(0, w, step):
                    pix = _libx11.XGetPixel(img, i, j)
                    r = (pix >> 16) & 0xFF
                    g = (pix >> 8) & 0xFF
                    b = pix & 0xFF
                    if (r + g + b) // 3 < 128:
                        dark += 1
            return dark
        finally:
            _libx11.XDestroyImage(img)

    def _find_window_by_title_substring(self, substring: str, timeout: float = 10.0) -> str:
        """Poll xdotool for an active visible X11 window containing substring."""
        deadline = time.monotonic() + timeout
        last_names = []
        while time.monotonic() < deadline:
            res = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", ".*"], env=self.env, capture_output=True, text=True)
            last_names = []
            for wid in res.stdout.strip().split():
                name = subprocess.run(["xdotool", "getwindowname", wid], env=self.env, capture_output=True, text=True).stdout.strip()
                last_names.append((wid, name))
                if substring in name:
                    return wid
            time.sleep(0.2)
        raise TimeoutError(f"Window containing {substring!r} not found within {timeout}s. Current windows: {last_names}")

    def _open_document_from_library_dialog(self, lib_win: str):
        """Click row 0 in LibraryDialog and click Open button, then dismiss acquisition dialog."""
        subprocess.run(["xdotool", "windowfocus", "--sync", lib_win], env=self.env)
        time.sleep(0.2)
        subprocess.run(["xdotool", "mousemove", "--window", lib_win, "122", "139", "click", "1"], env=self.env)
        time.sleep(0.2)
        subprocess.run(["xdotool", "mousemove", "--window", lib_win, "890", "547", "click", "1"], env=self.env)
        time.sleep(1.0)
        try:
            acq_win = self._find_window_by_title_substring("Acquire", timeout=1.0)
            subprocess.run(["xdotool", "windowclose", acq_win], env=self.env)
            time.sleep(0.3)
        except TimeoutError:
            pass

    def test_01_payload_checksum_and_verify_flag(self):
        """Verify sha256sum matches and self-extracting --verify checks integrity."""
        self.assertTrue(REAL_INSTALLER.is_file(), f"Installer missing: {REAL_INSTALLER}")
        self.assertTrue(REAL_CHECKSUM.is_file(), f"Checksum file missing: {REAL_CHECKSUM}")

        inst_copy = self.tmp_dir / REAL_INSTALLER.name
        sha_copy = self.tmp_dir / REAL_CHECKSUM.name
        shutil.copy2(REAL_INSTALLER, inst_copy)
        shutil.copy2(REAL_CHECKSUM, sha_copy)

        res_sha = subprocess.run(["sha256sum", "-c", sha_copy.name], cwd=self.tmp_dir, capture_output=True, text=True)
        self.assertEqual(res_sha.returncode, 0, f"sha256sum verification failed:\n{res_sha.stderr}")
        self.assertIn("OK", res_sha.stdout)

        res_verify = subprocess.run(["sh", str(inst_copy), "--verify"], cwd=self.tmp_dir, capture_output=True, text=True)
        self.assertEqual(res_verify.returncode, 0, f"--verify failed:\n{res_verify.stderr}")
        self.assertIn("Package integrity verified", res_verify.stdout)

    def test_02_clean_environment_installation_standard(self):
        """Install into clean disposable HOME with PATH=/usr/bin:/bin, no .venv or PYTHONPATH."""
        inst_copy = self.tmp_dir / "installer.sh"
        shutil.copy2(REAL_INSTALLER, inst_copy)

        res = subprocess.run(["sh", str(inst_copy)], cwd=self.tmp_dir, env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Installer failed:\n{res.stderr}\n{res.stdout}")
        self.assertIn("installed successfully", res.stdout)

        app_dir = self.clean_home / ".local" / "share" / "paperassist-app"
        self.assertTrue(app_dir.is_dir())
        exe_path = app_dir / "PaperAssist"
        self.assertTrue(exe_path.is_file())
        self.assertTrue(os.access(exe_path, os.X_OK))

        internal_dir = app_dir / "_internal"
        self.assertTrue(internal_dir.is_dir())
        self.assertTrue(bool(list(internal_dir.glob("libsqlite3.so*"))), "Bundled SQLite shared object missing")
        self.assertTrue(bool(list(internal_dir.glob("**/libpdfium.so*"))), "Bundled PDFium shared object missing")

        license_path = app_dir / "LICENSE"
        self.assertTrue(license_path.is_file())
        self.assertIn("General Public License", license_path.read_text(encoding="utf-8"))

        notices_path = app_dir / "THIRD-PARTY-NOTICES.txt"
        self.assertTrue(notices_path.is_file())
        notices_text = notices_path.read_text(encoding="utf-8")
        self.assertIn("Python / CPython Runtime", notices_text)
        self.assertIn("Qt for Python", notices_text)
        self.assertIn("PDFium via pypdfium2", notices_text)

        uninst = app_dir / "uninstall.sh"
        self.assertTrue(uninst.is_file())
        self.assertTrue(os.access(uninst, os.X_OK))

        svg_icon = self.clean_home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "paperassist.svg"
        png_icon = self.clean_home / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "paperassist.png"
        self.assertTrue(svg_icon.is_file())
        self.assertTrue(png_icon.is_file())
        svg_content = svg_icon.read_text(encoding="utf-8")
        self.assertTrue("<svg" in svg_content and svg_content.strip().endswith("</svg>"))
        png_bytes = png_icon.read_bytes()
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        desktop_file = self.clean_home / ".local" / "share" / "applications" / "paperassist.desktop"
        self.assertTrue(desktop_file.is_file())
        if shutil.which("desktop-file-validate"):
            res_val = subprocess.run(["desktop-file-validate", str(desktop_file)], capture_output=True, text=True)
            self.assertEqual(res_val.returncode, 0, f"desktop-file-validate rejected desktop file:\n{res_val.stderr}")

        launcher = self.clean_home / ".local" / "bin" / "paperassist"
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        res_ver = subprocess.run([str(launcher), "--version"], cwd="/tmp", env=self.env, capture_output=True, text=True)
        self.assertEqual(res_ver.returncode, 0, f"Launcher --version failed:\n{res_ver.stderr}")
        self.assertIn(f"PaperAssist {__version__}", res_ver.stdout)

        desktop_lines = desktop_file.read_text(encoding="utf-8").splitlines()
        exec_line = next(line for line in desktop_lines if line.startswith("Exec="))
        raw_exec = exec_line.split("=", 1)[1].strip()
        exec_cmd = shlex.split(raw_exec)[0]
        res_exec = subprocess.run([exec_cmd, "--version"], cwd="/tmp", env=self.env, capture_output=True, text=True)
        self.assertEqual(res_exec.returncode, 0, f"Exec command failed:\n{res_exec.stderr}")
        self.assertIn(f"PaperAssist {__version__}", res_exec.stdout)

    def test_03_clean_environment_installation_spaced_paths(self):
        """Install into custom install-dir and bin-dir containing spaces."""
        spaced_root = self.tmp_dir / "PaperAssist Custom Space"
        spaced_app = spaced_root / "App Bundle Folder"
        spaced_bin = spaced_root / "Bin Folder"
        clean_home = spaced_root / "Home Folder"
        clean_home.mkdir(parents=True)

        env = {
            "HOME": str(clean_home),
            "PATH": "/usr/bin:/bin",
            "TERM": "xterm",
            "QT_QPA_PLATFORM": "offscreen",
        }

        inst_copy = self.tmp_dir / "installer_spaced.sh"
        shutil.copy2(REAL_INSTALLER, inst_copy)

        res = subprocess.run(
            ["sh", str(inst_copy), "--install-dir", str(spaced_app), "--bin-dir", str(spaced_bin)],
            cwd=self.tmp_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Spaced installation failed:\n{res.stderr}\n{res.stdout}")

        launcher = spaced_bin / "paperassist"
        self.assertTrue(launcher.is_file())
        res_ver = subprocess.run([str(launcher), "--version"], cwd="/tmp", env=env, capture_output=True, text=True)
        self.assertEqual(res_ver.returncode, 0)
        self.assertIn(f"PaperAssist {__version__}", res_ver.stdout)

        desktop_file = clean_home / ".local" / "share" / "applications" / "paperassist.desktop"
        self.assertTrue(desktop_file.is_file())
        if shutil.which("desktop-file-validate"):
            res_val = subprocess.run(["desktop-file-validate", str(desktop_file)], capture_output=True, text=True)
            self.assertEqual(res_val.returncode, 0)

    def test_04_startup_and_frozen_renderer_subprocess(self):
        """Verify installed frozen binary self-test and child renderer process resolution."""
        # Install bundle
        shutil.copy2(REAL_INSTALLER, self.tmp_dir / "installer.sh")
        res_inst = subprocess.run(["sh", str(self.tmp_dir / "installer.sh"), "--no-desktop"], env=self.env, capture_output=True)
        self.assertEqual(res_inst.returncode, 0)
        launcher = self.clean_home / ".local" / "bin" / "paperassist"
        app_exe = self.clean_home / ".local" / "share" / "paperassist-app" / "PaperAssist"

        test_pdf = self.tmp_dir / "test_sample.pdf"
        create_scientific_citation_pdf(test_pdf, pages=2)

        # 1. --self-test execution
        res_st = subprocess.run([str(launcher), "--self-test", str(test_pdf)], env=self.env, capture_output=True, text=True)
        self.assertEqual(res_st.returncode, 0, f"Self-test failed:\n{res_st.stderr}\n{res_st.stdout}")
        self.assertIn("self-test ok", res_st.stdout)

        # 2. Real GUI launch under Xvfb
        lib_dir = self.tmp_dir / "proc_lib"
        lib_dir.mkdir()
        gui_proc = subprocess.Popen([str(launcher), "--data-dir", str(lib_dir), str(test_pdf)], env=self.env)
        try:
            time.sleep(2.0)
            self.assertIsNone(gui_proc.poll(), "GUI process exited prematurely")

            # Check GUI process exe
            gui_exe = Path(f"/proc/{gui_proc.pid}/exe").resolve()
            self.assertEqual(gui_exe, app_exe.resolve(), "GUI process exe does not match installed PaperAssist")

            # Find child render worker process
            children = read_child_pids(gui_proc.pid)
            worker_pid = None
            for c in children:
                try:
                    cmdline = Path(f"/proc/{c}/cmdline").read_bytes()
                    if b"multiprocessing-fork" in cmdline:
                        worker_pid = c
                        break
                except OSError:
                    pass

            self.assertIsNotNone(worker_pid, f"Render worker child process not found among: {children}")
            worker_exe = Path(f"/proc/{worker_pid}/exe").resolve()
            self.assertEqual(worker_exe, app_exe.resolve(), "Render worker child process exe does not match installed PaperAssist")
        finally:
            gui_proc.terminate()
            gui_proc.wait(timeout=4)

    def test_05_headless_wayland_smoke_and_clean_exit(self):
        """Exercise installed frozen binary under Mutter or Weston headless Wayland compositor."""
        compositor = None
        if shutil.which("mutter") and shutil.which("dbus-launch"):
            compositor = "mutter"
        elif shutil.which("weston"):
            compositor = "weston"

        if not compositor:
            self.skipTest("No headless Wayland compositor (mutter/weston) found on system")

        shutil.copy2(REAL_INSTALLER, self.tmp_dir / "installer.sh")
        subprocess.run(["sh", str(self.tmp_dir / "installer.sh"), "--no-desktop"], env=self.env, check=True, capture_output=True)
        launcher = self.clean_home / ".local" / "bin" / "paperassist"

        test_pdf = self.tmp_dir / "wayland_test.pdf"
        create_scientific_citation_pdf(test_pdf, pages=2)

        xdg_runtime = tempfile.mkdtemp(prefix="xdg-wayland-")
        os.chmod(xdg_runtime, 0o700)
        self.addCleanup(lambda: shutil.rmtree(xdg_runtime, ignore_errors=True))

        env = {
            "HOME": str(self.clean_home),
            "PATH": "/usr/bin:/bin",
            "XDG_RUNTIME_DIR": xdg_runtime,
            "XDG_CURRENT_DESKTOP": "",
            "NO_AT_BRIDGE": "1",
            "QT_NO_PORTAL": "1",
            "QT_QPA_PLATFORM": "wayland",
            "LC_ALL": "C.UTF-8",
        }

        dbus_proc = subprocess.run(["dbus-launch", "--sh-syntax"], capture_output=True, text=True)
        dbus_env = {}
        for line in dbus_proc.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                dbus_env[k.strip()] = v.strip().rstrip(";").strip("'\"")
        env.update(dbus_env)

        if compositor == "mutter":
            comp_proc = subprocess.Popen(
                ["mutter", "--wayland", "--headless", "--no-x11", "--virtual-monitor", "1024x768"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.2)
            env["WAYLAND_DISPLAY"] = "wayland-0"
        else:
            comp_proc = subprocess.Popen(
                ["weston", "--backend=headless", "--socket=wayland-acc-0"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.8)
            env["WAYLAND_DISPLAY"] = "wayland-acc-0"

        try:
            lib_dir = self.tmp_dir / "wayland_lib"
            lib_dir.mkdir()
            app_proc = subprocess.Popen(
                [str(launcher), "--data-dir", str(lib_dir), str(test_pdf)],
                cwd="/tmp",
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(2.0)
            self.assertIsNone(app_proc.poll(), "App terminated prematurely under Wayland")

            app_proc.send_signal(signal.SIGTERM)
            stdout, stderr = app_proc.communicate(timeout=6)
            self.assertEqual(app_proc.returncode, 0, f"App exited with non-zero under Wayland: {stderr}")
            self.assertNotIn("QWaylandTextInputv3::disableSurface", stderr)
        finally:
            comp_proc.terminate()
            comp_proc.wait(timeout=3)
            if "DBUS_SESSION_BUS_PID" in dbus_env:
                os.kill(int(dbus_env["DBUS_SESSION_BUS_PID"]), signal.SIGKILL)

    def test_06_import_and_reopen_reading_position_persistence(self):
        """Verify installed frozen binary persists page navigation into SQLite and restores it on reopen."""
        shutil.copy2(REAL_INSTALLER, self.tmp_dir / "installer.sh")
        subprocess.run(["sh", str(self.tmp_dir / "installer.sh"), "--no-desktop"], env=self.env, check=True, capture_output=True)
        launcher = self.clean_home / ".local" / "bin" / "paperassist"

        # Prepare library fixture with 3-page document
        lib_dir = self.tmp_dir / "library_pos"
        ws = Workspace(lib_dir)
        pdf_path, pdf_bytes = create_scientific_citation_pdf(self.tmp_dir / "doc.pdf", pages=3)
        doc = ws.library.import_stream(io.BytesIO(pdf_bytes), "test_doc.pdf")
        doc_id = doc["id"]
        ws.close()

        # Launch installed binary
        app_proc = subprocess.Popen([str(launcher), "--data-dir", str(lib_dir)], env=self.env)
        try:
            lib_win = self._find_window_by_title_substring("Library")
            self._open_document_from_library_dialog(lib_win)

            main_win = self._find_window_by_title_substring("— PaperAssist")
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win], env=self.env)
            subprocess.run(["xdotool", "windowmove", main_win, "0", "0"], env=self.env)
            subprocess.run(["xdotool", "windowsize", main_win, "1400", "900"], env=self.env)
            time.sleep(0.5)

            # Navigate to page 2 via keyboard shortcut 'n' (Next page)
            subprocess.run(["xdotool", "key", "--window", main_win, "n"], env=self.env)
            time.sleep(1.0)
        finally:
            app_proc.send_signal(signal.SIGTERM)
            app_proc.wait(timeout=5)

        # Inspect SQLite database directly on disk
        with sqlite3.connect(lib_dir / "library.sqlite3") as conn:
            row = conn.execute("SELECT content FROM document_views WHERE document_id = ?", (doc_id,)).fetchone()
            self.assertIsNotNone(row, "Reading position not saved to SQLite document_views table")
            data = json.loads(row[0])
            self.assertEqual(data.get("page"), 2, f"Expected page 2 in document_views, got {data}")

        # Reopen installed frozen application and verify it restores to saved position
        app_proc2 = subprocess.Popen([str(launcher), "--data-dir", str(lib_dir)], env=self.env)
        try:
            lib_win2 = self._find_window_by_title_substring("Library")
            self._open_document_from_library_dialog(lib_win2)

            main_win2 = self._find_window_by_title_substring("— PaperAssist")
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win2], env=self.env)
            time.sleep(0.8)

            # Assert non-blank pixels rendered in reader
            dark_pixels = self._count_dark_pixels(300, 200, 800, 600)
            self.assertGreater(dark_pixels, 50, "Restored reader rendered blank white page")
        finally:
            app_proc2.send_signal(signal.SIGTERM)
            app_proc2.wait(timeout=5)

    def test_07_annotations_and_explicit_save_no_autosave(self):
        """Verify Draw tool, window dirty indicator, absence of autosave, and byte-for-byte source preservation."""
        shutil.copy2(REAL_INSTALLER, self.tmp_dir / "installer.sh")
        subprocess.run(["sh", str(self.tmp_dir / "installer.sh"), "--no-desktop"], env=self.env, check=True, capture_output=True)
        launcher = self.clean_home / ".local" / "bin" / "paperassist"

        lib_dir = self.tmp_dir / "library_ann"
        ws = Workspace(lib_dir)
        pdf_path, orig_bytes = create_scientific_citation_pdf(self.tmp_dir / "source.pdf", pages=3)
        doc = ws.library.import_stream(io.BytesIO(orig_bytes), "source.pdf")
        doc_id = doc["id"]
        orig_store = ws.library.get_path(doc_id, annotated=False)
        ws.close()

        working_dir = lib_dir / "working"

        # Verify no working revision initially
        self.assertEqual(len(list(working_dir.glob("*.pdf"))), 0)

        app_proc = subprocess.Popen([str(launcher), "--data-dir", str(lib_dir)], env=self.env)
        try:
            lib_win = self._find_window_by_title_substring("Library")
            self._open_document_from_library_dialog(lib_win)

            main_win = self._find_window_by_title_substring("— PaperAssist")
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win], env=self.env)
            subprocess.run(["xdotool", "windowmove", main_win, "0", "0"], env=self.env)
            subprocess.run(["xdotool", "windowsize", main_win, "1400", "900"], env=self.env)
            time.sleep(1.0)

            # Clean window title
            title0 = subprocess.run(["xdotool", "getwindowname", main_win], env=self.env, capture_output=True, text=True).stdout.strip()
            self.assertFalse(title0.startswith("●"), f"Window title started dirty: {title0}")

            # Activate Draw tool (shortcut 'd') and draw an ink stroke
            subprocess.run(["xdotool", "key", "--window", main_win, "d"], env=self.env)
            time.sleep(0.2)
            subprocess.run(["xdotool", "mousemove", "500", "350"], env=self.env)
            subprocess.run(["xdotool", "mousedown", "1"], env=self.env)
            time.sleep(0.05)
            subprocess.run(["xdotool", "mousemove", "550", "400"], env=self.env)
            time.sleep(0.05)
            subprocess.run(["xdotool", "mouseup", "1"], env=self.env)
            time.sleep(0.5)

            # Assert window title now indicates unsaved changes with '●'
            title_dirty = subprocess.run(["xdotool", "getwindowname", main_win], env=self.env, capture_output=True, text=True).stdout.strip()
            self.assertTrue(title_dirty.startswith("●"), f"Window title did not show dirty indicator: {title_dirty}")

            # Wait 1.5 seconds and assert NO autosave on timer
            time.sleep(1.5)
            self.assertEqual(len(list(working_dir.glob("*.pdf"))), 0, "Autosave erroneously created a working file!")
            self.assertEqual(orig_store.read_bytes(), orig_bytes, "Original store altered before explicit save")
            self.assertEqual(pdf_path.read_bytes(), orig_bytes, "Source PDF altered before explicit save")

            # Explicit Save via Ctrl+S
            subprocess.run(["xdotool", "key", "--window", main_win, "ctrl+s"], env=self.env)
            time.sleep(1.2)

            # Assert window title clears '●'
            title_saved = subprocess.run(["xdotool", "getwindowname", main_win], env=self.env, capture_output=True, text=True).stdout.strip()
            self.assertFalse(title_saved.startswith("●"), f"Window title still showed dirty indicator after save: {title_saved}")

            # Assert revision 1 written to working/
            rev1_files = list(working_dir.glob(f"*{doc_id}*"))
            self.assertGreaterEqual(len(rev1_files), 1, "Working revision file missing after Save")
            rev1_bytes = rev1_files[0].read_bytes()

            # Verify PDF annotation exists in revision 1
            reader = PdfReader(io.BytesIO(rev1_bytes))
            annots = reader.pages[0].get("/Annots")
            self.assertIsNotNone(annots, "Saved revision PDF lacks /Annots array")
            self.assertGreater(len(annots), 0, "Saved revision PDF has 0 annotations")

            # Assert byte-for-byte preservation of original files
            self.assertEqual(orig_store.read_bytes(), orig_bytes, "Original store byte content altered!")
            self.assertEqual(pdf_path.read_bytes(), orig_bytes, "Source PDF byte content altered!")
        finally:
            app_proc.send_signal(signal.SIGTERM)
            app_proc.wait(timeout=5)

    def test_08_save_discard_cancel_close_prompt(self):
        """Verify modal close prompt decision rules (Cancel, Discard, Save) on installed frozen executable."""
        shutil.copy2(REAL_INSTALLER, self.tmp_dir / "installer.sh")
        subprocess.run(["sh", str(self.tmp_dir / "installer.sh"), "--no-desktop"], env=self.env, check=True, capture_output=True)
        launcher = self.clean_home / ".local" / "bin" / "paperassist"

        lib_dir = self.tmp_dir / "library_dec"
        ws = Workspace(lib_dir)
        _, orig_bytes = create_scientific_citation_pdf(self.tmp_dir / "paper.pdf", pages=3)
        doc = ws.library.import_stream(io.BytesIO(orig_bytes), "paper.pdf")
        doc_id = doc["id"]
        orig_store = ws.library.get_path(doc_id, annotated=False)
        ws.close()

        working_dir = lib_dir / "working"

        # ----------------------------------------------------
        # 1. Test CANCEL
        # ----------------------------------------------------
        app_proc = subprocess.Popen([str(launcher), "--data-dir", str(lib_dir)], env=self.env)
        try:
            lib_win = self._find_window_by_title_substring("Library")
            self._open_document_from_library_dialog(lib_win)

            main_win = self._find_window_by_title_substring("— PaperAssist")
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win], env=self.env)
            subprocess.run(["xdotool", "windowmove", main_win, "0", "0"], env=self.env)
            subprocess.run(["xdotool", "windowsize", main_win, "1400", "900"], env=self.env)
            time.sleep(0.8)

            # Draw stroke to dirty the document
            subprocess.run(["xdotool", "key", "--window", main_win, "d"], env=self.env)
            time.sleep(0.2)
            subprocess.run(["xdotool", "mousemove", "500", "350"], env=self.env)
            subprocess.run(["xdotool", "mousedown", "1"], env=self.env)
            subprocess.run(["xdotool", "mousemove", "550", "400"], env=self.env)
            subprocess.run(["xdotool", "mouseup", "1"], env=self.env)
            time.sleep(0.5)

            # Trigger close prompt via Ctrl+W
            subprocess.run(["xdotool", "key", "--window", main_win, "ctrl+w"], env=self.env)
            time.sleep(0.8)

            # Modal dialog "Unsaved changes" must appear
            dialog_win = self._find_window_by_title_substring("Unsaved changes", timeout=4.0)
            subprocess.run(["xdotool", "windowfocus", "--sync", dialog_win], env=self.env)

            # Press Escape (Cancel)
            subprocess.run(["xdotool", "key", "Escape"], env=self.env)
            time.sleep(0.8)

            # Dialog closes, app process must still be running and dirty
            self.assertIsNone(app_proc.poll(), "Cancel prematurely killed the application process")
            title_after_cancel = subprocess.run(["xdotool", "getwindowname", main_win], env=self.env, capture_output=True, text=True).stdout.strip()
            self.assertTrue(title_after_cancel.startswith("●"), "Cancel did not preserve unsaved edits")
            self.assertEqual(len(list(working_dir.glob("*.pdf"))), 0)

            # ----------------------------------------------------
            # 2. Test DISCARD
            # ----------------------------------------------------
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win], env=self.env)
            subprocess.run(["xdotool", "key", "--window", main_win, "ctrl+w"], env=self.env)
            time.sleep(0.8)
            dialog_win2 = self._find_window_by_title_substring("Unsaved changes", timeout=4.0)
            subprocess.run(["xdotool", "windowfocus", "--sync", dialog_win2], env=self.env)

            # Tab to Discard and press Return
            subprocess.run(["xdotool", "key", "Tab"], env=self.env)
            time.sleep(0.1)
            subprocess.run(["xdotool", "key", "Return"], env=self.env)
            time.sleep(1.0)
            self.assertEqual(len(list(working_dir.glob("*.pdf"))), 0, "Discard wrote revision file")
        finally:
            app_proc.send_signal(signal.SIGTERM)
            app_proc.wait(timeout=5)
            time.sleep(1.0)

        # ----------------------------------------------------
        # 3. Test SAVE on Close
        # ----------------------------------------------------
        app_proc_save = subprocess.Popen([str(launcher), "--data-dir", str(lib_dir)], env=self.env)
        try:
            lib_win3 = self._find_window_by_title_substring("Library")
            self._open_document_from_library_dialog(lib_win3)

            main_win3 = self._find_window_by_title_substring("— PaperAssist")
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win3], env=self.env)
            subprocess.run(["xdotool", "windowmove", main_win3, "0", "0"], env=self.env)
            subprocess.run(["xdotool", "windowsize", main_win3, "1400", "900"], env=self.env)
            time.sleep(0.8)

            # Draw ink stroke
            subprocess.run(["xdotool", "key", "--window", main_win3, "d"], env=self.env)
            time.sleep(0.2)
            subprocess.run(["xdotool", "mousemove", "500", "350"], env=self.env)
            subprocess.run(["xdotool", "mousedown", "1"], env=self.env)
            subprocess.run(["xdotool", "mousemove", "550", "400"], env=self.env)
            subprocess.run(["xdotool", "mouseup", "1"], env=self.env)
            time.sleep(0.5)

            subprocess.run(["xdotool", "key", "--window", main_win3, "ctrl+w"], env=self.env)
            time.sleep(0.8)
            dialog_win3 = self._find_window_by_title_substring("Unsaved changes", timeout=4.0)
            subprocess.run(["xdotool", "windowfocus", "--sync", dialog_win3], env=self.env)

            # Default button is Save; press Return
            subprocess.run(["xdotool", "key", "Return"], env=self.env)
            time.sleep(1.0)
            self.assertEqual(len(list(working_dir.glob("*.pdf"))), 1, "Save on close failed to write revision PDF")
        finally:
            app_proc_save.send_signal(signal.SIGTERM)
            app_proc_save.wait(timeout=5)
            time.sleep(0.5)

    def test_09_quick_view_independent_navigation_and_citation_clicks(self):
        """Verify citation click behaviors on installed binary: reveal for missing PDF, Quick view for local PDF."""
        shutil.copy2(REAL_INSTALLER, self.tmp_dir / "installer.sh")
        subprocess.run(["sh", str(self.tmp_dir / "installer.sh"), "--no-desktop"], env=self.env, check=True, capture_output=True)
        launcher = self.clean_home / ".local" / "bin" / "paperassist"

        # Build paper1 (with citations [1] and [2]) and paper2 (cited paper [1])
        lib_dir = self.tmp_dir / "library_cite"
        ws = Workspace(lib_dir)

        p1_path, p1_bytes = create_scientific_citation_pdf(self.tmp_dir / "paper1.pdf", pages=3)
        p2_path, p2_bytes = create_scientific_citation_pdf(self.tmp_dir / "paper2.pdf", pages=2)

        doc1 = ws.library.import_stream(io.BytesIO(p1_bytes), "paper1.pdf")
        doc2 = ws.library.import_stream(io.BytesIO(p2_bytes), "paper2.pdf")

        # Link reference 1 to doc2, leave reference 2 unlinked
        payload = references.analyze(ws.library, doc1["id"])
        ref1 = next(r for r in payload["references"] if r["label"] == "1")
        references.link(ws.library, doc1["id"], ref1["id"], doc2["id"])
        ws.close()

        app_proc = subprocess.Popen([str(launcher), "--data-dir", str(lib_dir)], env=self.env)
        try:
            lib_win = self._find_window_by_title_substring("Library")
            self._open_document_from_library_dialog(lib_win)

            main_win = self._find_window_by_title_substring("— PaperAssist")
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win], env=self.env)
            subprocess.run(["xdotool", "windowmove", main_win, "0", "0"], env=self.env)
            subprocess.run(["xdotool", "windowsize", main_win, "1400", "900"], env=self.env)
            time.sleep(1.0)

            # Sample regions before clicks
            h_ref_before = self._sample_region_hash(1100, 150, 250, 500)
            h_cite_area_before = self._sample_region_hash(500, 240, 350, 30)
            dark_bottom_before = self._count_dark_pixels(300, 550, 700, 250)

            # ----------------------------------------------------
            # 1. Click citation [2] (missing local PDF) at (806, 257)
            # ----------------------------------------------------
            subprocess.run(["xdotool", "mousemove", "806", "257"], env=self.env)
            time.sleep(0.1)
            subprocess.run(["xdotool", "mousedown", "1"], env=self.env)
            time.sleep(0.1)
            subprocess.run(["xdotool", "mouseup", "1"], env=self.env)
            time.sleep(1.2)

            h_ref_after_cite2 = self._sample_region_hash(1100, 150, 250, 500)
            h_cite_area_after_cite2 = self._sample_region_hash(500, 240, 350, 30)

            # References panel must update/reveal card, main reader must NOT jump to bibliography page
            self.assertNotEqual(h_ref_before, h_ref_after_cite2, "References panel did not reveal card for citation [2]")
            self.assertEqual(h_cite_area_before, h_cite_area_after_cite2, "Citation [2] click erroneously jumped main reader page!")

            # ----------------------------------------------------
            # 2. Click citation [1] (local PDF present) at (607, 257)
            # ----------------------------------------------------
            subprocess.run(["xdotool", "mousemove", "607", "257"], env=self.env)
            time.sleep(0.1)
            subprocess.run(["xdotool", "mousedown", "1"], env=self.env)
            time.sleep(0.1)
            subprocess.run(["xdotool", "mouseup", "1"], env=self.env)
            time.sleep(1.8)

            dark_bottom_after_cite1 = self._count_dark_pixels(300, 550, 700, 250)
            # Quick view must open rendered cited PDF in bottom split reader
            self.assertGreater(dark_bottom_after_cite1, dark_bottom_before + 100, "Quick view did not render split view for citation [1]")

            # ----------------------------------------------------
            # 3. Navigate Quick view pane independently to page 2
            # ----------------------------------------------------
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win], env=self.env)
            subprocess.run(["xdotool", "mousemove", "600", "650"], env=self.env)
            time.sleep(0.1)
            subprocess.run(["xdotool", "mousedown", "1"], env=self.env)
            time.sleep(0.1)
            subprocess.run(["xdotool", "mouseup", "1"], env=self.env)
            time.sleep(0.3)
            subprocess.run(["xdotool", "key", "n"], env=self.env)
            time.sleep(1.2)
        finally:
            app_proc.send_signal(signal.SIGTERM)
            app_proc.wait(timeout=5)

        with sqlite3.connect(lib_dir / "library.sqlite3") as conn:
            row1 = conn.execute("SELECT content FROM document_views WHERE document_id = ?", (doc1["id"],)).fetchone()
            row2 = conn.execute("SELECT content FROM document_views WHERE document_id = ?", (doc2["id"],)).fetchone()
            self.assertIsNotNone(row1)
            self.assertIsNotNone(row2)
            self.assertEqual(json.loads(row1[0]).get("page"), 1, "Primary pane page changed during Quick view navigation")
            self.assertEqual(json.loads(row2[0]).get("page"), 2, "Quick view pane did not navigate independently to page 2")

    def test_10_400_percent_zoom_and_bounded_memory(self):
        """Verify 400% zoom rendering on installed binary and bounded smaps_rollup memory footprint."""
        shutil.copy2(REAL_INSTALLER, self.tmp_dir / "installer.sh")
        subprocess.run(["sh", str(self.tmp_dir / "installer.sh"), "--no-desktop"], env=self.env, check=True, capture_output=True)
        launcher = self.clean_home / ".local" / "bin" / "paperassist"

        test_pdf = self.tmp_dir / "zoom_test.pdf"
        create_scientific_citation_pdf(test_pdf, pages=4)

        app_proc = subprocess.Popen([str(launcher), str(test_pdf)], env=self.env)
        try:
            main_win = self._find_window_by_title_substring("— PaperAssist")
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win], env=self.env)
            subprocess.run(["xdotool", "windowmove", main_win, "0", "0"], env=self.env)
            subprocess.run(["xdotool", "windowsize", main_win, "1400", "900"], env=self.env)
            time.sleep(1.0)

            # Zoom to 400% via Ctrl+equal key repeat (7 steps: 100% -> 120% -> 144% -> 173% -> 207% -> 249% -> 299% -> 400%)
            for _ in range(8):
                subprocess.run(["xdotool", "key", "--window", main_win, "ctrl+equal"], env=self.env)
                time.sleep(0.08)
            time.sleep(1.5)

            # Verify high-resolution rendered pixels via ctypes libX11
            dark_pixels = self._count_dark_pixels(300, 200, 800, 600)
            self.assertGreater(dark_pixels, 100, "400% zoom rendered blank white page")

            # Check memory consumption via smaps_rollup
            children = read_child_pids(app_proc.pid)
            worker_pid = next((c for c in children if b"multiprocessing-fork" in Path(f"/proc/{c}/cmdline").read_bytes()), None)

            ui_smaps = read_smaps_rollup(app_proc.pid)
            wk_smaps = read_smaps_rollup(worker_pid) if worker_pid else {}

            total_pss_mb = (ui_smaps.get("Pss", 0) + wk_smaps.get("Pss", 0)) / 1024
            self.assertLess(total_pss_mb, 450.0, f"Combined PSS ({total_pss_mb:.1f} MB) exceeded 450 MB limit at 400% zoom")
        finally:
            app_proc.send_signal(signal.SIGTERM)
            app_proc.wait(timeout=5)

    def test_11_upgrade_failed_rollback_interrupted_repair_and_uninstall(self):
        """Populated library upgrade, rollback, interrupted repair recovery, and clean uninstaller data preservation."""
        clean_home = self.tmp_dir / "upgrade_home"
        data_dir = clean_home / ".local" / "share"
        config_dir = clean_home / ".config"
        bin_dir = clean_home / ".local" / "bin"
        for d in (clean_home, data_dir, config_dir, bin_dir):
            d.mkdir(parents=True, exist_ok=True)

        env = {
            "HOME": str(clean_home),
            "XDG_DATA_HOME": str(data_dir),
            "XDG_CONFIG_HOME": str(config_dir),
            "XDG_BIN_HOME": str(bin_dir),
            "PATH": "/usr/bin:/bin",
            "QT_QPA_PLATFORM": "offscreen",
        }

        # 1. Populate disposable library in ${XDG_DATA_HOME}/paperassist & ${XDG_CONFIG_HOME}/paperassist
        lib_dir = data_dir / "paperassist"
        orig_dir = lib_dir / "originals"
        work_dir = lib_dir / "working"
        orig_dir.mkdir(parents=True)
        work_dir.mkdir(parents=True)

        sample_bytes = SAMPLE_PDF.read_bytes()
        (orig_dir / "paper1.pdf").write_bytes(sample_bytes)
        (work_dir / "1-1.pdf").write_bytes(sample_bytes)
        (lib_dir / "library.sqlite3").write_text("SQLite format 3\x00populated_test_db")
        (lib_dir / "settings.json").write_text('{"recent": ["1"]}')

        pa_config = config_dir / "paperassist"
        pa_config.mkdir(parents=True)
        (pa_config / "config.json").write_text('{"theme": "dark", "library_path": "' + str(lib_dir) + '"}')

        lib_digests = record_tree_hashes(lib_dir)
        cfg_digests = record_tree_hashes(pa_config)

        # 2. Simulate earlier installed version (0.1.0a0)
        b0 = self.tmp_dir / "b0"
        b0.mkdir()
        (b0 / "PaperAssist").write_text('#!/bin/sh\necho "PaperAssist 0.1.0a0"\n')
        (b0 / "PaperAssist").chmod(0o755)
        (b0 / "_internal").mkdir()
        (b0 / "_internal" / "libv0.so").write_bytes(b"\x7fELFfakev0")
        (b0 / "LICENSE").write_text("GNU GPL-3.0-or-later v0")
        (b0 / "THIRD-PARTY-NOTICES.txt").write_text("Third party notices v0")
        d0 = self.tmp_dir / "d0"
        d0.mkdir()
        (d0 / "paperassist.desktop").write_text("[Desktop Entry]\nName=PaperAssist\nType=Application\nExec=PaperAssist\n")
        (d0 / "paperassist.svg").write_text("<svg>icon_v0</svg>")
        (d0 / "paperassist.png").write_bytes(b"\x89PNGicon_v0")
        inst0 = self.tmp_dir / "installer_v0.sh"
        build_installer(bundle_dir=b0, output_path=inst0, desktop_dir=d0, version="0.1.0a0", app_name="PaperAssist")

        res_v0_inst = subprocess.run([str(inst0)], env=env, capture_output=True, text=True)
        self.assertEqual(res_v0_inst.returncode, 0)
        launcher = bin_dir / "paperassist"
        res_v0_check = subprocess.run([str(launcher)], env=env, capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_v0_check.stdout)

        # Upgrade to the current frozen package.
        res_up = subprocess.run([str(REAL_INSTALLER)], env=env, capture_output=True, text=True)
        self.assertEqual(res_up.returncode, 0, f"Upgrade failed:\n{res_up.stderr}")
        self.assertIn("installed successfully", res_up.stdout)

        manifest_text = (data_dir / "paperassist-app" / ".paperassist-install-manifest").read_text(encoding="utf-8")
        self.assertIn(f"VERSION={__version__}", manifest_text)

        res_v1 = subprocess.run([str(launcher), "--version"], env=env, capture_output=True, text=True)
        self.assertIn(f"PaperAssist {__version__}", res_v1.stdout)
        res_st = subprocess.run([str(launcher), "--self-test", str(SAMPLE_PDF)], env=env, capture_output=True, text=True)
        self.assertEqual(res_st.returncode, 0, f"Self-test on upgraded binary failed:\n{res_st.stderr}")

        # User data 100% byte-for-byte identical after upgrade
        self.assertEqual(record_tree_hashes(lib_dir), lib_digests)
        self.assertEqual(record_tree_hashes(pa_config), cfg_digests)

        # 4. Failed upgrade: corrupted/truncated installer
        corrupted = self.tmp_dir / "corrupted_installer.sh"
        shutil.copy2(REAL_INSTALLER, corrupted)
        with open(corrupted, "r+b") as fp:
            fp.truncate(os.path.getsize(corrupted) - 4096)
        res_cor = subprocess.run([str(corrupted)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res_cor.returncode, 0)

        # The existing installation remains fully working.
        res_st_cor = subprocess.run([str(launcher), "--self-test", str(SAMPLE_PDF)], env=env, capture_output=True, text=True)
        self.assertEqual(res_st_cor.returncode, 0)

        # 5. Failed upgrade: fault injection after_backup
        env_fault = {**env, "__PAPERASSIST_INSTALLER_FAULT_INJECT": "after_backup"}
        res_fault = subprocess.run([str(REAL_INSTALLER)], env=env_fault, capture_output=True, text=True)
        self.assertNotEqual(res_fault.returncode, 0)
        self.assertIn("Rolling back changes", res_fault.stdout + res_fault.stderr)

        res_st_flt = subprocess.run([str(launcher), "--self-test", str(SAMPLE_PDF)], env=env, capture_output=True, text=True)
        self.assertEqual(res_st_flt.returncode, 0)

        # 6. Verify Defect 1: Rejected library target leaves adjacent unowned backups intact
        unrelated_backup = lib_dir.with_name(lib_dir.name + ".backup.2026")
        unrelated_backup.mkdir()
        sentinel_pdf = unrelated_backup / "private_research.pdf"
        sentinel_pdf.write_bytes(b"sentinel research content")

        res_rej = subprocess.run([str(REAL_INSTALLER), "--install-dir", str(lib_dir), "--no-launcher", "--no-desktop"],
                                 env=env, capture_output=True, text=True)
        self.assertEqual(res_rej.returncode, 1)
        self.assertTrue(sentinel_pdf.is_file(), "Installer defect 1 reproduced: adjacent unowned backup was deleted!")
        self.assertEqual(sentinel_pdf.read_bytes(), b"sentinel research content")

        # 7. Verify Defect 2: Interrupted installation (post-swap crash) recovers cleanly via --repair
        app = data_dir / "paperassist-app"
        backup_swap = app.with_name(app.name + ".backup.999")
        app.rename(backup_swap)
        shutil.copytree(backup_swap, app, symlinks=True)
        (app / ".paperassist-install-manifest").unlink()

        res_rep = subprocess.run([str(REAL_INSTALLER), "--repair"], env=env, capture_output=True, text=True)
        self.assertEqual(res_rep.returncode, 0, f"Installer defect 2 reproduced: --repair failed to recover post-swap crash:\n{res_rep.stderr}")
        self.assertTrue((app / ".paperassist-install-manifest").is_file(), "Recovered installation missing manifest")

        res_st_rep = subprocess.run([str(launcher), "--self-test", str(SAMPLE_PDF)], env=env, capture_output=True, text=True)
        self.assertEqual(res_st_rep.returncode, 0)

        # 8. Clean uninstall via uninstall.sh
        uninst_sh = data_dir / "paperassist-app" / "uninstall.sh"
        self.assertTrue(uninst_sh.is_file())
        res_un = subprocess.run([str(uninst_sh)], env=env, capture_output=True, text=True)
        self.assertEqual(res_un.returncode, 0, f"Uninstall failed:\n{res_un.stderr}")

        self.assertFalse((data_dir / "paperassist-app").exists())
        self.assertFalse(launcher.exists())
        self.assertFalse((data_dir / "applications" / "paperassist.desktop").exists())

        # Populated user data 100% intact
        self.assertEqual(record_tree_hashes(lib_dir), lib_digests)
        self.assertEqual(record_tree_hashes(pa_config), cfg_digests)

    def test_12_installed_frozen_runtime_measurements(self):
        """Measure cold/warm self-test, startup, and memory footprint of the real installed frozen binary."""
        shutil.copy2(REAL_INSTALLER, self.tmp_dir / "installer.sh")
        subprocess.run(["sh", str(self.tmp_dir / "installer.sh"), "--no-desktop"], env=self.env, check=True, capture_output=True)
        launcher = self.clean_home / ".local" / "bin" / "paperassist"

        bench_pdf, _ = create_scientific_citation_pdf(self.tmp_dir / "bench.pdf", pages=8)

        # 1. Cold self-test time
        t0 = time.perf_counter()
        res_cold = subprocess.run([str(launcher), "--self-test", str(bench_pdf)], env=self.env, capture_output=True, text=True)
        t_cold = time.perf_counter() - t0
        self.assertEqual(res_cold.returncode, 0)

        # 2. Warm self-test time
        t1 = time.perf_counter()
        res_warm = subprocess.run([str(launcher), "--self-test", str(bench_pdf)], env=self.env, capture_output=True, text=True)
        t_warm = time.perf_counter() - t1
        self.assertEqual(res_warm.returncode, 0)

        # 3. Window startup to mapped and rendered X11 window
        t_start0 = time.perf_counter()
        app_proc = subprocess.Popen([str(launcher), str(bench_pdf)], env=self.env)
        try:
            main_win = self._find_window_by_title_substring("— PaperAssist")
            subprocess.run(["xdotool", "windowfocus", "--sync", main_win], env=self.env)
            subprocess.run(["xdotool", "windowmove", main_win, "0", "0"], env=self.env)
            subprocess.run(["xdotool", "windowsize", main_win, "1400", "900"], env=self.env)

            # Wait for non-blank pixels in reader area
            deadline = time.monotonic() + 5.0
            dark_pixels = 0
            while time.monotonic() < deadline:
                dark_pixels = self._count_dark_pixels(300, 200, 800, 600)
                if dark_pixels > 50:
                    break
                time.sleep(0.05)
            t_startup_rendered = time.perf_counter() - t_start0
            self.assertGreater(dark_pixels, 50, "App window failed to render first page pixels")

            # Memory at 100% zoom
            children = read_child_pids(app_proc.pid)
            worker_pid = next((c for c in children if b"multiprocessing-fork" in Path(f"/proc/{c}/cmdline").read_bytes()), None)

            ui_smaps_100 = read_smaps_rollup(app_proc.pid)
            wk_smaps_100 = read_smaps_rollup(worker_pid) if worker_pid else {}

            # Zoom to 400%
            for _ in range(8):
                subprocess.run(["xdotool", "key", "--window", main_win, "ctrl+equal"], env=self.env)
                time.sleep(0.08)
            time.sleep(1.2)

            ui_smaps_400 = read_smaps_rollup(app_proc.pid)
            wk_smaps_400 = read_smaps_rollup(worker_pid) if worker_pid else {}
        finally:
            app_proc.send_signal(signal.SIGTERM)
            app_proc.wait(timeout=5)

        # Assert performance bounds
        self.assertLess(t_cold, 10.0, f"Cold self-test too slow: {t_cold:.2f}s")
        self.assertLess(t_warm, 5.0, f"Warm self-test too slow: {t_warm:.2f}s")
        self.assertLess(t_startup_rendered, 3.5, f"Startup + render too slow: {t_startup_rendered:.2f}s")

        total_pss_100 = (ui_smaps_100.get("Pss", 0) + wk_smaps_100.get("Pss", 0)) / 1024
        total_pss_400 = (ui_smaps_400.get("Pss", 0) + wk_smaps_400.get("Pss", 0)) / 1024
        self.assertLess(total_pss_400, 450.0, f"400% PSS ({total_pss_400:.1f} MB) exceeded 450 MB limit")

        print("\n" + "=" * 70)
        print(f"GENUINE INSTALLED FROZEN APPLICATION BENCHMARK ({__version__})")
        print("=" * 70)
        print(f"Cold --self-test wall time:          {t_cold*1000:.1f} ms")
        print(f"Warm --self-test wall time:          {t_warm*1000:.1f} ms")
        print(f"Startup to mapped & rendered X11:    {t_startup_rendered*1000:.1f} ms")
        print(f"UI Process RSS (100% zoom):          {ui_smaps_100.get('Rss', 0) / 1024:.2f} MB (PSS: {ui_smaps_100.get('Pss', 0) / 1024:.2f} MB)")
        print(f"Worker Process RSS (100% zoom):      {wk_smaps_100.get('Rss', 0) / 1024:.2f} MB (PSS: {wk_smaps_100.get('Pss', 0) / 1024:.2f} MB)")
        print(f"Total PSS (100% zoom):               {total_pss_100:.2f} MB")
        print(f"UI Process RSS (400% zoom):          {ui_smaps_400.get('Rss', 0) / 1024:.2f} MB (PSS: {ui_smaps_400.get('Pss', 0) / 1024:.2f} MB)")
        print(f"Worker Process RSS (400% zoom):      {wk_smaps_400.get('Rss', 0) / 1024:.2f} MB (PSS: {wk_smaps_400.get('Pss', 0) / 1024:.2f} MB)")
        print(f"Total PSS (400% zoom):               {total_pss_400:.2f} MB")
        print("=" * 70 + "\n")


# ===========================================================================
# 2. Source-Level (.venv) Widget Regressions (Explicitly Supplementary)
# ===========================================================================
@needs_qt
class TestSourceLevelWidgetRegressions(unittest.TestCase):
    """Supplementary source-level (.venv) widget unit and regression tests.

    These tests run inside the test runner Python process using virtual environment sources,
    exercising in-process PySide6 widget logic and RenderClient internal timings.
    These measurements are explicitly documented as source-process results and are never
    described as installed frozen executable acceptance.
    """

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

        self.lib_dir = self.tmp_dir / "library"
        self.workspace = Workspace(self.lib_dir)
        self.addCleanup(self.workspace.close)

        self.main_pdf_path, self.main_pdf_bytes = create_scientific_citation_pdf(self.tmp_dir / "main.pdf", pages=4)
        self.other_pdf_path, self.other_pdf_bytes = create_scientific_citation_pdf(self.tmp_dir / "other.pdf", pages=3)

    def pump(self, condition=lambda: False, timeout=10.0):
        """Process Qt events until condition returns True or timeout expires."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.005)
        return condition()

    def test_13_source_level_400_percent_cache_budget(self):
        """Source-level regression: Verify high-zoom cache budget enforcement in PdfView."""
        main_doc = self.workspace.library.import_stream(io.BytesIO(self.main_pdf_bytes), "main.pdf")
        doc_id = main_doc["id"]

        win = MainWindow(self.client, self.workspace)
        try:
            win.show()
            tab = win.open_library_document(doc_id)
            view = tab.main.view
            self.assertTrue(self.pump(lambda: view.has_page(0)))

            view._set_zoom(4.0)
            self.assertEqual(view.zoom, 4.0)
            self.assertTrue(self.pump(lambda: view.has_page(0)))

            self.assertLessEqual(view.cache.total(), view.cache.budget)
            cached_page = view.cache.get(0)
            self.assertIsNotNone(cached_page)
            scale, cached_doc_id, pixmap = cached_page
            self.assertEqual(scale, 4.0)
            self.assertEqual(cached_doc_id, view.doc_id)
            self.assertGreater(pixmap.width(), 1000)
            self.assertGreater(pixmap.height(), 1000)
        finally:
            win.close()
            win.deleteLater()
            self.app.processEvents()

    def test_14_source_level_independent_navigation_controls(self):
        """Source-level regression: Verify dual navigation toolbar controls act only on their respective pane."""
        main_doc = self.workspace.library.import_stream(io.BytesIO(self.main_pdf_bytes), "main.pdf")
        other_doc = self.workspace.library.import_stream(io.BytesIO(self.other_pdf_bytes), "other.pdf")

        win = MainWindow(self.client, self.workspace)
        try:
            win.show()
            tab = win.open_library_document(main_doc["id"])
            quick = win.open_library_document_in_quick_view(other_doc["id"])
            self.assertTrue(self.pump(lambda: tab.main.view.has_page(0) and quick.view.has_page(0)))

            main_view, quick_view = tab.main.view, quick.view

            for side_by_side in (False, True):
                tab.set_side_by_side(side_by_side)
                tab.activate(quick)

                win.shortcuts.page_field.edit.setText("2")
                win.shortcuts.page_field.commit()
                self.assertEqual(current_page(main_view), 1)
                self.assertEqual(current_page(quick_view), 0)

                quick.navigation_field.edit.setText("3")
                quick.navigation_field.commit()
                self.assertEqual(current_page(quick_view), 2)
                self.assertEqual(current_page(main_view), 1)

                m_zoom, q_zoom = main_view.zoom, quick_view.zoom
                zoom_in_action = next(a for a in quick.navigation_bar.actions() if a.text() == "Second paper: zoom in")
                zoom_in_action.trigger()
                self.assertEqual(main_view.zoom, m_zoom)
                self.assertGreater(quick_view.zoom, q_zoom)

                win.fit_action.trigger()
                self.assertTrue(main_view.fit)
                self.assertFalse(quick_view.fit)

                main_view.goto(0)
                quick_view.goto(0)
        finally:
            win.close()
            win.deleteLater()
            self.app.processEvents()

    def test_15_source_level_citation_clicks(self):
        """Source-level regression: In-process citation click dispatching to Quick view and references panel."""
        main_doc = self.workspace.library.import_stream(io.BytesIO(self.main_pdf_bytes), "main.pdf")
        other_doc = self.workspace.library.import_stream(io.BytesIO(self.other_pdf_bytes), "other.pdf")

        payload = references.analyze(self.workspace.library, main_doc["id"])
        ref1 = next(r for r in payload["references"] if r["label"] == "1")
        ref2 = next(r for r in payload["references"] if r["label"] == "2")
        references.link(self.workspace.library, main_doc["id"], ref1["id"], other_doc["id"])

        win = MainWindow(self.client, self.workspace)
        try:
            win.resize(1400, 900)
            win.show()
            tab = win.open_library_document(main_doc["id"])
            view = tab.main.view
            panel = win.details
            self.assertTrue(self.pump(lambda: panel.list.count() == 2 and view.has_page(0)))

            win.set_columns(details=False)
            self.assertIsNone(tab.quick)

            links = view.doc.links(0)
            self.assertGreaterEqual(len(links), 2)

            # Click in-text citation [1] (local PDF present)
            link1 = links[0]
            pos1 = view._band(0, Rect(*link1.rect)).center().toPoint()
            QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, pos1)
            self.pump(lambda: tab.quick is not None and tab.quick.view.has_page(0))

            self.assertIsNotNone(tab.quick)
            self.assertEqual(tab.quick.view.doc.source.document_id, other_doc["id"])

            # Click in-text citation [2] (local PDF missing)
            before_state = (view.zoom, view.verticalScrollBar().value(), current_page(view))
            link2 = links[1]
            pos2 = view._band(0, Rect(*link2.rect)).center().toPoint()
            QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, pos2)
            self.pump(lambda: panel.expanded == ref2["id"])

            self.assertTrue(win.details_column.isVisible())
            self.assertEqual(win.detail_switch.mode, "references")
            self.assertEqual(panel.expanded, ref2["id"])
            self.assertEqual(panel.list.currentItem().data(Qt.UserRole + 1), ref2["id"])

            after_state = (view.zoom, view.verticalScrollBar().value(), current_page(view))
            self.assertEqual(before_state, after_state)
        finally:
            win.close()
            win.deleteLater()
            self.app.processEvents()

    def test_16_source_level_runtime_measurements(self):
        """Source-level benchmark: In-process RenderClient rendering latencies and citation locate timing."""
        doc_info = self.workspace.library.import_stream(io.BytesIO(self.main_pdf_bytes), "main.pdf")
        doc_id = doc_info["id"]

        win = MainWindow(self.client, self.workspace)
        try:
            win.show()
            tab = win.open_library_document(doc_id)
            view = tab.main.view
            while not view.has_page(0):
                self.app.processEvents()
                time.sleep(0.002)

            latency_100 = view.render_seconds[-1]

            view._set_zoom(4.0)
            while not view.has_page(0):
                self.app.processEvents()
                time.sleep(0.002)
            for _ in range(5):
                self.app.processEvents()
                time.sleep(0.005)

            latency_400 = view.render_seconds[-1]

            payload = references.analyze(self.workspace.library, doc_id)
            ref0 = payload["references"][0]
            loc_done = [False]
            loc_result = [None]

            def on_locate(res, _err):
                loc_result[0] = res
                loc_done[0] = True

            t_loc0 = time.perf_counter()
            self.client.locate(view.doc_id, ref0, on_locate)
            while not loc_done[0]:
                self.app.processEvents()
                time.sleep(0.002)
            t_citation_locate = time.perf_counter() - t_loc0

            self.assertLess(latency_100, 0.5, f"100% render latency too high: {latency_100:.3f}s")
            self.assertLess(latency_400, 1.0, f"400% render latency too high: {latency_400:.3f}s")
            self.assertLess(t_citation_locate, 0.5, f"Citation locate too slow: {t_citation_locate:.3f}s")

            print("\n" + "=" * 70)
            print("SUPPLEMENTARY SOURCE-LEVEL (.VENV) BENCHMARK SUMMARY")
            print("=" * 70)
            print(f"Page render latency (100% zoom):    {latency_100*1000:.2f} ms")
            print(f"Page render latency (400% zoom):    {latency_400*1000:.2f} ms")
            print(f"Citation locate latency:            {t_citation_locate*1000:.2f} ms")
            print("=" * 70 + "\n")
        finally:
            win.close()
            win.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
