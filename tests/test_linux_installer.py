"""Tests for PaperAssist self-extracting Linux installer, upgrade, and uninstall."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add installer directory to sys.path so we can import build_installer
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packaging" / "installer"))

from build_installer import build_installer, assemble_installer, create_payload_archive


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux installer tests require Linux")
class LinuxInstallerTestCase(unittest.TestCase):
    """Test suite for PaperAssist Linux installer behaviors and safety guarantees."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self._temp_dir.name)
        self.home = self.test_root / "home"
        self.data_dir = self.home / ".local" / "share"
        self.config_dir = self.home / ".config"
        self.bin_dir = self.home / ".local" / "bin"

        self.home.mkdir(parents=True)
        self.data_dir.mkdir(parents=True)
        self.config_dir.mkdir(parents=True)
        self.bin_dir.mkdir(parents=True)

        self.base_env = os.environ.copy()
        self.base_env["HOME"] = str(self.home)
        self.base_env["XDG_DATA_HOME"] = str(self.data_dir)
        self.base_env["XDG_CONFIG_HOME"] = str(self.config_dir)
        self.base_env["XDG_BIN_HOME"] = str(self.bin_dir)

    def tearDown(self):
        self._temp_dir.cleanup()

    def _create_synthetic_bundle(
        self,
        name: str = "bundle",
        version: str = "0.1.0a0",
        notices: str = "Synthetic notices",
        broken: bool = False,
        non_executable: bool = False,
        missing_license: bool = False,
    ) -> Path:
        """Create a synthetic PyInstaller one-dir bundle."""
        bundle_path = self.test_root / name
        bundle_path.mkdir(parents=True, exist_ok=True)

        app_sh = bundle_path / "PaperAssist"
        if broken:
            app_sh.write_text("#!/bin/sh\nexit 1\n")
        else:
            app_sh.write_text(
                f"#!/bin/sh\n"
                f'if [ "$1" = "--version" ]; then echo "PaperAssist {version}"; exit 0; fi\n'
                f'if [ "$1" = "--help" ]; then echo "PaperAssist usage"; exit 0; fi\n'
                f'echo "PaperAssist {version} running: $@"\n'
            )

        if non_executable:
            app_sh.chmod(0o644)
        else:
            app_sh.chmod(0o755)

        internal = bundle_path / "_internal"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "fake_qt.so.6.12.0").write_bytes(b"\x7fELFfakeqt")
        (internal / "fake_qt.so.6").symlink_to("fake_qt.so.6.12.0")
        (internal / "version.txt").write_text(version)

        if not missing_license:
            (bundle_path / "LICENSE").write_text("GNU GPL-3.0-or-later")

        (bundle_path / "THIRD-PARTY-NOTICES.txt").write_text(notices)
        return bundle_path

    def _create_desktop_assets(self, name: str = "desktop") -> Path:
        """Create synthetic desktop integration assets."""
        desktop_path = self.test_root / name
        desktop_path.mkdir(parents=True, exist_ok=True)

        (desktop_path / "paperassist.desktop").write_text(
            "[Desktop Entry]\n"
            "Name=PaperAssist\n"
            "Comment=Local research paper reader\n"
            "Exec=PaperAssist %F\n"
            "Icon=paperassist\n"
            "Terminal=false\n"
            "Type=Application\n"
            "Categories=Office;Viewer;\n"
        )
        (desktop_path / "paperassist.svg").write_text("<svg>synthetic_icon_svg</svg>")
        (desktop_path / "paperassist.png").write_bytes(b"\x89PNGsynthetic_icon_png")
        return desktop_path

    def _build_test_installer(
        self,
        bundle_dir: Path,
        installer_name: str = "installer.sh",
        desktop_dir: Path | None = None,
        version: str = "0.1.0a0",
    ) -> Path:
        installer_path = self.test_root / installer_name
        build_installer(
            bundle_dir=bundle_dir,
            output_path=installer_path,
            desktop_dir=desktop_dir,
            version=version,
        )
        return installer_path

    def test_fresh_install_and_verification(self):
        """Test fresh install, --verify option, CLI launcher execution, and uninstall.sh."""
        bundle = self._create_synthetic_bundle("b_fresh", version="0.1.0a0")
        desktop = self._create_desktop_assets("d_fresh")
        installer = self._build_test_installer(bundle, "installer_fresh.sh", desktop, "0.1.0a0")

        # 1. Verify flag checks integrity and exits without creating any files
        res_verify = subprocess.run(
            [str(installer), "--verify"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_verify.returncode, 0)
        self.assertIn("Package integrity verified", res_verify.stdout)
        self.assertFalse((self.data_dir / "paperassist-app").exists())
        self.assertFalse((self.bin_dir / "paperassist").exists())

        # 2. Fresh installation
        res_install = subprocess.run(
            [str(installer)],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_install.returncode, 0, res_install.stderr)
        self.assertIn("installed successfully", res_install.stdout)

        # 3. Check installed paths and permissions
        app_dir = self.data_dir / "paperassist-app"
        self.assertTrue(app_dir.is_dir())
        self.assertTrue((app_dir / "PaperAssist").is_file())
        self.assertTrue(os.access(app_dir / "PaperAssist", os.X_OK))
        self.assertTrue((app_dir / "LICENSE").is_file())
        self.assertTrue((app_dir / "_internal").is_dir())

        uninstaller = app_dir / "uninstall.sh"
        self.assertTrue(uninstaller.is_file())
        self.assertTrue(os.access(uninstaller, os.X_OK))

        manifest = app_dir / ".paperassist-install-manifest"
        self.assertTrue(manifest.is_file())
        manifest_text = manifest.read_text()
        self.assertIn("Managed-By: PaperAssist-Linux-Installer", manifest_text)
        self.assertIn("VERSION=0.1.0a0", manifest_text)

        # CLI launcher
        launcher = self.bin_dir / "paperassist"
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        launcher_text = launcher.read_text()
        self.assertIn("# Managed-By: PaperAssist-Linux-Installer", launcher_text)

        # Register a supported handler without changing the user's default application.
        desktop_entry = self.data_dir / "applications" / "paperassist.desktop"
        self.assertTrue(desktop_entry.is_file())
        desktop_text = desktop_entry.read_text()
        self.assertIn("# Managed-By: PaperAssist-Linux-Installer", desktop_text)
        self.assertIn(f'Exec="{launcher}" %F\n', desktop_text)
        self.assertIn("MimeType=application/pdf;", desktop_text)
        self.assertNotIn("%f", desktop_text)
        self.assertTrue((app_dir / "_internal" / "fake_qt.so.6").is_symlink())

        svg_icon = self.data_dir / "icons" / "hicolor" / "scalable" / "apps" / "paperassist.svg"
        png_icon = self.data_dir / "icons" / "hicolor" / "256x256" / "apps" / "paperassist.png"
        self.assertTrue(svg_icon.is_file())
        self.assertTrue(png_icon.is_file())

        # 4. Run launcher from unrelated working directory
        res_run = subprocess.run(
            [str(launcher), "--version"],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_run.returncode, 0)
        self.assertIn("PaperAssist 0.1.0a0", res_run.stdout)

        res_args = subprocess.run(
            [str(launcher), "doc.pdf", "--data-dir", "/tmp/somedir"],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_args.returncode, 0)
        self.assertIn("doc.pdf --data-dir /tmp/somedir", res_args.stdout)

    def test_paths_with_spaces(self):
        """Test installer and launcher when paths contain spaces."""
        space_root = self.test_root / "space path root"
        space_root.mkdir()

        bundle = self._create_synthetic_bundle("bundle with spaces", version="0.1.0a1")
        desktop = self._create_desktop_assets("desktop with spaces")

        installer_dir = space_root / "installer dir with spaces"
        installer_dir.mkdir()
        installer = installer_dir / "PaperAssist-0.1.0a1-linux-x86_64.sh"
        build_installer(bundle, installer, desktop, "0.1.0a1")

        custom_home = space_root / "user home"
        custom_install = space_root / "paperassist app dir"
        custom_bin = space_root / "my custom bin"

        env = self.base_env.copy()
        env["HOME"] = str(custom_home)
        env["XDG_DATA_HOME"] = str(custom_home / "data dir")
        env["XDG_CONFIG_HOME"] = str(custom_home / "config dir")
        env["XDG_BIN_HOME"] = str(custom_bin)

        # Run installer pointing to paths with spaces
        res_inst = subprocess.run(
            [
                "sh",
                str(installer),
                "--install-dir",
                str(custom_install),
                "--bin-dir",
                str(custom_bin),
            ],
            env=env,
            cwd=str(self.test_root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_inst.returncode, 0, res_inst.stderr)

        launcher = custom_bin / "paperassist"
        self.assertTrue(launcher.is_file())

        # Execute launcher with arguments containing spaces
        res_run = subprocess.run(
            [str(launcher), "my research paper.pdf", "second arg"],
            cwd="/tmp",
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_run.returncode, 0, res_run.stderr)
        self.assertIn("my research paper.pdf second arg", res_run.stdout)

        # File arguments remain outside the quoted executable path.
        desktop_file = custom_home / "data dir" / "applications" / "paperassist.desktop"
        self.assertTrue(desktop_file.is_file())
        self.assertIn(f'Exec="{launcher}" %F\n', desktop_file.read_text())

    def test_idempotent_repeated_installation(self):
        """Test that re-running the same installer over an existing installation succeeds cleanly."""
        bundle = self._create_synthetic_bundle("b_idem", version="0.1.0a1")
        desktop = self._create_desktop_assets("d_idem")
        installer = self._build_test_installer(bundle, "installer_idem.sh", desktop, "0.1.0a1")

        # First install
        res1 = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res1.returncode, 0, res1.stderr)

        # Second install of the exact same package
        res2 = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res2.returncode, 0, res2.stderr)
        self.assertIn("installed successfully", res2.stdout)

        launcher = self.bin_dir / "paperassist"
        res_run = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertEqual(res_run.returncode, 0)
        self.assertIn("PaperAssist 0.1.0a1", res_run.stdout)

    def test_upgrade_from_v0_to_v1(self):
        """Test upgrading from 0.1.0a0 to 0.1.0a1 updates binary, notices, and manifest."""
        b0 = self._create_synthetic_bundle("b_v0", version="0.1.0a0", notices="Old Notices v0")
        b1 = self._create_synthetic_bundle("b_v1", version="0.1.0a1", notices="New Notices v1")
        desktop = self._create_desktop_assets("d_up")

        inst0 = self._build_test_installer(b0, "inst_v0.sh", desktop, "0.1.0a0")
        inst1 = self._build_test_installer(b1, "inst_v1.sh", desktop, "0.1.0a1")

        # 1. Install v0
        res0 = subprocess.run([str(inst0)], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res0.returncode, 0, res0.stderr)

        launcher = self.bin_dir / "paperassist"
        res_v0 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_v0.stdout)

        app_dir = self.data_dir / "paperassist-app"
        self.assertEqual((app_dir / "THIRD-PARTY-NOTICES.txt").read_text(), "Old Notices v0")

        # 2. Upgrade to v1
        res1 = subprocess.run([str(inst1)], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res1.returncode, 0, res1.stderr)

        # Verify upgraded version
        res_v1 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a1", res_v1.stdout)

        self.assertEqual((app_dir / "THIRD-PARTY-NOTICES.txt").read_text(), "New Notices v1")
        manifest_text = (app_dir / ".paperassist-install-manifest").read_text()
        self.assertIn("VERSION=0.1.0a1", manifest_text)

    def test_failed_upgrade_corrupted_or_truncated_package(self):
        """Test that corrupted or truncated packages abort before modifying existing install."""
        b0 = self._create_synthetic_bundle("b_cor_v0", version="0.1.0a0")
        b1 = self._create_synthetic_bundle("b_cor_v1", version="0.1.0a1")
        inst0 = self._build_test_installer(b0, "inst_cor_v0.sh", None, "0.1.0a0")
        inst1 = self._build_test_installer(b1, "inst_cor_v1.sh", None, "0.1.0a1")

        # Install v0
        subprocess.run([str(inst0)], env=self.base_env, check=True)
        launcher = self.bin_dir / "paperassist"

        orig_inst1_bytes = inst1.read_bytes()

        # 1. Truncated package
        trunc_installer = self.test_root / "inst_trunc.sh"
        trunc_installer.write_bytes(orig_inst1_bytes[:-500])
        trunc_installer.chmod(0o755)

        res_trunc = subprocess.run([str(trunc_installer)], env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(res_trunc.returncode, 0)
        self.assertIn("truncated", res_trunc.stderr)

        # Existing v0 installation must be intact
        res_check1 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_check1.stdout)

        # 2. Corrupted payload byte
        corrupt_installer = self.test_root / "inst_corrupt.sh"
        corrupt_bytes = bytearray(orig_inst1_bytes)
        corrupt_bytes[-50] = (corrupt_bytes[-50] + 1) % 256
        corrupt_installer.write_bytes(bytes(corrupt_bytes))
        corrupt_installer.chmod(0o755)

        res_corrupt = subprocess.run([str(corrupt_installer)], env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(res_corrupt.returncode, 0)
        self.assertIn("Checksum verification failed", res_corrupt.stderr)

        # Existing v0 installation must be intact
        res_check2 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_check2.stdout)

    def test_failed_upgrade_broken_or_nonexecutable_payload(self):
        """Test that non-executable or broken payloads abort before touching existing install."""
        b0 = self._create_synthetic_bundle("b_brk_v0", version="0.1.0a0")
        b_broken = self._create_synthetic_bundle("b_brk_v1", version="0.1.0a1", broken=True)
        b_nonexec = self._create_synthetic_bundle("b_non_v1", version="0.1.0a1", non_executable=True)

        inst0 = self._build_test_installer(b0, "inst_brk_v0.sh", None, "0.1.0a0")
        inst_broken = self._build_test_installer(b_broken, "inst_brk_v1.sh", None, "0.1.0a1")
        inst_nonexec = self._build_test_installer(b_nonexec, "inst_non_v1.sh", None, "0.1.0a1")

        # Install v0
        subprocess.run([str(inst0)], env=self.base_env, check=True)
        launcher = self.bin_dir / "paperassist"

        # Try broken upgrade
        res_brk = subprocess.run([str(inst_broken)], env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(res_brk.returncode, 0)
        self.assertIn("execution sanity check", res_brk.stderr)

        # v0 still working
        res_check1 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_check1.stdout)

        # Try non-executable upgrade
        res_non = subprocess.run([str(inst_nonexec)], env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(res_non.returncode, 0)
        self.assertIn("missing or not executable", res_non.stderr)

        # v0 still working
        res_check2 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_check2.stdout)

    def test_failed_upgrade_rollback_on_readonly_bindir(self):
        """Test rollback when writing launcher fails due to read-only BIN_DIR."""
        b0 = self._create_synthetic_bundle("b_ro_v0", version="0.1.0a0")
        b1 = self._create_synthetic_bundle("b_ro_v1", version="0.1.0a1")

        inst0 = self._build_test_installer(b0, "inst_ro_v0.sh", None, "0.1.0a0")
        inst1 = self._build_test_installer(b1, "inst_ro_v1.sh", None, "0.1.0a1")

        subprocess.run([str(inst0)], env=self.base_env, check=True)
        launcher = self.bin_dir / "paperassist"

        # Make BIN_DIR read-only
        self.bin_dir.chmod(0o555)
        try:
            res_upgrade = subprocess.run([str(inst1)], env=self.base_env, capture_output=True, text=True)
            self.assertNotEqual(res_upgrade.returncode, 0)
            self.assertIn("Rolling back", res_upgrade.stderr)
        finally:
            self.bin_dir.chmod(0o755)

        # v0 must still be present and runnable
        res_check = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertEqual(res_check.returncode, 0)
        self.assertIn("PaperAssist 0.1.0a0", res_check.stdout)

    def test_failed_upgrade_fault_injections_rollback(self):
        """Test rollback handlers under fault injection after_stage, after_backup, launcher_write."""
        b0 = self._create_synthetic_bundle("b_fi_v0", version="0.1.0a0")
        b1 = self._create_synthetic_bundle("b_fi_v1", version="0.1.0a1")

        inst0 = self._build_test_installer(b0, "inst_fi_v0.sh", None, "0.1.0a0")
        inst1 = self._build_test_installer(b1, "inst_fi_v1.sh", None, "0.1.0a1")

        subprocess.run([str(inst0)], env=self.base_env, check=True)
        launcher = self.bin_dir / "paperassist"

        # 1. Fault: after_stage
        env_fault1 = self.base_env.copy()
        env_fault1["__PAPERASSIST_INSTALLER_FAULT_INJECT"] = "after_stage"
        res1 = subprocess.run([str(inst1)], env=env_fault1, capture_output=True, text=True)
        self.assertNotEqual(res1.returncode, 0)
        res_v0 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_v0.stdout)

        # 2. Fault: after_backup
        env_fault2 = self.base_env.copy()
        env_fault2["__PAPERASSIST_INSTALLER_FAULT_INJECT"] = "after_backup"
        res2 = subprocess.run([str(inst1)], env=env_fault2, capture_output=True, text=True)
        self.assertNotEqual(res2.returncode, 0)
        self.assertIn("Rolling back", res2.stderr)
        res_v0_2 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_v0_2.stdout)

        # 3. Fault: launcher_write
        env_fault3 = self.base_env.copy()
        env_fault3["__PAPERASSIST_INSTALLER_FAULT_INJECT"] = "launcher_write"
        res3 = subprocess.run([str(inst1)], env=env_fault3, capture_output=True, text=True)
        self.assertNotEqual(res3.returncode, 0)
        self.assertIn("Rolling back", res3.stderr)
        res_v0_3 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_v0_3.stdout)

    def test_recovery_after_simulated_sigkill(self):
        """Test auto-recovery and --repair when a prior run was interrupted by SIGKILL."""
        b0 = self._create_synthetic_bundle("b_kill_v0", version="0.1.0a0")
        inst0 = self._build_test_installer(b0, "inst_kill_v0.sh", None, "0.1.0a0")

        subprocess.run([str(inst0)], env=self.base_env, check=True)
        launcher = self.bin_dir / "paperassist"
        app_dir = self.data_dir / "paperassist-app"

        # Simulate SIGKILL right after moving INSTALL_DIR -> INSTALL_DIR.backup.999
        backup_dir = app_dir.with_name("paperassist-app.backup.999")
        app_dir.rename(backup_dir)

        # Also create a stale staging directory from killed extraction
        staging_dir = app_dir.with_name("paperassist-app.staging.888")
        staging_dir.mkdir()
        (staging_dir / ".paperassist-staging-marker").write_text(
            "# Managed-By: PaperAssist-Linux-Installer\nPID=888\n"
        )
        (staging_dir / "partial.bin").write_bytes(b"corrupted")

        self.assertFalse(app_dir.exists())
        self.assertTrue(backup_dir.is_dir())
        self.assertTrue(staging_dir.is_dir())

        # Run with --repair
        res_repair = subprocess.run([str(inst0), "--repair"], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res_repair.returncode, 0, res_repair.stderr)
        self.assertIn("Repair successful", res_repair.stdout)

        self.assertTrue(app_dir.is_dir())
        self.assertFalse(backup_dir.exists())
        self.assertFalse(staging_dir.exists())

        res_run = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
        self.assertIn("PaperAssist 0.1.0a0", res_run.stdout)

    def test_refusal_on_foreign_and_library_files(self):
        """Test installer safety: refuse to overwrite library, foreign launcher, desktop, or icon."""
        bundle = self._create_synthetic_bundle("b_forg", version="0.1.0a1")
        desktop = self._create_desktop_assets("d_forg")
        installer = self._build_test_installer(bundle, "inst_forg.sh", desktop, "0.1.0a1")

        # 1. Refuse if --install-dir contains library.sqlite3
        lib_dir = self.test_root / "research_library"
        lib_dir.mkdir()
        (lib_dir / "library.sqlite3").write_bytes(b"SQLite format 3")

        res_lib = subprocess.run(
            [str(installer), "--install-dir", str(lib_dir)],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_lib.returncode, 0)
        self.assertIn("library catalog (library.sqlite3)", res_lib.stderr)

        # 2. Refuse if --install-dir is non-empty unmanaged
        unmanaged_dir = self.test_root / "unmanaged_app"
        unmanaged_dir.mkdir()
        (unmanaged_dir / "other_software.txt").write_text("not paperassist")

        res_unm = subprocess.run(
            [str(installer), "--install-dir", str(unmanaged_dir)],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_unm.returncode, 0)
        self.assertIn("no PaperAssist install manifest", res_unm.stderr)

        # 3. Refuse if foreign launcher exists (or dangling symlink)
        foreign_launcher = self.bin_dir / "paperassist"
        foreign_launcher.write_text("#!/bin/sh\necho foreign\n")

        res_flauncher = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(res_flauncher.returncode, 0)
        self.assertIn("not managed by PaperAssist-Linux-Installer", res_flauncher.stderr)
        foreign_launcher.unlink()

        # 4. Refuse if foreign desktop entry exists
        app_menu_dir = self.data_dir / "applications"
        app_menu_dir.mkdir(parents=True, exist_ok=True)
        foreign_desktop = app_menu_dir / "paperassist.desktop"
        foreign_desktop.write_text("[Desktop Entry]\nName=Custom\n")

        res_fdesktop = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(res_fdesktop.returncode, 0)
        self.assertIn("not managed by PaperAssist-Linux-Installer", res_fdesktop.stderr)
        foreign_desktop.unlink()

        # 5. Refuse if foreign icon exists
        svg_icon = self.data_dir / "icons" / "hicolor" / "scalable" / "apps" / "paperassist.svg"
        svg_icon.parent.mkdir(parents=True, exist_ok=True)
        svg_icon.write_text("<svg>foreign_icon</svg>")

        res_ficon = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(res_ficon.returncode, 0)
        self.assertIn("was not installed by PaperAssist", res_ficon.stderr)
        svg_icon.unlink()

    def test_uninstall_preserves_user_data_and_foreign_files(self):
        """Test uninstalling via uninstall.sh preserves user library and foreign launcher."""
        bundle = self._create_synthetic_bundle("b_uninst", version="0.1.0a1")
        desktop = self._create_desktop_assets("d_uninst")
        installer = self._build_test_installer(bundle, "inst_uninst.sh", desktop, "0.1.0a1")

        # 1. Install
        subprocess.run([str(installer)], env=self.base_env, check=True)

        app_dir = self.data_dir / "paperassist-app"
        launcher = self.bin_dir / "paperassist"
        desktop_file = self.data_dir / "applications" / "paperassist.desktop"
        svg_icon = self.data_dir / "icons" / "hicolor" / "scalable" / "apps" / "paperassist.svg"

        self.assertTrue(app_dir.is_dir())
        self.assertTrue(launcher.is_file())
        self.assertTrue(desktop_file.is_file())
        self.assertTrue(svg_icon.is_file())

        # 2. Populate user library and configuration
        user_library = self.data_dir / "paperassist"
        user_library.mkdir(parents=True, exist_ok=True)
        (user_library / "library.sqlite3").write_bytes(b"SQLITE_RESEARCH_CATALOG_CONTENT")
        (user_library / "originals").mkdir(exist_ok=True)
        (user_library / "originals" / "paper1.pdf").write_bytes(b"%PDF-1.4 raw binary original")
        (user_library / "working").mkdir(exist_ok=True)
        (user_library / "working" / "paper1_rev.pdf").write_bytes(b"%PDF-1.4 annotated copy")
        (user_library / "settings.json").write_text('{"sidebar_width": 250}')

        user_config = self.config_dir / "paperassist"
        user_config.mkdir(parents=True, exist_ok=True)
        (user_config / "config.json").write_text(f'{{"library": "{user_library}"}}')

        # Snapshot byte contents
        expected_sql = (user_library / "library.sqlite3").read_bytes()
        expected_orig = (user_library / "originals" / "paper1.pdf").read_bytes()
        expected_rev = (user_library / "working" / "paper1_rev.pdf").read_bytes()
        expected_settings = (user_library / "settings.json").read_bytes()
        expected_cfg = (user_config / "config.json").read_bytes()

        # 3. User replaces ~/.local/bin/paperassist with custom wrapper
        launcher.write_text("#!/bin/sh\necho my-custom-wrapper\n")
        expected_launcher_content = launcher.read_text()

        # 4. Run uninstall.sh from inside app directory
        uninstaller = app_dir / "uninstall.sh"
        res_un = subprocess.run([str(uninstaller)], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res_un.returncode, 0, res_un.stderr)
        self.assertIn("uninstalled successfully", res_un.stdout)

        # 5. Verify application files were deleted
        self.assertFalse(app_dir.exists())
        self.assertFalse(desktop_file.exists())
        self.assertFalse(svg_icon.exists())

        # Verify foreign launcher was preserved
        self.assertTrue(launcher.is_file())
        self.assertEqual(launcher.read_text(), expected_launcher_content)

        # Verify user data preserved byte-for-byte
        self.assertEqual((user_library / "library.sqlite3").read_bytes(), expected_sql)
        self.assertEqual((user_library / "originals" / "paper1.pdf").read_bytes(), expected_orig)
        self.assertEqual((user_library / "working" / "paper1_rev.pdf").read_bytes(), expected_rev)
        self.assertEqual((user_library / "settings.json").read_bytes(), expected_settings)
        self.assertEqual((user_config / "config.json").read_bytes(), expected_cfg)

    def test_uninstall_via_installer_flag(self):
        """Test uninstalling via installer.sh --uninstall."""
        bundle = self._create_synthetic_bundle("b_unflag", version="0.1.0a1")
        installer = self._build_test_installer(bundle, "inst_unflag.sh", None, "0.1.0a1")

        subprocess.run([str(installer)], env=self.base_env, check=True)
        app_dir = self.data_dir / "paperassist-app"
        launcher = self.bin_dir / "paperassist"
        self.assertTrue(app_dir.is_dir())
        self.assertTrue(launcher.is_file())

        res_un = subprocess.run(
            [str(installer), "--uninstall"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_un.returncode, 0, res_un.stderr)
        self.assertIn("uninstalled successfully", res_un.stdout)
        self.assertFalse(app_dir.exists())
        self.assertFalse(launcher.exists())

    def test_refusal_when_running_as_root_without_flag(self):
        """Test that running as root (id -u == 0) is refused unless --allow-root is passed."""
        bundle = self._create_synthetic_bundle("b_root", version="0.1.0a1")
        installer = self._build_test_installer(bundle, "inst_root.sh", None, "0.1.0a1")

        mock_bin = self.test_root / "mock_root_bin"
        mock_bin.mkdir()
        mock_id = mock_bin / "id"
        mock_id.write_text("#!/bin/sh\necho 0\n")
        mock_id.chmod(0o755)

        root_env = self.base_env.copy()
        root_env["PATH"] = f"{mock_bin}:{root_env.get('PATH', '')}"

        # 1. Refusal without --allow-root
        res_refuse = subprocess.run([str(installer)], env=root_env, capture_output=True, text=True)
        self.assertNotEqual(res_refuse.returncode, 0)
        self.assertIn("installed per-user without sudo", res_refuse.stderr)

        # 2. Permitted with --allow-root
        res_allow = subprocess.run(
            [str(installer), "--allow-root", "--verify"],
            env=root_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_allow.returncode, 0)
        self.assertIn("Package integrity verified", res_allow.stdout)

    def test_rejected_library_and_foreign_targets_leave_adjacent_files_untouched(self):
        """Test rejection of library catalog or foreign directories leaves all adjacent backup/staging untouched."""
        bundle = self._create_synthetic_bundle("b_rej", version="0.1.0a1")
        installer = self._build_test_installer(bundle, "inst_rej.sh", None, "0.1.0a1")

        # 1. Target with library.sqlite3 and adjacent unowned .backup.* and .staging.*
        library_dir = self.test_root / "research-library"
        library_dir.mkdir()
        (library_dir / "library.sqlite3").write_bytes(b"catalog sentinel content")
        (library_dir / "paper.pdf").write_bytes(b"valuable user paper")

        adj_backup = library_dir.with_name(library_dir.name + ".backup.2026")
        adj_backup.mkdir()
        backup_sentinel = adj_backup / "original.pdf"
        backup_sentinel.write_bytes(b"private PDF backup sentinel")

        adj_staging = library_dir.with_name(library_dir.name + ".staging.2026")
        adj_staging.mkdir()
        staging_sentinel = adj_staging / "draft.pdf"
        staging_sentinel.write_bytes(b"draft PDF staging sentinel")

        expected_lib_catalog = (library_dir / "library.sqlite3").read_bytes()
        expected_lib_paper = (library_dir / "paper.pdf").read_bytes()
        expected_backup = backup_sentinel.read_bytes()
        expected_staging = staging_sentinel.read_bytes()

        # Run installer -> must fail, leave everything unchanged
        res_inst = subprocess.run(
            [str(installer), "--install-dir", str(library_dir), "--no-launcher", "--no-desktop"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_inst.returncode, 0)
        self.assertIn("library catalog (library.sqlite3)", res_inst.stderr)
        self.assertEqual((library_dir / "library.sqlite3").read_bytes(), expected_lib_catalog)
        self.assertEqual((library_dir / "paper.pdf").read_bytes(), expected_lib_paper)
        self.assertEqual(backup_sentinel.read_bytes(), expected_backup)
        self.assertEqual(staging_sentinel.read_bytes(), expected_staging)

        # Run --repair -> must fail, leave everything unchanged
        res_rep = subprocess.run(
            [str(installer), "--install-dir", str(library_dir), "--repair"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_rep.returncode, 0)
        self.assertIn("library catalog (library.sqlite3)", res_rep.stderr)
        self.assertEqual((library_dir / "library.sqlite3").read_bytes(), expected_lib_catalog)
        self.assertEqual(backup_sentinel.read_bytes(), expected_backup)
        self.assertEqual(staging_sentinel.read_bytes(), expected_staging)

        # Run --uninstall -> must fail, leave everything unchanged
        res_un = subprocess.run(
            [str(installer), "--install-dir", str(library_dir), "--uninstall"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_un.returncode, 0)
        self.assertIn("library catalog (library.sqlite3)", res_un.stderr)
        self.assertEqual((library_dir / "library.sqlite3").read_bytes(), expected_lib_catalog)
        self.assertEqual(backup_sentinel.read_bytes(), expected_backup)
        self.assertEqual(staging_sentinel.read_bytes(), expected_staging)

        # 2. Target with foreign unmanaged files and adjacent unowned .backup.* and .staging.*
        foreign_dir = self.test_root / "foreign-app"
        foreign_dir.mkdir()
        (foreign_dir / "custom.txt").write_bytes(b"custom third-party files")

        foreign_backup = foreign_dir.with_name(foreign_dir.name + ".backup.2026")
        foreign_backup.mkdir()
        foreign_backup_file = foreign_backup / "backup.txt"
        foreign_backup_file.write_bytes(b"foreign backup text")

        foreign_staging = foreign_dir.with_name(foreign_dir.name + ".staging.2026")
        foreign_staging.mkdir()
        foreign_staging_file = foreign_staging / "staging.txt"
        foreign_staging_file.write_bytes(b"foreign staging text")

        expected_foreign_custom = (foreign_dir / "custom.txt").read_bytes()
        expected_foreign_backup = foreign_backup_file.read_bytes()
        expected_foreign_staging = foreign_staging_file.read_bytes()

        # Run installer
        res_f_inst = subprocess.run(
            [str(installer), "--install-dir", str(foreign_dir), "--no-launcher", "--no-desktop"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_f_inst.returncode, 0)
        self.assertEqual((foreign_dir / "custom.txt").read_bytes(), expected_foreign_custom)
        self.assertEqual(foreign_backup_file.read_bytes(), expected_foreign_backup)
        self.assertEqual(foreign_staging_file.read_bytes(), expected_foreign_staging)

        # Run --repair
        res_f_rep = subprocess.run(
            [str(installer), "--install-dir", str(foreign_dir), "--repair"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_f_rep.returncode, 0)
        self.assertEqual((foreign_dir / "custom.txt").read_bytes(), expected_foreign_custom)
        self.assertEqual(foreign_backup_file.read_bytes(), expected_foreign_backup)
        self.assertEqual(foreign_staging_file.read_bytes(), expected_foreign_staging)

        # Run --uninstall
        res_f_un = subprocess.run(
            [str(installer), "--install-dir", str(foreign_dir), "--uninstall"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_f_un.returncode, 0)
        self.assertEqual((foreign_dir / "custom.txt").read_bytes(), expected_foreign_custom)
        self.assertEqual(foreign_backup_file.read_bytes(), expected_foreign_backup)
        self.assertEqual(foreign_staging_file.read_bytes(), expected_foreign_staging)

    def test_valid_operations_preserve_adjacent_unowned_backups_and_staging(self):
        """Test that install, upgrade, and repair preserve unowned adjacent backup/staging directories."""
        bundle0 = self._create_synthetic_bundle("b_adj0", version="0.1.0a0")
        bundle1 = self._create_synthetic_bundle("b_adj1", version="0.1.0a1")
        inst0 = self._build_test_installer(bundle0, "inst_adj0.sh", None, "0.1.0a0")
        inst1 = self._build_test_installer(bundle1, "inst_adj1.sh", None, "0.1.0a1")

        # 1. Fresh install of v0
        subprocess.run([str(inst0)], env=self.base_env, check=True)
        app_dir = self.data_dir / "paperassist-app"

        # 2. User places unowned backup and staging directories adjacent to paperassist-app
        user_backup = app_dir.with_name(app_dir.name + ".backup.user_manual_copy")
        user_backup.mkdir()
        (user_backup / "manual_backup.txt").write_bytes(b"unrelated user manual backup content")

        user_staging = app_dir.with_name(app_dir.name + ".staging.user_notes")
        user_staging.mkdir()
        (user_staging / "user_notes.txt").write_bytes(b"unrelated user staging notes content")

        expected_backup = (user_backup / "manual_backup.txt").read_bytes()
        expected_staging = (user_staging / "user_notes.txt").read_bytes()

        # 3. Upgrade to v1
        res_up = subprocess.run([str(inst1)], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res_up.returncode, 0, res_up.stderr)

        self.assertTrue(user_backup.is_dir())
        self.assertEqual((user_backup / "manual_backup.txt").read_bytes(), expected_backup)
        self.assertTrue(user_staging.is_dir())
        self.assertEqual((user_staging / "user_notes.txt").read_bytes(), expected_staging)

        # 4. Repair v1
        res_rep = subprocess.run([str(inst1), "--repair"], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res_rep.returncode, 0, res_rep.stderr)

        self.assertTrue(user_backup.is_dir())
        self.assertEqual((user_backup / "manual_backup.txt").read_bytes(), expected_backup)
        self.assertTrue(user_staging.is_dir())
        self.assertEqual((user_staging / "user_notes.txt").read_bytes(), expected_staging)

    def test_post_swap_crash_and_reproduce_audit_scenario(self):
        """Test repair recovers previous valid installation after an interrupted swap (defect 2)."""
        bundle0 = self._create_synthetic_bundle("b_audit0", version="0.1.0a0")
        bundle1 = self._create_synthetic_bundle("b_audit1", version="0.1.0a1")
        inst0 = self._build_test_installer(bundle0, "inst_audit0.sh", None, "0.1.0a0")
        inst1 = self._build_test_installer(bundle1, "inst_audit1.sh", None, "0.1.0a1")

        # 1. Install v0
        res = subprocess.run(
            [str(inst0), "--no-launcher", "--no-desktop"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)

        app = self.data_dir / "paperassist-app"
        backup = app.with_name(app.name + ".backup.999")
        app.rename(backup)
        shutil.copytree(bundle1, app, symlinks=True)

        # Precondition from reproduce_recovery.py:
        # app exists without manifest, backup has valid previous installation
        self.assertTrue(app.is_dir())
        self.assertFalse((app / ".paperassist-install-manifest").exists())
        self.assertTrue(backup.is_dir())
        self.assertTrue((backup / ".paperassist-install-manifest").exists())

        # 2. Run --repair -> must NOT delete backup, must recover previous installation!
        res_rep = subprocess.run(
            [str(inst0), "--repair", "--no-launcher", "--no-desktop"],
            env=self.base_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_rep.returncode, 0, res_rep.stderr)
        self.assertIn("Repair successful", res_rep.stdout)

        # Recovered app must have manifest and execute v0
        self.assertTrue((app / ".paperassist-install-manifest").exists())
        res_check = subprocess.run([str(app / "PaperAssist"), "--version"], capture_output=True, text=True)
        self.assertEqual(res_check.returncode, 0)
        self.assertIn("PaperAssist 0.1.0a0", res_check.stdout)

    def test_all_cut_points_sigkill_and_repair(self):
        """Test all 6 transaction cut points interrupted by SIGKILL and recovered by --repair."""
        bundle0 = self._create_synthetic_bundle("b_cut0", version="0.1.0a0")
        bundle1 = self._create_synthetic_bundle("b_cut1", version="0.1.0a1")
        desktop = self._create_desktop_assets("d_cut")
        inst0 = self._build_test_installer(bundle0, "inst_cut0.sh", desktop, "0.1.0a0")
        inst1 = self._build_test_installer(bundle1, "inst_cut1.sh", desktop, "0.1.0a1")
        launcher = self.bin_dir / "paperassist"
        app_dir = self.data_dir / "paperassist-app"

        cut_points = [
            "during_extraction",
            "after_stage",
            "after_backup",
            "after_swap_before_manifest",
            "during_integration",
            "after_commit_before_cleanup",
        ]

        for cut_point in cut_points:
            with self.subTest(cut_point=cut_point):
                # Clean up previous state
                if app_dir.exists():
                    shutil.rmtree(app_dir)
                for p in self.data_dir.glob("paperassist-app.*"):
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
                if launcher.exists():
                    launcher.unlink()
                if (self.data_dir / "icons").exists():
                    shutil.rmtree(self.data_dir / "icons")
                if (self.data_dir / "applications").exists():
                    shutil.rmtree(self.data_dir / "applications")

                # 1. Install base v0
                res0 = subprocess.run([str(inst0)], env=self.base_env, capture_output=True, text=True)
                self.assertEqual(res0.returncode, 0, res0.stderr)

                res_v0 = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
                self.assertIn("PaperAssist 0.1.0a0", res_v0.stdout)

                # 2. Trigger fault injection with SIGKILL
                env_kill = self.base_env.copy()
                env_kill["__PAPERASSIST_INSTALLER_FAULT_INJECT"] = cut_point
                env_kill["__PAPERASSIST_INSTALLER_CRASH_SIGNAL"] = "KILL"

                proc_kill = subprocess.run([str(inst1)], env=env_kill, capture_output=True, text=True)
                self.assertNotEqual(proc_kill.returncode, 0)

                # 3. Run --repair
                res_repair = subprocess.run([str(inst0), "--repair"], env=self.base_env, capture_output=True, text=True)
                self.assertEqual(res_repair.returncode, 0, res_repair.stderr)
                self.assertIn("Repair successful", res_repair.stdout)

                # 4. Check expected version after repair
                if cut_point == "after_commit_before_cleanup":
                    manifest = (app_dir / ".paperassist-install-manifest").read_text()
                    self.assertIn("VERSION=0.1.0a1", manifest)
                    res_run = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
                    self.assertIn("PaperAssist 0.1.0a1", res_run.stdout)
                    backups = list(self.data_dir.glob("paperassist-app.backup.*"))
                    self.assertEqual(len(backups), 0)
                else:
                    manifest = (app_dir / ".paperassist-install-manifest").read_text()
                    self.assertIn("VERSION=0.1.0a0", manifest)
                    res_run = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
                    self.assertIn("PaperAssist 0.1.0a0", res_run.stdout)

    def test_fresh_install_interruption_repair_and_retry(self):
        bundle = self._create_synthetic_bundle("b_fresh_cut")
        desktop = self._create_desktop_assets("d_fresh_cut")
        installer = self._build_test_installer(bundle, "inst_fresh_cut.sh", desktop)
        app = self.data_dir / "paperassist-app"
        library = self.data_dir / "paperassist" / "library.sqlite3"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"personal library")
        pdf = library.parent / "paper.pdf"
        pdf.write_bytes(b"personal PDF")
        paths = [self.bin_dir / "paperassist",
                 self.data_dir / "applications" / "paperassist.desktop",
                 self.data_dir / "icons" / "hicolor" / "scalable" / "apps" / "paperassist.svg",
                 self.data_dir / "icons" / "hicolor" / "256x256" / "apps" / "paperassist.png"]
        for cut in ("during_extraction", "after_stage", "after_backup",
                    "after_swap_before_manifest", "during_integration", "after_commit_before_cleanup"):
            with self.subTest(cut=cut):
                env = self.base_env | {"__PAPERASSIST_INSTALLER_FAULT_INJECT": cut,
                                       "__PAPERASSIST_INSTALLER_CRASH_SIGNAL": "KILL"}
                killed = subprocess.run([str(installer)], env=env, capture_output=True, text=True)
                self.assertNotEqual(killed.returncode, 0)
                repaired = subprocess.run([str(installer), "--repair"], env=self.base_env, capture_output=True, text=True)
                self.assertEqual(repaired.returncode, 0 if cut == "after_commit_before_cleanup" else 1, repaired.stderr)
                retry = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
                self.assertEqual(retry.returncode, 0, retry.stderr)
                self.assertEqual(library.read_bytes(), b"personal library")
                self.assertEqual(pdf.read_bytes(), b"personal PDF")
                shutil.rmtree(app)
                for path in paths:
                    path.unlink(missing_ok=True)

    def test_fresh_recovery_uses_recorded_custom_launcher_path(self):
        bundle = self._create_synthetic_bundle("b_custom_recovery")
        desktop = self._create_desktop_assets("d_custom_recovery")
        installer = self._build_test_installer(bundle, "inst_custom_recovery.sh", desktop)
        app = self.test_root / "custom app"
        bin_dir = self.test_root / "custom bin"
        env = self.base_env | {"__PAPERASSIST_INSTALLER_FAULT_INJECT": "during_integration",
                               "__PAPERASSIST_INSTALLER_CRASH_SIGNAL": "KILL"}
        killed = subprocess.run([str(installer), "--install-dir", str(app), "--bin-dir", str(bin_dir)],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(killed.returncode, 0)
        launcher = bin_dir / "paperassist"
        self.assertTrue(launcher.exists())
        repaired = subprocess.run([str(installer), "--repair", "--install-dir", str(app)],
                                  env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(repaired.returncode, 0)
        self.assertFalse(launcher.exists())
        retry = subprocess.run([str(installer), "--install-dir", str(app), "--bin-dir", str(bin_dir)],
                               env=self.base_env, capture_output=True, text=True)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertTrue(launcher.exists())

    def test_fresh_install_refuses_managed_integration_symlinks(self):
        bundle = self._create_synthetic_bundle("b_symlink_recovery")
        desktop = self._create_desktop_assets("d_symlink_recovery")
        installer = self._build_test_installer(bundle, "inst_symlink_recovery.sh", desktop)
        managed = self.test_root / "managed"
        managed.write_text("# Managed-By: PaperAssist-Linux-Installer\n")
        launcher = self.bin_dir / "paperassist"
        desktop_path = self.data_dir / "applications" / "paperassist.desktop"
        desktop_path.parent.mkdir(parents=True)
        for path in (launcher, desktop_path):
            with self.subTest(path=path):
                path.symlink_to(managed)
                result = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlink", result.stderr)
                self.assertTrue(path.is_symlink())
                self.assertEqual(managed.read_text(), "# Managed-By: PaperAssist-Linux-Installer\n")
                path.unlink()

    def test_fresh_recovery_preserves_modified_integration(self):
        bundle = self._create_synthetic_bundle("b_fresh_modified")
        desktop = self._create_desktop_assets("d_fresh_modified")
        installer = self._build_test_installer(bundle, "inst_fresh_modified.sh", desktop)
        env = self.base_env | {"__PAPERASSIST_INSTALLER_FAULT_INJECT": "during_integration",
                               "__PAPERASSIST_INSTALLER_CRASH_SIGNAL": "KILL"}
        subprocess.run([str(installer)], env=env, capture_output=True, text=True)
        icon = self.data_dir / "icons" / "hicolor" / "scalable" / "apps" / "paperassist.svg"
        icon.write_text("user modified")
        subprocess.run([str(installer), "--repair"], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(icon.read_text(), "user modified")
        retry = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
        self.assertNotEqual(retry.returncode, 0)
        self.assertEqual(icon.read_text(), "user modified")

    def test_all_cut_points_trap_rollback(self):
        """Test transaction cut points aborting with normal error trigger trap rollback immediately."""
        bundle0 = self._create_synthetic_bundle("b_trap0", version="0.1.0a0")
        bundle1 = self._create_synthetic_bundle("b_trap1", version="0.1.0a1")
        desktop = self._create_desktop_assets("d_trap")
        inst0 = self._build_test_installer(bundle0, "inst_trap0.sh", desktop, "0.1.0a0")
        inst1 = self._build_test_installer(bundle1, "inst_trap1.sh", desktop, "0.1.0a1")
        launcher = self.bin_dir / "paperassist"
        app_dir = self.data_dir / "paperassist-app"

        rollback_cut_points = [
            "during_extraction",
            "after_stage",
            "after_backup",
            "after_swap_before_manifest",
            "during_integration",
        ]

        for cut_point in rollback_cut_points:
            with self.subTest(cut_point=cut_point):
                # Clean up previous state
                if app_dir.exists():
                    shutil.rmtree(app_dir)
                for p in self.data_dir.glob("paperassist-app.*"):
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
                if launcher.exists():
                    launcher.unlink()
                if (self.data_dir / "icons").exists():
                    shutil.rmtree(self.data_dir / "icons")
                if (self.data_dir / "applications").exists():
                    shutil.rmtree(self.data_dir / "applications")

                # 1. Install base v0
                res0 = subprocess.run([str(inst0)], env=self.base_env, capture_output=True, text=True)
                self.assertEqual(res0.returncode, 0, res0.stderr)

                # 2. Trigger fault injection with normal exit 1 (trap runs)
                env_trap = self.base_env.copy()
                env_trap["__PAPERASSIST_INSTALLER_FAULT_INJECT"] = cut_point

                res_fail = subprocess.run([str(inst1)], env=env_trap, capture_output=True, text=True)
                self.assertNotEqual(res_fail.returncode, 0)
                if cut_point not in ("during_extraction", "after_stage"):
                    self.assertIn("Rolling back", res_fail.stderr)

                # 3. v0 must be intact immediately without running --repair
                manifest = (app_dir / ".paperassist-install-manifest").read_text()
                self.assertIn("VERSION=0.1.0a0", manifest)
                res_run = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
                self.assertIn("PaperAssist 0.1.0a0", res_run.stdout)

    def test_concurrent_installer_and_repair_invocations(self):
        """Test that active PID lock prevents concurrent installer or repair runs from destroying state."""
        bundle1 = self._create_synthetic_bundle("b_conc1", version="0.1.0a1")
        installer = self._build_test_installer(bundle1, "inst_conc.sh", None, "0.1.0a1")
        app_dir = self.data_dir / "paperassist-app"

        # Start a real active background process to simulate an active installer
        bg_proc = subprocess.Popen(["sleep", "60"])
        try:
            active_pid = bg_proc.pid

            # Create active lock directory
            lock_dir = app_dir.with_name(app_dir.name + ".lock")
            lock_dir.mkdir(parents=True, exist_ok=True)
            (lock_dir / "owner").write_text(
                f"# Managed-By: PaperAssist-Linux-Installer\nPID={active_pid}\n"
            )

            # Create active staging directory
            staging_dir = app_dir.with_name(f"{app_dir.name}.staging.{active_pid}")
            staging_dir.mkdir(parents=True, exist_ok=True)
            (staging_dir / ".paperassist-staging-marker").write_text(
                f"# Managed-By: PaperAssist-Linux-Installer\nPID={active_pid}\n"
            )
            sentinel_payload = staging_dir / "in_progress_data.bin"
            sentinel_payload.write_bytes(b"active staging data")

            # 1. Concurrent installer run must fail and not delete lock or staging
            res_inst = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
            self.assertNotEqual(res_inst.returncode, 0)
            self.assertIn("Another PaperAssist installer or maintenance process", res_inst.stderr)
            self.assertTrue(lock_dir.exists())
            self.assertTrue(sentinel_payload.exists())

            # 2. Concurrent --repair run must also fail and not delete lock or staging
            res_rep = subprocess.run([str(installer), "--repair"], env=self.base_env, capture_output=True, text=True)
            self.assertNotEqual(res_rep.returncode, 0)
            self.assertIn("Another PaperAssist installer or maintenance process", res_rep.stderr)
            self.assertTrue(lock_dir.exists())
            self.assertTrue(sentinel_payload.exists())
        finally:
            bg_proc.terminate()
            bg_proc.wait()

        # Now that bg_proc is dead:
        # A subsequent installer invocation must detect stale lock, clean up stale staging, and succeed!
        res_after = subprocess.run([str(installer)], env=self.base_env, capture_output=True, text=True)
        self.assertEqual(res_after.returncode, 0, res_after.stderr)
        self.assertTrue(app_dir.is_dir())
        self.assertFalse(staging_dir.exists())
        self.assertFalse(lock_dir.exists())


if __name__ == "__main__":
    unittest.main()
