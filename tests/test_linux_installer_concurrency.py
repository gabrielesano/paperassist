"""Exercise maintenance locking with actual overlapping installer processes."""

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

try:
    from tests import test_linux_installer
except ImportError:
    import test_linux_installer


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux installer tests require Linux")
class MaintenanceConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_linux_installer.LinuxInstallerTestCase(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        f = self.fixture
        bundle = f._create_synthetic_bundle()
        desktop = f._create_desktop_assets()
        self.installer = f._build_test_installer(bundle, desktop_dir=desktop)
        self.args = [str(self.installer)] + (["--allow-root"] if os.getuid() == 0 else [])
        self.env = f.base_env.copy()
        self.app = f.data_dir / "paperassist-app"
        result = self.run_command(self.args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.gate = f.test_root / "gate"
        self.gate.mkdir()

    def run_command(self, args, env=None):
        return subprocess.run(args, env=env or self.env, capture_output=True, text=True, timeout=20)

    def paused_process(self, args, command, predicate):
        wrapper_dir = self.fixture.test_root / (command + "-wrapper")
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / command
        wrapper.write_text(
            '#!/bin/sh\n'
            f'if {predicate}; then\n'
            '  touch "$GATE/ready"\n'
            '  while [ ! -e "$GATE/go" ]; do sleep 0.02; done\n'
            'fi\n'
            f'exec /usr/bin/{command} "$@"\n'
        )
        wrapper.chmod(0o755)
        env = {**self.env, "PATH": f"{wrapper_dir}:/usr/bin:/bin", "GATE": str(self.gate)}
        proc = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_process, proc)
        deadline = time.monotonic() + 15
        while not (self.gate / "ready").exists():
            if proc.poll() is not None or time.monotonic() >= deadline:
                self.fail(f"Process did not reach gate: {proc.communicate(timeout=2)}")
            time.sleep(0.02)
        return proc

    def stop_process(self, proc):
        (self.gate / "go").touch()
        if proc.poll() is None:
            proc.terminate()
        proc.communicate(timeout=10)

    def test_uninstall_holds_lock_against_install_and_repair(self):
        proc = self.paused_process([str(self.app / "uninstall.sh")], "grep", '[ "$1" = "^LAUNCHER_PATH=" ]')
        for args in (self.args, self.args + ["--repair"]):
            result = self.run_command(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("maintenance process", result.stderr)
            self.assertTrue(self.app.exists())
        (self.gate / "go").touch()
        _, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, err)
        self.assertFalse(self.app.exists())

    def test_installer_uninstall_holds_lock_through_removal(self):
        proc = self.paused_process(self.args + ["--uninstall"], "grep", '[ "$1" = "^LAUNCHER_PATH=" ]')
        result = self.run_command(self.args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("maintenance process", result.stderr)
        self.assertTrue(self.app.exists())
        (self.gate / "go").touch()
        _, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, err)
        self.assertFalse(self.app.exists())

    def test_unmanaged_uninstall_targets_leave_no_lock_files(self):
        for name in ("library", "foreign"):
            target = self.fixture.test_root / name
            target.mkdir()
            if name == "library":
                (target / "library.sqlite3").touch()
            result = self.run_command([str(self.app / "uninstall.sh"), "--install-dir", str(target)])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.with_name(target.name + ".lock").exists())
            self.assertFalse(target.with_name(target.name + ".lock.flock").exists())

    def test_stale_marker_reclamation_is_serialized(self):
        lock_dir = self.app.with_name(self.app.name + ".lock")
        lock_dir.mkdir()
        (lock_dir / "owner").write_text(
            "# Managed-By: PaperAssist-Linux-Installer\nPID=999999999\n"
        )
        proc = self.paused_process(self.args, "grep", '[ "$1" = "^PID=" ]')
        result = self.run_command(self.args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("maintenance process", result.stderr)
        self.assertTrue((lock_dir / "owner").exists())
        (self.gate / "go").touch()
        _, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, err)
        self.assertFalse(lock_dir.exists())

    def test_installer_holds_lock_against_uninstall(self):
        proc = self.paused_process(self.args, "sha256sum", 'true')
        result = self.run_command([str(self.app / "uninstall.sh")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("maintenance process", result.stderr)
        self.assertTrue(self.app.exists())
        (self.gate / "go").touch()
        _, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, err)
        self.assertTrue(self.app.exists())


if __name__ == "__main__":
    unittest.main()
