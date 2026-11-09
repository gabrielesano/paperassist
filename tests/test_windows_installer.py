"""Acceptance of the single-file Windows installer and installed GUI executable."""

import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from paperassist import __version__
from paperassist.workspace import Workspace

if sys.platform == "win32":
    import winreg

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "dist" / f"PaperAssist-{__version__}-windows-x86_64-setup.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PaperAssist.Native_is1"


def registry_value(key, name=''):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except FileNotFoundError:
        return None


def user_folder(number):
    buffer = ctypes.create_unicode_buffer(32768)
    result = ctypes.WinDLL("shell32").SHGetFolderPathW(None, number, None, 0, buffer)
    if result:
        raise OSError(f"Cannot find user shell folder: {result}")
    return Path(buffer.value)


def reader_windows(pid):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    found = []

    @callback_type
    def visit(window, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(window):
            title = ctypes.create_unicode_buffer(1024)
            user.GetWindowTextW(window, title, len(title))
            if "PaperAssist" in title.value:
                found.append((window, title.value))
        return True

    user.EnumWindows(visit, 0)
    return found


def visible_reader_window(pid):
    windows = reader_windows(pid)
    return next((window for window, title in windows if title.endswith(" — PaperAssist")),
                windows[0][0] if windows else None)


def tree_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


@unittest.skipUnless(sys.platform == "win32", "Windows installer acceptance requires Windows")
class WindowsInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not INSTALLER.is_file():
            raise unittest.SkipTest("Build the Windows installer first")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
                raise unittest.SkipTest("Use a disposable Windows account without an existing PaperAssist installation")
        except FileNotFoundError:
            pass
        cls.start_link = user_folder(2) / "PaperAssist.lnk"
        cls.desktop_link = user_folder(0x10) / "PaperAssist.lnk"
        if cls.start_link.exists() or cls.desktop_link.exists():
            raise unittest.SkipTest("Existing PaperAssist shortcuts must not be replaced by acceptance tests")
        if registry_value(r'Software\Classes\PaperAssist.PDF') is not None:
            raise unittest.SkipTest('Use an account without an existing PaperAssist PDF registration')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="paperassist-windows-")
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)
        self.app = self.root / "Programs with spaces/PaperAssist"
        self.binary = self.app / "PaperAssist.exe"
        self.uninstaller = self.app / "unins000.exe"
        self.env = {**os.environ, "APPDATA": str(self.root / "roaming"),
                    "LOCALAPPDATA": str(self.root / "local"), "QT_QPA_PLATFORM": "windows"}
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            self.env.pop(key, None)
        self.env["PATH"] = os.pathsep.join([os.environ["SystemRoot"], str(Path(os.environ["SystemRoot"]) / "System32")])
        self.pdf = self.root / "sample.pdf"
        self.original = (ROOT / "tests/fixtures/pdfjs-annotations.pdf").read_bytes()
        self.pdf.write_bytes(self.original)
        self.library = Path(self.env["APPDATA"]) / "PaperAssist"
        workspace = Workspace(self.library)
        try:
            self.stored = workspace.library.import_stream(io.BytesIO(self.original), "sample.pdf")
            workspace.library.update_metadata(self.stored['id'], {'title': 'Acceptance title'})
        finally:
            workspace.close()
        config = Path(self.env["LOCALAPPDATA"]) / "PaperAssist/config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"library": str(self.library), "sentinel": "preserve"}))
        self.before = tree_hashes(Path(self.env["APPDATA"])), tree_hashes(Path(self.env["LOCALAPPDATA"]))
        self.defaults = [registry_value(r'Software\Classes\.pdf'),
                         registry_value(r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice', 'ProgId'),
                         registry_value(r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice', 'Hash')]
        self.addCleanup(self.cleanup_installation)

    def cleanup_installation(self):
        if self.uninstaller.exists():
            subprocess.run([str(self.uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=60)

    def setup_command(self, target=None):
        return [str(INSTALLER), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                f"/DIR={target or self.app}", "/TASKS=desktopicon,pdfhandler", f"/LOG={self.root / 'setup.log'}"]

    def install(self):
        result = subprocess.run(self.setup_command(), capture_output=True, timeout=90)
        log = (self.root / "setup.log").read_bytes()
        self.assertEqual(result.returncode, 0, log.decode("utf-16" if log.startswith(b"\xff\xfe") else "utf-8", errors="replace"))

    def assert_data_preserved(self):
        self.assertEqual(self.pdf.read_bytes(), self.original)
        self.assertEqual((tree_hashes(Path(self.env["APPDATA"])), tree_hashes(Path(self.env["LOCALAPPDATA"]))), self.before)
        self.assertEqual([registry_value(r'Software\Classes\.pdf'),
                          registry_value(r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice', 'ProgId'),
                          registry_value(r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice', 'Hash')], self.defaults)

    def test_library_and_unmanaged_targets_are_refused(self):
        for target in (self.library, self.root / "foreign"):
            with self.subTest(target=target):
                target.mkdir(parents=True, exist_ok=True)
                (target / "sentinel.txt").write_text("untouched")
                before = tree_hashes(target)
                result = subprocess.run(self.setup_command(target), capture_output=True, timeout=45)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(tree_hashes(target), before)

    def test_install_gui_busy_upgrade_and_uninstall(self):
        self.assertEqual(INSTALLER.with_name(INSTALLER.name + ".sha256").read_text().split()[0],
                         hashlib.sha256(INSTALLER.read_bytes()).hexdigest())
        self.install()
        self.assertTrue(self.binary.is_file())
        self.assertTrue((self.app / "_internal").is_dir())
        for name in ("LICENSE", "THIRD-PARTY-NOTICES.txt", "BUILD-PROVENANCE.json", "INNO-SETUP-LICENSE.txt"):
            self.assertTrue((self.app / name).is_file(), name)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            self.assertEqual(winreg.QueryValueEx(key, "DisplayVersion")[0], __version__)
            self.assertEqual(Path(winreg.QueryValueEx(key, "InstallLocation")[0]).resolve(), self.app)
        self.assertTrue(self.start_link.exists())
        self.assertTrue(self.desktop_link.exists())
        shell_command = registry_value(r'Software\Classes\PaperAssist.PDF\shell\open\command')
        self.assertEqual(shell_command, f'"{self.binary}" "%1"')
        self.assertEqual(registry_value(r'Software\Classes\.pdf\OpenWithProgids', 'PaperAssist.PDF'), '')
        self.assertEqual(registry_value(r'Software\PaperAssist\Capabilities\FileAssociations', '.pdf'), 'PaperAssist.PDF')
        self.assertEqual(registry_value(r'Software\RegisteredApplications', 'PaperAssist'), r'Software\PaperAssist\Capabilities')
        script = self.root / "shortcut.ps1"
        script.write_text('param([string]$ShortcutPath)\n$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($ShortcutPath)\n$shortcut.TargetPath\n')
        target = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", str(script), str(self.start_link)],
                                capture_output=True, text=True, check=True, timeout=20).stdout.strip()
        self.assertEqual(Path(target).resolve(), self.binary)

        data = self.binary.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        self.assertEqual(struct.unpack_from("<H", data, pe + 24 + 68)[0], 2, "GUI app must not open a console")
        subprocess.run([str(self.binary), "--self-test", str(self.pdf)], env=self.env, cwd=self.root, check=True, timeout=70)
        from tests.test_ocr import scan_pdf
        scan = scan_pdf(self.root / 'Scanned paper è.pdf')
        scan_before = scan.read_bytes()
        ocr_env = {**self.env, 'PATH': '', 'TESSDATA_PREFIX': str(self.root / 'absent-models')}
        subprocess.run([str(self.binary), '--self-test-ocr', str(scan)], env=ocr_env,
                       cwd=self.root, check=True, timeout=90)
        self.assertEqual(scan.read_bytes(), scan_before)
        self.assert_data_preserved()
        proc = subprocess.Popen([str(self.binary), str(self.pdf)], env=self.env, cwd=self.root)
        try:
            deadline = time.monotonic() + 20
            window = None
            while time.monotonic() < deadline and proc.poll() is None:
                if window := visible_reader_window(proc.pid):
                    break
                time.sleep(0.1)
            self.assertIsNotNone(window, "Installed GUI did not create a visible reader window")
            second = self.root / 'Second è % paper.pdf'
            second.write_bytes(self.original + b'\n%Second external PDF\n')
            delivered = subprocess.run(shell_command.replace('"%1"', subprocess.list2cmdline([str(second)])),
                                       env=self.env, cwd=self.root, timeout=15)
            self.assertEqual(delivered.returncode, 0)
            repeated = subprocess.run([str(self.binary), str(self.pdf), str(second)], env=self.env,
                                      cwd=self.root, timeout=15)
            self.assertEqual(repeated.returncode, 0)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                with sqlite3.connect((self.library / 'library.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
                    count = db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
                if count == 2:
                    break
                time.sleep(.1)
            self.assertEqual(count, 2, 'Shell requests must import once in the existing owner')
            self.assertIsNone(proc.poll())
            self.assertIsNotNone(visible_reader_window(proc.pid))
            query = (f"Get-CimInstance Win32_Process -Filter 'ParentProcessId = {proc.pid}' | "
                     "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
            worker_data = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
                                         capture_output=True, text=True, check=True, timeout=20)
            workers = json.loads(worker_data.stdout)
            if isinstance(workers, dict):
                workers = [workers]
            self.assertTrue(any("multiprocessing-fork" in (worker.get("CommandLine") or "")
                                and Path(worker.get("ExecutablePath") or "").resolve() == self.binary for worker in workers))
            executable_before = hashlib.sha256(self.binary.read_bytes()).hexdigest()
            for args in (self.setup_command(), [str(self.uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]):
                result = subprocess.run(args, capture_output=True, timeout=45)
                self.assertNotEqual(result.returncode, 0, "Maintenance must refuse a running GUI")
                self.assertIsNone(proc.poll(), "Maintenance terminated the reader")
                self.assertEqual(hashlib.sha256(self.binary.read_bytes()).hexdigest(), executable_before)
            window = visible_reader_window(proc.pid)
            self.assertIsNotNone(window, f"Reader window disappeared: {reader_windows(proc.pid)}")
            print(f"Reader windows before close: {reader_windows(proc.pid)}", flush=True)
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self.assertTrue(user.PostMessageW(window, 0x0010, 0, 0), f"WM_CLOSE failed: {ctypes.get_last_error()}")
            try:
                self.assertEqual(proc.wait(timeout=15), 0)
            except subprocess.TimeoutExpired:
                self.fail(f"Reader did not close; remaining windows: {reader_windows(proc.pid)}")
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        workspace = Workspace(self.library)
        try:
            self.assertEqual(len(workspace.library.list_documents()), 2)
            self.assertEqual(workspace.library.get_document(self.stored['id'])['title'], 'Acceptance title')
            self.assertEqual(workspace.library.get_path(self.stored['id'], False).read_bytes(), self.original)
            for document in workspace.library.list_documents():
                self.assertEqual(document['revision'], 0)
                self.assertEqual(workspace.library.get_notes(document['id']), [])
        finally:
            workspace.close()
        self.assertEqual(second.read_bytes(), self.original + b'\n%Second external PDF\n')
        self.assertEqual(tree_hashes(Path(self.env['LOCALAPPDATA'])), self.before[1])
        # Reading positions and the explicitly imported second paper are now expected state.
        self.before = tree_hashes(Path(self.env['APPDATA'])), self.before[1]
        self.assert_data_preserved()
        self.install()
        self.assert_data_preserved()
        (self.app / "user-file.txt").write_text("not installed by PaperAssist")
        subprocess.run([str(self.uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=60)
        deadline = time.monotonic() + 10
        while self.binary.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(self.binary.exists())
        self.assertFalse(self.start_link.exists())
        self.assertFalse(self.desktop_link.exists())
        self.assertEqual((self.app / "user-file.txt").read_text(), "not installed by PaperAssist")
        with self.assertRaises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        self.assertIsNone(registry_value(r'Software\Classes\PaperAssist.PDF'))
        self.assertIsNone(registry_value(r'Software\Classes\.pdf\OpenWithProgids', 'PaperAssist.PDF'))
        self.assertIsNone(registry_value(r'Software\RegisteredApplications', 'PaperAssist'))
        self.assert_data_preserved()


if __name__ == "__main__":
    unittest.main()
