"""Pinned source integrity and fail-closed OCR bundle validation."""
import hashlib
import importlib.util
import json
import platform
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('paperassist_build_ocr', ROOT / 'packaging/ocr/build_ocr.py')
notices = load('paperassist_ocr_notices', ROOT / 'packaging/collect_notices.py')


class OcrPackagingTests(unittest.TestCase):
    def test_cached_download_requires_the_pinned_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            cached = directory / 'source.tar.gz'
            cached.write_bytes(b'correct source')
            item = {'filename': cached.name, 'sha256': hashlib.sha256(cached.read_bytes()).hexdigest(),
                    'url': 'https://invalid.example/source'}
            self.assertEqual(builder.download(item, directory), cached)
            cached.write_bytes(b'tampered source')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                builder.download(item, directory)

    def make_runtime(self, directory):
        files = {}
        for name in ('tesseract.exe' if sys.platform == 'win32' else 'tesseract', 'tessdata/eng.traineddata',
                     'tessdata/configs/tsv', 'LICENSE.tesseract', 'LICENSE.leptonica', 'LICENSE.tessdata_fast'):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'Synthetic {name}', encoding='utf-8')
            files[name] = {'bytes': path.stat().st_size, 'sha256': builder.digest(path)}
        manifest = {'profile': builder.PROFILE, 'platform': sys.platform, 'architecture': platform.machine(),
                    'sources': builder.SOURCES, 'files': files}
        self.save_manifest(directory, manifest)
        return manifest

    def save_manifest(self, directory, manifest):
        (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_modified_missing_or_foreign_runtime_is_rejected(self):
        for change in ('model', 'missing', 'platform', 'sources'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                manifest = self.make_runtime(directory)
                self.assertEqual(builder.verify_runtime(directory), manifest)
                if change == 'model':
                    (directory / 'tessdata/eng.traineddata').write_bytes(b'foreign model')
                elif change == 'missing':
                    manifest['files'].pop('tessdata/eng.traineddata')
                elif change == 'platform':
                    manifest['platform'] = 'another-platform'
                else:
                    manifest['sources'] = {}
                self.save_manifest(directory, manifest)
                with self.assertRaises(ValueError):
                    builder.verify_runtime(directory)

    def test_notices_include_all_three_ocr_components_and_their_license_text(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.make_runtime(directory)
            text = notices.generate_notices(ocr_runtime=directory)
        for name in ('tesseract', 'leptonica', 'tessdata_fast'):
            self.assertIn(f'Bundled OCR: {name}', text)
            self.assertIn(f'Synthetic LICENSE.{name}', text)
            self.assertIn(builder.SOURCES[name]['sha256'], text)
