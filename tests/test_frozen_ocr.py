"""The copied app recovers a scan with no PATH, system engine or external tessdata."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_ocr import AUTHORS, scan_pdf, pdfium

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'dist' / 'PaperAssist'
EXECUTABLE = 'PaperAssist.exe' if sys.platform == 'win32' else 'PaperAssist'


@unittest.skipUnless((BUNDLE / EXECUTABLE).is_file() and pdfium, 'Build the standalone app with bundled OCR first')
class FrozenOcrTests(unittest.TestCase):
    def test_copied_bundle_recovers_a_scan_offline_and_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory(prefix='paperassist-ocr-') as folder:
            root = Path(folder)
            copied = root / 'App with spaces è'
            shutil.copytree(BUNDLE, copied, symlinks=True)
            scan = scan_pdf(root / 'Scanned paper è.pdf')
            before = hashlib.sha256(scan.read_bytes()).hexdigest()
            empty = root / 'empty'
            empty.mkdir()
            env = {**os.environ, 'PATH': str(empty), 'TESSDATA_PREFIX': str(empty),
                   'HOME': str(root), 'XDG_CONFIG_HOME': str(root / 'config'),
                   'XDG_DATA_HOME': str(root / 'data'), 'PAPERASSIST_DATA_DIR': str(root / 'library')}
            result = subprocess.run([str(copied / EXECUTABLE), '--self-test-ocr', str(scan)],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if sys.platform != 'win32':
                self.assertIn('OCR self-test ok: bundled Tesseract', result.stdout)
                self.assertIn('Adiabatic Heating of Coronal Loops', result.stdout)
                for author in AUTHORS.split(' and '):
                    self.assertIn(author, result.stdout)
            self.assertEqual(hashlib.sha256(scan.read_bytes()).hexdigest(), before)
            self.assertFalse((root / 'library').exists())
            manifest = json.loads((copied / '_internal/ocr/manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(set(path.stem for path in (copied / '_internal/ocr/tessdata').glob('*.traineddata')), {'eng'})
            notices = (copied / 'THIRD-PARTY-NOTICES.txt').read_text(encoding='utf-8')
            for name in ('tesseract', 'leptonica', 'tessdata_fast'):
                self.assertIn(f'Bundled OCR: {name}', notices)
            self.assertGreater(manifest['runtime_bytes'], 4_000_000)
