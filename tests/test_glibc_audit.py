import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('audit_glibc', Path(__file__).resolve().parents[1] / 'packaging/audit_glibc.py')
audit_glibc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_glibc)


class GlibcAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = Path(self.tmp.name)
        self.executable = self.bundle / 'PaperAssist'
        self.executable.write_bytes(b'\x7fELF')
        self.executable.chmod(0o755)

    def run_audit(self, output='Name: GLIBC_2.35', side_effect=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr='')
        with patch.object(audit_glibc.subprocess, 'run', return_value=result, side_effect=side_effect), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = audit_glibc.audit(self.bundle)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_readelf_failure_fails_with_path(self):
        failure = subprocess.CalledProcessError(1, 'readelf', stderr='invalid ELF')
        status, output, error = self.run_audit(side_effect=failure)
        self.assertEqual(status, 1)
        self.assertIn(str(self.executable), error)
        self.assertIn('invalid ELF', error)
        self.assertNotIn('PASSED', output)

    def test_missing_readelf_fails(self):
        status, _, error = self.run_audit(side_effect=FileNotFoundError('readelf missing'))
        self.assertEqual(status, 1)
        self.assertIn('readelf missing', error)

    def test_missing_or_non_elf_executable_fails(self):
        self.executable.write_text('not ELF')
        status, _, error = self.run_audit()
        self.assertEqual(status, 1)
        self.assertIn('expected executable', error)

    def test_executable_requires_numeric_glibc(self):
        status, _, error = self.run_audit(output='No version information found')
        self.assertEqual(status, 1)
        self.assertIn('no numeric GLIBC', error)

    def test_newer_version_fails_and_old_version_passes(self):
        status, _, error = self.run_audit(output='Name: GLIBC_2.36')
        self.assertEqual(status, 1)
        self.assertIn('GLIBC_2.36', error)
        self.assertEqual(self.run_audit()[0], 0)

    def test_library_without_glibc_is_allowed(self):
        library = self.bundle / 'library.so'
        library.write_bytes(b'\x7fELF')
        def inspect(command, **kwargs):
            output = 'Name: GLIBC_2.14' if command[-1] == str(self.executable) else 'No version information found'
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr='')
        self.assertEqual(self.run_audit(side_effect=inspect)[0], 0)

    def test_unreadable_file_fails(self):
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.name == 'bad.so':
                raise PermissionError('permission denied')
            return original(path, *args, **kwargs)
        (self.bundle / 'bad.so').touch()
        with patch.object(Path, 'open', open_file):
            status, _, error = self.run_audit()
        self.assertEqual(status, 1)
        self.assertIn('bad.so', error)
        self.assertIn('permission denied', error)


if __name__ == '__main__':
    unittest.main()
