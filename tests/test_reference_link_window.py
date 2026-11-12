"""Citation counts and navigation agree in a native window after cached-analysis migration."""
import hashlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_native_support import needs_qt
from test_reference_links import make_link_paper


@needs_qt
class ReferenceLinkWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        from paperassist.native.renderer import RenderClient
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()

    def pump(self, condition):
        end = time.monotonic() + 15
        while time.monotonic() < end and not condition():
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(condition(), 'Timed out waiting for the native reader')

    def test_count_button_visits_same_page_and_later_appendices_without_saving(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        from paperassist import references
        from paperassist.native.window import MainWindow
        from paperassist.workspace import Workspace

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.pdf'
            make_link_paper(source)
            digest = hashlib.sha256(source.read_bytes()).digest()
            with patch('paperassist.paths.config_file', return_value=root / 'config.json'):
                workspace = Workspace(root / 'library')
                window = None
                try:
                    document = workspace.library.import_stream(io.BytesIO(source.read_bytes()), 'source.pdf')
                    original = references.analyze(workspace.library, document['id'])
                    original['extraction_version'] = references.EXTRACTION_VERSION - 1
                    original['references'][0]['retrieval'] = {'status': 'not_found'}
                    references._save(workspace.library, document['id'], original)
                    window = MainWindow(self.client, workspace)
                    window.acquisition.main_opened = lambda *_: None
                    window.resize(1400, 900)
                    window.show()
                    view = window.open_library_document(document['id']).main.view
                    panel = window.details
                    self.pump(lambda: bool(panel.refs) and view.has_page(0))
                    ref = panel.refs[0]
                    self.assertEqual(ref['citation_count'], 5)
                    self.assertEqual(ref['retrieval']['status'], 'not_found')
                    item = panel.list.item(0)
                    QTest.mouseClick(panel.list.viewport(), Qt.LeftButton, pos=panel.list.visualItemRect(item).center())
                    self.pump(lambda: panel.list.itemWidget(item) is not None)
                    card = panel.list.itemWidget(item)
                    button = next(button for button in card.findChildren(QPushButton)
                                  if button.accessibleName() == 'Go to the next citation in the text')
                    self.assertEqual(button.text(), '5 detected')
                    for step in range(6):
                        QTest.mouseClick(button, Qt.LeftButton)
                        self.pump(lambda: panel.series.get(view, (None, -1))[1] == step % 5)
                        self.assertTrue(view._outlines)
                        self.assertEqual(panel.series[view][2], 5)
                    self.assertEqual([event['page'] for event in panel.located[view][ref['id']]], [1, 1, 1, 2, 3])
                    self.assertFalse(view.doc.modified)
                    self.assertEqual(workspace.library.get_document(document['id'])['revision'], 0)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)
                finally:
                    if window is not None:
                        window.close()
                        window.deleteLater()
                    workspace.close()


if __name__ == '__main__':
    unittest.main()
