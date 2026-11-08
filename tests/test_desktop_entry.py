"""Tests for PaperAssist desktop entry, application icons, and launch contract."""

from __future__ import annotations

import configparser
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_DIR = REPO_ROOT / "packaging" / "desktop"
DESKTOP_FILE = DESKTOP_DIR / "paperassist.desktop"
SVG_FILE = DESKTOP_DIR / "paperassist.svg"
PNG_FILE = DESKTOP_DIR / "paperassist.png"
GENERATE_SCRIPT = DESKTOP_DIR / "generate_icons.py"
SAMPLE_PDF = REPO_ROOT / "tests" / "fixtures" / "pdfjs-annotations.pdf"
FROZEN_BINARY = REPO_ROOT / "dist" / "PaperAssist" / "PaperAssist"

# Import helper from generate_icons
sys.path.insert(0, str(DESKTOP_DIR))
from generate_icons import quote_exec_path, TEAL, WHITE  # noqa: E402


class DesktopEntryTestCase(unittest.TestCase):
    """Test suite for desktop file specification, icon assets, and unrelated-CWD launching."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="pa-desk-test-")
        self.test_root = Path(self._temp_dir.name)
        self.fake_home = self.test_root / "home"
        self.fake_home.mkdir(parents=True, exist_ok=True)

        self.env = os.environ.copy()
        self.env["HOME"] = str(self.fake_home)
        self.env["QT_QPA_PLATFORM"] = "offscreen"

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_desktop_entry_fields(self) -> None:
        """Validate desktop file follows Freedesktop 1.0 specification and brand requirements."""
        self.assertTrue(DESKTOP_FILE.is_file(), f"Missing desktop entry: {DESKTOP_FILE}")

        content = DESKTOP_FILE.read_text(encoding="utf-8")
        lines = content.splitlines()

        # 1. Header comment
        self.assertTrue(
            any(line.strip() == "# Managed-By: PaperAssist-Linux-Installer" for line in lines),
            "Desktop file must contain '# Managed-By: PaperAssist-Linux-Installer' header",
        )

        # 2. Parse INI format
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(content)

        self.assertIn("Desktop Entry", config.sections(), "Missing [Desktop Entry] section")
        entry = config["Desktop Entry"]

        self.assertEqual(entry.get("Version"), "1.0")
        self.assertEqual(entry.get("Type"), "Application")
        self.assertEqual(entry.get("Name"), "PaperAssist")
        self.assertEqual(entry.get("GenericName"), "Research Paper Reader")
        self.assertEqual(
            entry.get("Comment"),
            "Read research PDFs locally and consult cited references",
        )
        self.assertEqual(entry.get("Exec"), "paperassist")
        self.assertEqual(entry.get("Icon"), "paperassist")
        self.assertEqual(entry.get("Terminal"), "false")
        self.assertEqual(entry.get("Categories"), "Office;Viewer;Science;Literature;")
        self.assertEqual(
            entry.get("Keywords"),
            "PDF;research;papers;citations;bibliography;academic;",
        )
        self.assertEqual(entry.get("StartupNotify"), "true")
        self.assertEqual(entry.get("StartupWMClass"), "PaperAssist")

    def test_scope_restrictions_no_mimetypes_no_field_codes(self) -> None:
        """Verify strict alpha scope: no MimeType association and no %f/%F/%u/%U codes."""
        content = DESKTOP_FILE.read_text(encoding="utf-8")
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(content)
        entry = config["Desktop Entry"]

        # Alpha scope: PaperAssist does NOT register as default PDF reader or associate MIME types
        self.assertNotIn("MimeType", entry, "MimeType must NOT be set in alpha desktop entry")
        self.assertNotIn(
            "application/pdf", content, "Desktop entry must not declare application/pdf association"
        )

        # Exec must NOT contain file-open field codes (%f, %F, %u, %U)
        exec_val = entry.get("Exec", "")
        for code in ("%f", "%F", "%u", "%U"):
            self.assertNotIn(
                code,
                exec_val,
                f"Exec command must not contain '{code}' field code in minimal desktop entry",
            )

    def test_desktop_file_validate(self) -> None:
        """Run freedesktop desktop-file-validate validator if installed."""
        validator = shutil.which("desktop-file-validate")
        if not validator:
            self.skipTest("desktop-file-validate is not installed on this system")

        res = subprocess.run(
            [validator, str(DESKTOP_FILE)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"desktop-file-validate failed on {DESKTOP_FILE}:\n{res.stdout}\n{res.stderr}",
        )

    def test_desktop_file_validate_with_spaces_in_exec(self) -> None:
        """Verify desktop-file-validate accepts Exec paths with spaces when properly quoted."""
        validator = shutil.which("desktop-file-validate")
        if not validator:
            self.skipTest("desktop-file-validate is not installed on this system")

        spaced_exec = quote_exec_path("/tmp/dir with spaces/paperassist-app/PaperAssist")
        self.assertTrue(spaced_exec.startswith('"') and spaced_exec.endswith('"'))

        custom_desktop = self.test_root / "paperassist-spaced.desktop"
        content = DESKTOP_FILE.read_text(encoding="utf-8")
        modified = "\n".join(
            f"Exec={spaced_exec}" if line.startswith("Exec=") else line
            for line in content.splitlines()
        )
        custom_desktop.write_text(modified, encoding="utf-8")

        res = subprocess.run(
            [validator, str(custom_desktop)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"desktop-file-validate failed on spaced Exec desktop file:\n{res.stdout}\n{res.stderr}",
        )

    def test_quote_exec_path_helper(self) -> None:
        """Test the Freedesktop Exec= quoting and escaping helper."""
        # Simple path without spaces or quotes remains unquoted
        self.assertEqual(quote_exec_path("/usr/bin/paperassist"), "/usr/bin/paperassist")

        # Paths with spaces must be enclosed in quotes
        quoted_space = quote_exec_path("/home/user name/.local/bin/paperassist")
        self.assertEqual(quoted_space, '"/home/user name/.local/bin/paperassist"')

        # Special characters $, `, ", \ must be escaped
        special_path = '/home/user/app "$test"`dir\\PaperAssist'
        quoted_special = quote_exec_path(special_path)
        self.assertTrue(quoted_special.startswith('"') and quoted_special.endswith('"'))
        self.assertIn('\\"', quoted_special)
        self.assertIn("\\$", quoted_special)
        self.assertIn("\\`", quoted_special)
        self.assertIn("\\\\", quoted_special)

    def test_svg_icon_validity(self) -> None:
        """Verify scalable SVG icon is valid XML, standard dimensions, teal badge, and serif P."""
        self.assertTrue(SVG_FILE.is_file(), f"Missing SVG icon: {SVG_FILE}")

        svg_text = SVG_FILE.read_text(encoding="utf-8")
        root = ET.fromstring(svg_text)

        # Must be an SVG element
        self.assertTrue(
            root.tag.endswith("svg"), f"Root tag should be svg, got {root.tag}"
        )
        self.assertEqual(root.get("width"), "256")
        self.assertEqual(root.get("height"), "256")
        self.assertEqual(root.get("viewBox"), "0 0 256 256")

        # Color token checks matching src/paperassist/native/theme.py
        self.assertIn(TEAL, svg_text, f"SVG icon must use visual token TEAL ({TEAL})")
        self.assertIn(WHITE, svg_text, f"SVG icon must use text color WHITE ({WHITE})")

        # Check for badge rect and serif text
        rects = [e for e in root.iter() if e.tag.endswith("rect")]
        self.assertTrue(len(rects) >= 1, "SVG must contain badge rect")
        badge = rects[0]
        self.assertEqual(badge.get("fill"), TEAL)
        self.assertEqual(badge.get("rx"), "44")

        texts = [e for e in root.iter() if e.tag.endswith("text")]
        self.assertTrue(len(texts) >= 1, "SVG must contain P text mark")
        self.assertEqual(texts[0].text, "P")

    def test_png_icon_magic_and_dimensions(self) -> None:
        """Verify PNG icon has standard magic bytes, 256x256 dimensions, and visual tokens."""
        self.assertTrue(PNG_FILE.is_file(), f"Missing PNG icon: {PNG_FILE}")

        png_data = PNG_FILE.read_bytes()
        # Standard 8-byte PNG header: \x89PNG\r\n\x1a\n
        self.assertEqual(
            png_data[:8],
            b"\x89PNG\r\n\x1a\n",
            "File does not have PNG magic header",
        )

        # IHDR chunk dimensions (width and height at bytes 16-24)
        width, height = struct.unpack(">II", png_data[16:24])
        self.assertEqual(width, 256, f"Expected PNG width 256, got {width}")
        self.assertEqual(height, 256, f"Expected PNG height 256, got {height}")

        # Check using PySide6 QImage
        from PySide6.QtGui import QImage

        qimg = QImage(str(PNG_FILE))
        self.assertFalse(qimg.isNull(), "QImage failed to load PNG icon")
        self.assertEqual(qimg.width(), 256)
        self.assertEqual(qimg.height(), 256)
        self.assertTrue(qimg.hasAlphaChannel())

        # Top-left corner outside rounded rect is transparent
        self.assertEqual(qimg.pixelColor(0, 0).alpha(), 0)

        # Inside rounded badge is TEAL (#276c65)
        self.assertEqual(qimg.pixelColor(50, 50).name(), TEAL)

    def test_generate_icons_script(self) -> None:
        """Verify generate_icons.py generates both SVG and PNG deterministically."""
        self.assertTrue(GENERATE_SCRIPT.is_file())

        out_dir = self.test_root / "generated_icons"
        cmd = [
            sys.executable,
            str(GENERATE_SCRIPT),
            "--output-dir",
            str(out_dir),
        ]
        res = subprocess.run(cmd, env=self.env, capture_output=True, text=True)
        self.assertEqual(
            res.returncode,
            0,
            f"generate_icons.py failed:\n{res.stdout}\n{res.stderr}",
        )

        gen_svg = out_dir / "paperassist.svg"
        gen_png = out_dir / "paperassist.png"
        self.assertTrue(gen_svg.is_file())
        self.assertTrue(gen_png.is_file())

        # SVG must match exactly
        self.assertEqual(gen_svg.read_text(encoding="utf-8"), SVG_FILE.read_text(encoding="utf-8"))

        # PNG dimensions must match
        data = gen_png.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (256, 256))

    def _get_target_binary(self) -> tuple[list[str], dict[str, str]]:
        """Return ([executable, ...], env) targeting the frozen binary or native module."""
        env = self.env.copy()
        if FROZEN_BINARY.is_file() and os.access(FROZEN_BINARY, os.X_OK):
            return [str(FROZEN_BINARY)], env
        # Fallback to python -m paperassist.native
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        return [sys.executable, "-m", "paperassist.native"], env

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux launcher uses a POSIX shell")
    def test_launch_wrapper_in_spaced_path_from_unrelated_cwd(self) -> None:
        """Verify launching via a POSIX wrapper script in a spaced path from an unrelated CWD."""
        self.assertTrue(SAMPLE_PDF.is_file(), f"Sample PDF missing: {SAMPLE_PDF}")

        target_cmd, base_env = self._get_target_binary()

        spaced_dir = self.test_root / "dir with spaces" / "paperassist-bin"
        spaced_dir.mkdir(parents=True, exist_ok=True)
        wrapper_path = spaced_dir / "paperassist"

        escaped_target = " ".join(shlex.quote(arg) for arg in target_cmd)
        wrapper_content = f"""#!/bin/sh
