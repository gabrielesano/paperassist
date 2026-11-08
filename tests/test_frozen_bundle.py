"""Tests for the frozen PyInstaller bundle (dist/PaperAssist).

Skipped cleanly if the bundle has not been built yet.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paperassist import __version__

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = ROOT / "dist" / "PaperAssist"
EXE_PATH = BUNDLE_DIR / "PaperAssist"
FIXTURE_PDF = ROOT / "tests" / "fixtures" / "pdfjs-annotations.pdf"


def bundle_available():
    return EXE_PATH.is_file() and os.access(EXE_PATH, os.X_OK)


@unittest.skipUnless(bundle_available(), "Frozen bundle dist/PaperAssist/PaperAssist is not built")
class FrozenBundleStructureTests(unittest.TestCase):
    """Structural and file integrity tests for dist/PaperAssist."""

    def test_top_level_files(self):
        self.assertTrue(EXE_PATH.is_file())
        self.assertTrue(os.access(EXE_PATH, os.X_OK))

        internal_dir = BUNDLE_DIR / "_internal"
        self.assertTrue(internal_dir.is_dir())

        license_file = BUNDLE_DIR / "LICENSE"
        self.assertTrue(license_file.is_file(), "Top-level dist/PaperAssist/LICENSE missing")
        self.assertIn("General Public License", license_file.read_text(encoding="utf-8"))

        notices_file = BUNDLE_DIR / "THIRD-PARTY-NOTICES.txt"
        self.assertTrue(notices_file.is_file(), "Top-level dist/PaperAssist/THIRD-PARTY-NOTICES.txt missing")
        notices_text = notices_file.read_text(encoding="utf-8")
        self.assertIn("Python / CPython Runtime", notices_text)
        self.assertIn("Qt for Python", notices_text)
        self.assertIn("PDFium via pypdfium2", notices_text)
        self.assertIn("pypdf", notices_text)
        self.assertIn("libsqlite3", notices_text)

    def test_bundled_sqlite_and_pdfium(self):
        internal_dir = BUNDLE_DIR / "_internal"
        sqlite_libs = list(internal_dir.glob("libsqlite3.so*"))
        self.assertTrue(bool(sqlite_libs), "libsqlite3.so must be bundled in _internal to avoid host dependencies")

        pdfium_libs = list(internal_dir.glob("**/libpdfium.so*"))
        self.assertTrue(bool(pdfium_libs), "libpdfium.so must be bundled in _internal")

    def test_dist_info_metadata(self):
        internal_dir = BUNDLE_DIR / "_internal"
        dist_infos = [d.name.lower() for d in internal_dir.glob("*.dist-info")]
        self.assertTrue(any(name.startswith("paperassist-") for name in dist_infos), "paperassist dist-info missing")
        self.assertTrue(any(name.startswith("pypdf-") for name in dist_infos), "pypdf dist-info missing")
        self.assertTrue(any(name.startswith("pypdfium2-") for name in dist_infos), "pypdfium2 dist-info missing")
        self.assertTrue(
            any("pyside6" in name for name in dist_infos),
            "PySide6 / PySide6-Essentials dist-info missing",
        )


@unittest.skipUnless(bundle_available(), "Frozen bundle dist/PaperAssist/PaperAssist is not built")
class FrozenBundleElfTests(unittest.TestCase):
    """Dynamic linking, ldd, and ELF symbol version checks."""

    def test_ldd_no_missing_libraries(self):
        internal_dir = BUNDLE_DIR / "_internal"
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = f"{internal_dir}:{internal_dir / 'PySide6' / 'Qt' / 'lib'}"

        missing = []
        for root_dir, _, files in os.walk(BUNDLE_DIR):
            for file in files:
                p = Path(root_dir) / file
                try:
                    with open(p, "rb") as fp:
                        if fp.read(4) != b"\x7fELF":
                            continue
                except OSError:
                    continue
                res = subprocess.run(["ldd", str(p)], capture_output=True, text=True, env=env)
                for line in res.stdout.splitlines():
                    if "not found" in line:
                        missing.append((str(p.relative_to(BUNDLE_DIR)), line.strip()))

        self.assertEqual(missing, [], f"Missing shared libraries detected in bundle: {missing}")

    def test_pyinstaller_bootloader_glibc_requirement(self):
        res = subprocess.run(["readelf", "-V", str(EXE_PATH)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        glibc_versions = []
        for m in re.finditer(r"\bGLIBC_([0-9]+\.[0-9]+)\b", res.stdout):
            v_tuple = tuple(int(x) for x in m.group(1).split("."))
            glibc_versions.append(v_tuple)
        self.assertTrue(bool(glibc_versions))
        max_boot_glibc = max(glibc_versions)
        # PyInstaller bootloaders typically require <= GLIBC 2.17
        self.assertLessEqual(max_boot_glibc, (2, 28), f"Bootloader requires surprisingly high GLIBC: {max_boot_glibc}")


@unittest.skipUnless(bundle_available(), "Frozen bundle dist/PaperAssist/PaperAssist is not built")
class FrozenBundleExecutionTests(unittest.TestCase):
    """Execution, subprocess isolation, and self-test verification."""

    def test_self_test_runs_and_passes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = {
                "HOME": tmp_dir,
                "PATH": "/usr/bin:/bin",
                "QT_QPA_PLATFORM": "offscreen",
            }
            cmd = [str(EXE_PATH), "--self-test", str(FIXTURE_PDF)]
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=tmp_dir)
            self.assertEqual(res.returncode, 0, f"Self-test failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
            self.assertIn("self-test ok", res.stdout)

    def test_metadata_and_about_components_in_frozen_app(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = {
                "HOME": tmp_dir,
                "PATH": "/usr/bin:/bin",
                "QT_QPA_PLATFORM": "offscreen",
            }
            res = subprocess.run([str(EXE_PATH), "--version"], capture_output=True, text=True, env=env, cwd=tmp_dir)
            self.assertEqual(res.returncode, 0, f"--version failed:\n{res.stderr}")
            self.assertIn(f"PaperAssist {__version__}", res.stdout)

    def test_isolated_bundle_outside_repo_no_leakage(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            isolated_bundle = Path(tmp_dir) / "PaperAssist"
            shutil.copytree(BUNDLE_DIR, isolated_bundle, symlinks=True)
            clean_home = Path(tmp_dir) / "home"
            clean_home.mkdir()
            sample_copy = Path(tmp_dir) / "sample.pdf"
            shutil.copy2(FIXTURE_PDF, sample_copy)

            iso_exe = isolated_bundle / "PaperAssist"
            env = {
                "HOME": str(clean_home),
                "PATH": "/usr/bin:/bin",
                "QT_QPA_PLATFORM": "offscreen",
            }

            # 1. Self-test outside repo (also verifies component_info metadata inside frozen bundle)
            res = subprocess.run([str(iso_exe), "--self-test", str(sample_copy)],
                                 capture_output=True, text=True, env=env, cwd=tmp_dir)
            self.assertEqual(res.returncode, 0, f"Isolated self-test failed: {res.stderr}")
            self.assertIn("self-test ok", res.stdout)
            self.assertNotIn("self-test metadata missing", res.stdout)

            # 2. If strace is available, verify zero files opened from repo checkout or .venv
            if shutil.which("strace"):
                trace_file = Path(tmp_dir) / "strace.log"
                res2 = subprocess.run(
                    ["strace", "-f", "-e", "trace=file", "-o", str(trace_file), str(iso_exe), "--version"],
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=tmp_dir,
                )
                self.assertEqual(res2.returncode, 0)
                trace_text = trace_file.read_text(encoding="utf-8", errors="replace")
                self.assertNotIn(str(ROOT), trace_text, "Frozen binary opened a path inside the repository checkout")
                self.assertNotIn(".venv", trace_text, "Frozen binary opened a path inside .venv")


@unittest.skipUnless(bundle_available(), "Frozen bundle dist/PaperAssist/PaperAssist is not built")
class FrozenBundleProvenanceTests(unittest.TestCase):
    """Provenance and ELF audit verification."""

    def test_build_provenance_json_files(self):
        root_prov = BUNDLE_DIR / "BUILD-PROVENANCE.json"
        internal_prov = BUNDLE_DIR / "_internal" / "BUILD-PROVENANCE.json"

        self.assertTrue(root_prov.is_file(), "dist/PaperAssist/BUILD-PROVENANCE.json missing")
        self.assertTrue(internal_prov.is_file(), "dist/PaperAssist/_internal/BUILD-PROVENANCE.json missing")

        data_root = json.loads(root_prov.read_text(encoding="utf-8"))
        data_int = json.loads(internal_prov.read_text(encoding="utf-8"))
        self.assertEqual(data_root, data_int)

        self.assertEqual(data_root.get("version"), __version__)
        commit = data_root.get("git_commit", "")
        self.assertTrue(
            bool(re.match(r"^[0-9a-fA-F]{7,40}$", commit)),
            f"git_commit must be a valid hex SHA, got: {commit!r}",
        )
        self.assertIn("bootloader_max_glibc", data_root)
        self.assertIn("bundle_max_glibc", data_root)

        # Scan all ELF files in dist/PaperAssist/
        glibc_re = re.compile(r"\bGLIBC_([0-9]+\.[0-9]+)\b")
        max_glibc_found = (0, 0)

        for p in BUNDLE_DIR.rglob("*"):
            if not p.is_file() or p.is_symlink():
                continue
            try:
                with open(p, "rb") as fp:
                    if fp.read(4) != b"\x7fELF":
                        continue
            except OSError:
                continue

            res = subprocess.run(["readelf", "-V", str(p)], capture_output=True, text=True)
            for m in glibc_re.finditer(res.stdout):
                v_tuple = tuple(int(x) for x in m.group(1).split("."))
                if v_tuple > max_glibc_found:
                    max_glibc_found = v_tuple

        expected_str = data_root["bundle_max_glibc"]
        m_exp = glibc_re.search(expected_str)
        self.assertIsNotNone(m_exp, f"Invalid bundle_max_glibc string in provenance: {expected_str}")
        expected_tuple = tuple(int(x) for x in m_exp.group(1).split("."))

        self.assertEqual(
            max_glibc_found,
            expected_tuple,
            f"Empirical max GLIBC ({max_glibc_found}) does not match provenance ({expected_tuple})",
        )


if __name__ == "__main__":
    unittest.main()
