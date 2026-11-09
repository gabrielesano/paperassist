"""Native startup diagnostics work without requiring Qt or PDFium to be installed."""
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from types import ModuleType
from unittest.mock import patch

from paperassist.native import __main__ as entry


BLOCK_NATIVE = '''
import importlib.abc
import sys
class BlockNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('PySide6', 'shiboken6', 'pypdfium2', 'pypdfium2_raw'):
            raise ModuleNotFoundError(f"No module named '{fullname}'", name=fullname)
sys.meta_path.insert(0, BlockNative())
'''


class NativeBootstrapTests(unittest.TestCase):
    def test_windowed_windows_build_shows_startup_errors_without_a_console(self):
        with patch.object(sys, "platform", "win32"), patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "stderr", None), patch("ctypes.WinDLL", create=True) as library:
            entry.startup_error("Reinstall the complete application")
        library.return_value.MessageBoxW.assert_called_once_with(
            None, "Reinstall the complete application", "PaperAssist", 0x10,
        )

    def test_missing_dependencies_name_the_component_and_explain_source_installation(self):
        for missing in ("PySide6.QtWidgets", "pypdfium2"):
            with self.subTest(missing=missing):
                def load(name):
                    if name == missing:
                        raise ModuleNotFoundError(f"No module named '{name}'", name=name)
                output = io.StringIO()
                with patch.object(entry.multiprocessing, "freeze_support"), \
                        patch.object(entry, "import_module", side_effect=load), redirect_stderr(output):
                    with self.assertRaises(SystemExit) as result:
                        entry.main()
                self.assertEqual(result.exception.code, 1)
                self.assertIn(missing, output.getvalue())
                self.assertIn('python -m pip install -e .', output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())

    def test_native_library_load_errors_have_a_clean_diagnostic(self):
        for error in (ImportError("Qt shared library unavailable"), OSError("PDFium binary unavailable")):
            with self.subTest(error=error):
                output = io.StringIO()
                with patch.object(entry.multiprocessing, "freeze_support"), \
                        patch.object(entry, "import_module", side_effect=error), redirect_stderr(output):
                    with self.assertRaises(SystemExit) as result:
                        entry.main()
                self.assertEqual(result.exception.code, 1)
                self.assertIn(str(error), output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())

    def test_frozen_build_directs_the_user_to_reinstall_the_application(self):
        output = io.StringIO()
        with patch.object(entry.multiprocessing, "freeze_support"), \
                patch.object(entry, "import_module", side_effect=ImportError("Missing Qt")), \
                patch.object(sys, "frozen", True, create=True), redirect_stderr(output):
            with self.assertRaises(SystemExit):
                entry.main()
        self.assertIn("Reinstall a complete PaperAssist build", output.getvalue())
        self.assertNotIn("pip install", output.getvalue())

    def test_internal_application_import_errors_are_not_reported_as_missing_dependencies(self):
        output = io.StringIO()
        broken_app = ModuleType("paperassist.native.app")
        with patch.object(entry.multiprocessing, "freeze_support"), \
                patch.object(entry, "import_module"), \
                patch.dict(sys.modules, {"paperassist.native.app": broken_app}), redirect_stderr(output):
            with self.assertRaises(ImportError):
                entry.main()
        self.assertEqual(output.getvalue(), "")

    def test_frozen_worker_bootstrap_runs_before_native_dependency_imports(self):
        with patch.object(entry.multiprocessing, "freeze_support", side_effect=SystemExit(0)), \
                patch.object(entry, "import_module") as load:
            with self.assertRaises(SystemExit) as result:
                entry.main()
        self.assertEqual(result.exception.code, 0)
        load.assert_not_called()

    def test_real_native_entry_point_reports_missing_qt_without_a_traceback(self):
        result = subprocess.run([sys.executable, "-c", BLOCK_NATIVE + '''
import runpy
runpy.run_module('paperassist.native', run_name='__main__')
'''], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("PySide6", result.stderr)
        self.assertIn('python -m pip install -e .', result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_import_works_without_native_or_retired_web_packages(self):
        result = subprocess.run([sys.executable, "-c", BLOCK_NATIVE + '''
class BlockWeb(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('flask', 'werkzeug', 'webview', 'qtpy', 'PyQt5', 'PyQt6'):
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, BlockWeb())
import tempfile
from pathlib import Path
from unittest.mock import patch
from paperassist.cli import main
from tests.test_library import pdf
from paperassist.library import Library
try:
    main(['import', '--help'])
except SystemExit as error:
    assert error.code == 0
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    with patch('paperassist.paths.config_file', return_value=root / 'config.json'):
        original = root / 'source.pdf'
        original.write_bytes(pdf())
        assert main(['import', str(original), '--data-dir', str(root / 'library')]) == 0
        assert len(Library(root / 'library').list_documents()) == 1
        assert original.read_bytes() == pdf()
'''], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
