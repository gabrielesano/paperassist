"""A copied Linux bundle accepts repeated launches without Python or source paths."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'dist/PaperAssist'


@unittest.skipUnless(sys.platform.startswith('linux') and (BUNDLE / 'PaperAssist').is_file(),
                     'Build the Linux bundle first')
class FrozenFileDeliveryTests(unittest.TestCase):
    def test_copied_bundle_reuses_its_owner_and_managed_copies(self):
        with tempfile.TemporaryDirectory(prefix='paperassist-open-') as temporary:
            root = Path(temporary)
            copied = root / 'Application with spaces'
            shutil.copytree(BUNDLE, copied, symlinks=True)
            binary = copied / 'PaperAssist'
            first, second = root / 'First è % paper.pdf', root / 'Second paper.pdf'
            original = (ROOT / 'tests/fixtures/pdfjs-annotations.pdf').read_bytes()
            first.write_bytes(original)
            second.write_bytes(original + b'\n%Second external PDF\n')
            library = root / 'library'
            env = {'HOME': str(root / 'home'), 'XDG_CONFIG_HOME': str(root / 'config'),
                   'PATH': '/nonexistent', 'QT_QPA_PLATFORM': os.environ.get('QT_QPA_PLATFORM', 'offscreen')}
            for key in ('DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR'):
                if key in os.environ:
                    env[key] = os.environ[key]
            args = [str(binary), '--data-dir', str(library)]
            with (root / 'errors.log').open('w+') as errors:
                owner = subprocess.Popen([*args, str(first)], cwd=root, env=env, stderr=errors)
                try:
                    def documents(expected):
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline and owner.poll() is None:
                            try:
                                with sqlite3.connect((library / 'library.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
                                    rows = db.execute('SELECT id, sha256, revision FROM documents ORDER BY id').fetchall()
                                if len(rows) == expected:
                                    return rows
                            except sqlite3.OperationalError:
                                pass
                            time.sleep(.05)
                        errors.seek(0)
                        self.fail('Frozen owner did not import the expected papers: ' + errors.read())

                    first_rows = documents(1)
                    for files in ((str(first), str(second)), (first.as_uri(), str(second)), ()):
                        result = subprocess.run([*args, *files], cwd=root, env=env, capture_output=True,
                                                text=True, timeout=15)
                        self.assertEqual(result.returncode, 0, result.stderr)
                    rows = documents(2)
                    self.assertEqual(rows[0], first_rows[0])
                    self.assertTrue(all(row[2] == 0 for row in rows))
                    self.assertEqual({row[1] for row in rows}, {hashlib.sha256(path.read_bytes()).hexdigest()
                                                               for path in (first, second)})
                    deadline = time.monotonic() + 10
                    while owner.poll() is None and time.monotonic() < deadline:
                        owner.terminate()  # The close guard may wait for an accepted import to finish.
                        try:
                            owner.wait(timeout=.3)
                        except subprocess.TimeoutExpired:
                            pass
                    self.assertEqual(owner.poll(), 0)
                    self.assertEqual(first.read_bytes(), original)
                    self.assertEqual(second.read_bytes(), original + b'\n%Second external PDF\n')
                    self.assertEqual(list((library / 'working').glob('*.pdf')), [])
                finally:
                    if owner.poll() is None:
                        owner.kill()
                    owner.wait(timeout=5)