exec {escaped_target} "$@"
"""
        wrapper_path.write_text(wrapper_content, encoding="utf-8")
        wrapper_path.chmod(0o755)

        # Unrelated current working directory
        unrelated_cwd = self.test_root / "completely unrelated cwd"
        unrelated_cwd.mkdir(parents=True, exist_ok=True)

        res = subprocess.run(
            [str(wrapper_path), "--self-test", str(SAMPLE_PDF)],
            cwd=str(unrelated_cwd),
            env=base_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(
            res.returncode,
            0,
            f"Wrapper launch failed from {unrelated_cwd}:\nstdout: {res.stdout}\nstderr: {res.stderr}",
        )
        self.assertIn("self-test ok", res.stdout)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Freedesktop launcher requires Linux")
    def test_desktop_exec_command_execution_from_unrelated_cwd(self) -> None:
        """Verify launching via desktop Exec= line quoting paths with spaces from an unrelated CWD."""
        target_cmd, base_env = self._get_target_binary()

        # Create wrapper at path with spaces
        spaced_dir = self.test_root / "app folder with spaces"
        spaced_dir.mkdir(parents=True, exist_ok=True)
        launcher = spaced_dir / "PaperAssist"

        escaped_target = " ".join(shlex.quote(arg) for arg in target_cmd)
        launcher.write_text(f'#!/bin/sh\nexec {escaped_target} "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)

        # Construct desktop entry Exec line with Freedesktop quoting
        exec_value = quote_exec_path(launcher)

        # Simulate desktop environment: parse Exec line per Freedesktop spec
        cmd = shlex.split(exec_value)
        cmd.extend(["--self-test", str(SAMPLE_PDF)])

        unrelated_cwd = self.test_root / "unrelated desktop cwd"
        unrelated_cwd.mkdir(parents=True, exist_ok=True)

        res = subprocess.run(
            cmd,
            cwd=str(unrelated_cwd),
            env=base_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(
            res.returncode,
            0,
            f"Desktop Exec command failed:\nstdout: {res.stdout}\nstderr: {res.stderr}",
        )
        self.assertIn("self-test ok", res.stdout)


if __name__ == "__main__":
    unittest.main()
