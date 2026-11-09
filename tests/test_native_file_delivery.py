"""Two real invocations share the right owner and leave drafts and PDF bytes intact."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from test_native_support import make_pdf, needs_qt


@needs_qt
class NativeFileDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.env = {**os.environ, 'HOME': str(self.root), 'APPDATA': str(self.root / 'roaming'),
                    'LOCALAPPDATA': str(self.root / 'local'), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                    'XDG_DATA_HOME': str(self.root / 'data')}
        self.env.pop('PAPERASSIST_DATA_DIR', None)

    def owner(self, name, source):
        folder = self.root / name
        folder.mkdir()
        errors = (folder / 'errors.log').open('w')
        self.addCleanup(errors.close)
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('native_open_driver.py')),
                                    str(folder), str(source)], env=self.env, stderr=errors)

        def stop():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.addCleanup(stop)
        return folder, process

    def status(self, folder, process, predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            try:
                status = json.loads((folder / 'status.json').read_text())
                if predicate(status):
                    return status
            except (OSError, ValueError):
                pass
            time.sleep(.03)
        self.fail(f'Owner did not reach the requested state: {(folder / "errors.log").read_text()}')

    def command(self, folder, action, sequence):
        pending = folder / 'command.tmp'
        pending.write_text(json.dumps({'action': action, 'sequence': sequence}))
        pending.replace(folder / 'command.json')

    def launch(self, folder, *files):
        result = subprocess.run([sys.executable, '-m', 'paperassist', '--data-dir', str(folder / 'library'),
                                 *map(str, files)], env=self.env, capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_second_launch_preserves_dirty_document_and_routes_each_library(self):
        first = make_pdf(self.root / 'First è % paper.pdf', pages=2)
        second = make_pdf(self.root / 'Second paper.pdf', pages=3)
        before = first.read_bytes(), second.read_bytes()
        folder, process = self.owner('one', first)
        initial = self.status(folder, process, lambda value: value['tabs'] == 1)
        self.command(folder, 'dirty', 1)
        dirty = self.status(folder, process, lambda value: value['sequence'] == 1)
        identity = dirty['panes'][0]['identity']
        self.assertTrue(dirty['panes'][0]['modified'])
        self.assertTrue(dirty['panes'][0]['draft'])
        self.launch(folder, first, second)
        opened = self.status(folder, process, lambda value: value['tabs'] == 2)
        self.assertEqual(opened['pid'], initial['pid'])
        self.assertEqual(opened['panes'][0], dirty['panes'][0])
        self.launch(folder, first)
        reopened = self.status(folder, process, lambda value: value['active'] == dirty['active'])
        self.assertEqual(reopened['panes'][0]['identity'], identity)
        self.assertEqual(reopened['tabs'], 2)
        self.assertTrue(all(document['revision'] == 0 for document in reopened['documents']))
        other, other_process = self.owner('two', second)
        self.status(other, other_process, lambda value: value['tabs'] == 1)
        self.launch(other, first)
        self.status(other, other_process, lambda value: value['tabs'] == 2)
        self.launch(folder)
        self.command(folder, 'snapshot', 2)
        unchanged = self.status(folder, process, lambda value: value['sequence'] == 2)
        self.assertEqual(unchanged['panes'], reopened['panes'])
        self.assertEqual((first.read_bytes(), second.read_bytes()), before)
        for target, owner in ((other, other_process), (folder, process)):
            self.command(target, 'close', 3)
            self.assertEqual(owner.wait(timeout=10), 0)
        self.assertEqual((first.read_bytes(), second.read_bytes()), before)
