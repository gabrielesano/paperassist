"""Acceptance of the mounted Apple Silicon disk image and copied application."""

import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from paperassist import __version__
from paperassist.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dist/PaperAssist.app"
DMG = ROOT / "dist" / f"PaperAssist-{__version__}-macos-arm64.dmg"

# Window ownership and bounds are available without accessibility automation or
# screen-recording access; the test does not inspect other applications' content.
LAUNCHER = r'''
import AppKit
import CoreGraphics

if CommandLine.arguments[1] == "window" {
    let pid = Int32(CommandLine.arguments[2])!
    let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    let found = windows.contains { info in
        let bounds = info[kCGWindowBounds as String] as? [String: Any] ?? [:]
        return (info[kCGWindowOwnerPID as String] as? Int32) == pid
            && (info[kCGWindowLayer as String] as? Int) == 0
            && (bounds["Width"] as? Double ?? 0) > 100
            && (bounds["Height"] as? Double ?? 0) > 100
    }
    exit(found ? 0 : 1)
}

let config = NSWorkspace.OpenConfiguration()
config.createsNewApplicationInstance = true
config.arguments = Array(CommandLine.arguments.dropFirst(2))
config.environment = ProcessInfo.processInfo.environment
var finished = false
var status: Int32 = 1
NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: CommandLine.arguments[1]), configuration: config) { app, error in
    if let app = app {
        print(app.processIdentifier)
        status = 0
    } else {
        fputs("LaunchServices: \(String(describing: error))\n", stderr)
    }
    finished = true
}
while !finished { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05)) }
exit(status)
'''


def command(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60, **kwargs)


@unittest.skipUnless(sys.platform == "darwin", "Application disk images require macOS")
class MacOSBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not APP.is_dir() or not DMG.is_file():
            raise unittest.SkipTest("Build the macOS application and disk image first")

    def test_application_identity_resources_and_signature(self):
        info = plistlib.loads((APP / "Contents/Info.plist").read_bytes())
        self.assertEqual(info["CFBundlePackageType"], "APPL")
        self.assertEqual(info["CFBundleExecutable"], "PaperAssist")
        self.assertEqual(info["CFBundleIdentifier"], "org.paperassist.PaperAssist")
        self.assertFalse(info.get("LSBackgroundOnly", False))
        self.assertIn(__version__, info["CFBundleGetInfoString"])
        resources = APP / "Contents/Resources"
        self.assertTrue((resources / info["CFBundleIconFile"]).read_bytes().startswith(b"icns"))
        self.assertTrue((resources / "LICENSE").is_file())
        self.assertTrue((resources / "THIRD-PARTY-NOTICES.txt").is_file())
        provenance = json.loads((resources / "BUILD-PROVENANCE.json").read_text())
        self.assertEqual(provenance["version"], __version__)
        self.assertEqual(command("lipo", "-archs", str(APP / "Contents/MacOS/PaperAssist")).stdout.strip(), "arm64")
        command("codesign", "--verify", "--deep", "--strict", str(APP))

    def test_mount_copy_launch_render_and_close(self):
        digest = hashlib.sha256(DMG.read_bytes()).hexdigest()
        self.assertEqual(DMG.with_name(DMG.name + ".sha256").read_text().split()[0], digest)
        with tempfile.TemporaryDirectory(prefix="paperassist-macos-") as temporary:
            root = Path(temporary)
            mount = root / "volume"
            mount.mkdir()
            copied = root / "Applications with spaces/PaperAssist.app"
            command("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(DMG))
            try:
                self.assertEqual(os.readlink(mount / "Applications"), "/Applications")
                command("ditto", str(mount / "PaperAssist.app"), str(copied))
            finally:
                command("hdiutil", "detach", str(mount))
            command("codesign", "--verify", "--deep", "--strict", str(copied))
            binary = copied / "Contents/MacOS/PaperAssist"
            home = root / "home"
            home.mkdir()
            env = {**os.environ, "HOME": str(home), "PATH": "/usr/bin:/bin", "QT_QPA_PLATFORM": "cocoa"}
            for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
                env.pop(key, None)
            pdf = root / "sample.pdf"
            original = (ROOT / "tests/fixtures/pdfjs-annotations.pdf").read_bytes()
            pdf.write_bytes(original)
            self.assertIn(__version__, command(str(binary), "--version", env=env, cwd=root).stdout)
            self.assertIn("self-test ok", command(str(binary), "--self-test", str(pdf), env=env, cwd=root).stdout)

            swift = root / "launch.swift"
            swift.write_text(LAUNCHER)
            launcher = root / "launch"
            command("swiftc", str(swift), "-o", str(launcher))
            library = home / "Library/Application Support/PaperAssist"
            workspace = Workspace(library)
            try:
                stored = workspace.library.import_stream(io.BytesIO(original), "sample.pdf")
                stored_pdf = workspace.library.get_path(stored["id"], annotated=False)
            finally:
                workspace.close()
            catalog = library / "library.sqlite3"
            catalog_before = catalog.read_bytes()
            # File arguments use standalone mode; a normal Finder launch opens
            # the default library. Exercise both without conflating their state.
            for arguments in ((str(pdf),), ()):
                with self.subTest(arguments=arguments):
                    launched = command(str(launcher), str(copied), *arguments, env=env, cwd=root)
                    pid = int(launched.stdout.strip().splitlines()[-1])
                    try:
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            probe = subprocess.run([str(launcher), "window", str(pid)], capture_output=True, timeout=5)
                            processes = command("ps", "-axo", "pid=,ppid=,command=").stdout.splitlines()
                            worker = any(
                                len(parts := line.split(None, 2)) == 3 and parts[1] == str(pid)
                                and str(binary) in parts[2] and "multiprocessing-fork" in parts[2]
                                for line in processes
                            )
                            if probe.returncode == 0 and worker:
                                break
                            time.sleep(0.2)
                        else:
                            matching = [line for line in processes if str(binary) in line]
                            self.fail(f"Missing window or frozen worker: window={probe.returncode == 0}, worker={worker}; {matching}")
                        os.kill(pid, signal.SIGTERM)
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            try:
                                os.kill(pid, 0)
                            except ProcessLookupError:
                                pid = None
                                break
                            time.sleep(0.1)
                        self.assertIsNone(pid, "Application did not close cleanly")
                        if arguments:
                            self.assertEqual(catalog.read_bytes(), catalog_before)
                        self.assertEqual(pdf.read_bytes(), original)
                        self.assertEqual(stored_pdf.read_bytes(), original)
                        self.assertEqual(list((library / "working").glob("*.pdf")), [])
                    finally:
                        if pid is not None:
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass


if __name__ == "__main__":
    unittest.main()
