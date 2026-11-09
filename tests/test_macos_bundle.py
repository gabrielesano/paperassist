"""Acceptance of the mounted Apple Silicon disk image and copied application."""

import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import signal
import sqlite3
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
let files = CommandLine.arguments[1] == "files"
config.createsNewApplicationInstance = !files
if !files { config.arguments = Array(CommandLine.arguments.dropFirst(2)) }
config.environment = ProcessInfo.processInfo.environment
var finished = false
var status: Int32 = 1
let completed: (NSRunningApplication?, Error?) -> Void = { app, error in
    if let app = app {
        print(app.processIdentifier)
        status = 0
    } else {
        fputs("LaunchServices: \(String(describing: error))\n", stderr)
    }
    finished = true
}
if files {
    NSWorkspace.shared.open(Array(CommandLine.arguments.dropFirst(3)).map { URL(fileURLWithPath: $0) },
        withApplicationAt: URL(fileURLWithPath: CommandLine.arguments[2]), configuration: config,
        completionHandler: completed)
} else {
    NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: CommandLine.arguments[1]),
        configuration: config, completionHandler: completed)
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
        document_type, = info['CFBundleDocumentTypes']
        self.assertEqual(document_type['CFBundleTypeRole'], 'Viewer')
        self.assertEqual(document_type['LSHandlerRank'], 'Alternate')
        self.assertEqual(document_type['LSItemContentTypes'], ['com.adobe.pdf'])
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
            from tests.test_ocr import scan_pdf
            scan = scan_pdf(root / 'Scanned paper è.pdf')
            scan_before = scan.read_bytes()
            ocr_env = {**env, 'PATH': '', 'TESSDATA_PREFIX': str(root / 'absent-models')}
            recognized = command(str(binary), '--self-test-ocr', str(scan), env=ocr_env, cwd=root)
            self.assertIn('OCR self-test ok: bundled Tesseract', recognized.stdout)
            self.assertIn('Adiabatic Heating of Coronal Loops', recognized.stdout)
            self.assertEqual(scan.read_bytes(), scan_before)

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
            cold = root / 'Cold è % paper.pdf'
            cold_bytes = original + b'\n%Cold Finder PDF\n'
            cold.write_bytes(cold_bytes)

            def wait_count(expected):
                deadline = time.monotonic() + 15
                count = 0
                while time.monotonic() < deadline:
                    with sqlite3.connect(catalog.as_uri() + '?mode=ro', uri=True) as db:
                        count = db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
                    if count == expected:
                        break
                    time.sleep(.1)
                self.assertEqual(count, expected, 'Finder requests must import in the library owner')

            # Exercise cold Finder events and a normal application launch, then
            # send warm Finder events to the exact same running application.
            for index, arguments in enumerate((('files', str(copied), str(cold)), (str(copied),)):
                with self.subTest(arguments=arguments):
                    second = root / f'Warm {index} è % paper.pdf'
                    second_bytes = original + f'\n%Warm Finder PDF {index}\n'.encode()
                    second.write_bytes(second_bytes)
                    launched = command(str(launcher), *arguments, env=env, cwd=root)
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
                        wait_count(2 + index)
                        warm = command(str(launcher), 'files', str(copied), str(pdf), str(second), env=env, cwd=root)
                        self.assertEqual(int(warm.stdout.strip().splitlines()[-1]), pid)
                        wait_count(3 + index)
                        delivered = command(str(binary), str(pdf), str(second), env=env, cwd=root)
                        self.assertEqual(delivered.returncode, 0)
                        time.sleep(.5)  # Let the accepted queue finish before asking the window to close.
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
                        self.assertEqual(pdf.read_bytes(), original)
                        self.assertEqual(second.read_bytes(), second_bytes)
                        self.assertEqual(cold.read_bytes(), cold_bytes)
                        self.assertEqual(stored_pdf.read_bytes(), original)
                        self.assertEqual(list((library / "working").glob("*.pdf")), [])
                        workspace = Workspace(library)
                        try:
                            self.assertEqual(len(workspace.library.list_documents()), 3 + index)
                            for document in workspace.library.list_documents():
                                self.assertEqual(document['revision'], 0)
                                self.assertEqual(workspace.library.get_notes(document['id']), [])
                        finally:
                            workspace.close()
                    finally:
                        if pid is not None:
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass


if __name__ == "__main__":
    unittest.main()
