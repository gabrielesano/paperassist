"""PDF citation clicks open local papers or reveal missing references."""
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_native_support import PySide6, needs_qt, pdf_available

if pdf_available and PySide6:
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, TextStringObject
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
    from paperassist import references
    from paperassist.native.engine import Pdf, Rect
    from paperassist.native.renderer import RenderClient
    from paperassist.native.references_panel import ReferencesPanel
    from paperassist.native.window import MainWindow
    from paperassist.workspace import Workspace
    from test_library_window import cited_pdf


@needs_qt
class CitationRevealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()

    def pump(self, condition=lambda: False, timeout=.2):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(.002)
        return condition()

    def test_click_history_is_per_source_and_reference(self):
        panel = ReferencesPanel(None)
        first = {"id": 1, "local_document_id": 10}
        second = {"id": 2, "local_document_id": 20}
        missing = {"id": 3}
        with patch.object(panel, "_reference_open") as opened, patch.object(panel, "_reveal_reference") as revealed:
            panel.document_id = 100
            for ref in (first, first, first, second, second, missing, missing):
                panel._follow_citation(ref)
            self.assertEqual([call.args[0] for call in opened.call_args_list], [first, second])
            self.assertEqual([call.args[0] for call in revealed.call_args_list],
                             [first, first, second, missing, missing])
            panel.document_id = 200
            panel._follow_citation(first)
            self.assertEqual(opened.call_count, 3)
            panel.document_id = 100
            panel._follow_citation(first)
            self.assertEqual(revealed.call_count, 6)
        panel.deleteLater()

    def test_number_only_link_migrates_numbered_bibliography_and_follows_then_reveals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            writer = PdfWriter()
            font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Courier")}))
            for content in ("BT /F1 11 Tf 40 430 Td ([) Tj 8 0 Td (1) Tj 8 0 Td (]) Tj ET",
                            "BT /F1 11 Tf 40 430 Td (References) Tj 0 -30 Td "
                            "([1] Ada Lovelace. A study of coronal loops. 2024.) Tj ET"):
                page = writer.add_blank_page(400, 500)
                page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
                stream = DecodedStreamObject()
                stream.set_data(content.encode("ascii"))
                page[NameObject("/Contents")] = writer._add_object(stream)
            writer.add_named_destination_array(TextStringObject("cite.loops"), ArrayObject([
                writer.pages[1].indirect_reference, NameObject("/XYZ"), FloatObject(40), FloatObject(413), FloatObject(0)]))
            writer.add_annotation(0, DictionaryObject({NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (48, 428, 55, 440)),
                NameObject("/Dest"): TextStringObject("cite.loops")}))
            output = io.BytesIO()
            writer.write(output)
            with patch("paperassist.paths.config_file", return_value=root / "config.json"):
                workspace = Workspace(root / "library")
                window = None
                try:
                    main = workspace.library.import_stream(io.BytesIO(output.getvalue()), "numbered.pdf")
                    path = cited_pdf(root / "other.pdf", pages=3)
                    other = workspace.library.import_stream(io.BytesIO(path.read_bytes()), "other.pdf")
                    payload = references.analyze(workspace.library, main["id"])
                    ref = payload["references"][0]
                    self.assertEqual(ref["destinations"], ["cite.loops"])
                    references.link(workspace.library, main["id"], ref["id"], other["id"])
                    payload = references.get(workspace.library, main["id"])
                    payload["destination_version"] = references.DESTINATION_VERSION - 1
                    payload["references"][0]["destinations"] = []
                    references._save(workspace.library, main["id"], payload)
                    window = MainWindow(self.client, workspace)
                    window.acquisition.main_opened = lambda *_: None
                    window.show()
                    tab = window.open_library_document(main["id"])
                    view, panel = tab.main.view, window.details
                    self.assertTrue(self.pump(lambda: panel.list.count() == 1 and view.has_page(0), 10))
                    self.assertEqual(panel.refs[0]["destinations"], ["cite.loops"])
                    self.assertTrue(panel.refs[0]["manual_link"])
                    view._set_zoom(1.2)
                    view.goto(0)
                    self.pump()
                    before = (view.verticalScrollBar().value(), list(view.history))
                    for click in range(2):
                        position = view._band(0, view.doc.links(0)[0].rect).center().toPoint()
                        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, position)
                        self.pump()
                        self.assertEqual(tab.quick.view.doc.source.document_id, other["id"])
                        self.assertEqual((view.verticalScrollBar().value(), list(view.history)), before)
                        if click == 0:
                            quick = tab.quick
                        else:
                            self.assertIs(tab.quick, quick)
                            self.assertEqual(panel.expanded, ref["id"])
                finally:
                    if window:
                        for pane in window.all_panes():
                            pane.release()
                        window.close()
                        window.deleteLater()
                        self.app.processEvents()
                    workspace.close()

    def test_mouse_click_opens_local_reference_and_reveals_missing_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = cited_pdf(root / "main.pdf", pages=3)
            pdf = Pdf(path.read_bytes())
            words = [word for word in pdf.words(0) if word[4].rstrip(".") in ("[1]", "[2]")]
            self.assertEqual(len(words), 2)
            pdf.close()
            writer = PdfWriter()
            writer.append(path)
            writer.add_named_destination("unmapped-citation", 2)
            for x0, y0, x1, y1, *_ in words:
                writer.add_annotation(0, DictionaryObject({
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (x0, 500-y1, x1, 500-y0)),
                    NameObject("/Dest"): TextStringObject("unmapped-citation"),
                }))
            output = io.BytesIO()
            writer.write(output)
            with patch("paperassist.paths.config_file", return_value=root / "config.json"):
                workspace = Workspace(root / "library")
                window = None
                try:
                    main = workspace.library.import_stream(io.BytesIO(output.getvalue()), "main.pdf")
                    other_path = cited_pdf(root / "other.pdf", pages=4)
                    other = workspace.library.import_stream(io.BytesIO(other_path.read_bytes()), "other.pdf")
                    existing_path = cited_pdf(root / "existing.pdf", pages=5)
                    existing = workspace.library.import_stream(io.BytesIO(existing_path.read_bytes()), "existing.pdf")
                    payload = references.analyze(workspace.library, main["id"])
                    first = next(ref for ref in payload["references"] if ref["label"] == "1")
                    references.link(workspace.library, main["id"], first["id"], other["id"])
                    window = MainWindow(self.client, workspace)
                    window.acquisition.main_opened = lambda *_: None
                    window.resize(1400, 900)
                    window.show()
                    tab = window.open_library_document(main["id"])
                    view, panel = tab.main.view, window.details
                    self.assertTrue(self.pump(lambda: panel.list.count() == 2 and view.has_page(0), 10))
                    quick = window.open_library_document_in_quick_view(existing["id"])
                    self.assertTrue(self.pump(lambda: quick.view.has_page(0), 10))
                    panel.filter.setText("no match")
                    window.comments.show_mode("comments")
                    window.set_columns(details=False)
                    view._set_zoom(1.2)
                    view.goto(0)
                    self.pump()
                    link = view.doc.links(0)[0]
                    position = view._band(0, Rect(*link.rect)).center().toPoint()
                    self.assertTrue(view.viewport().rect().contains(position))
                    before = (view.zoom, view.verticalScrollBar().value(), view.horizontalScrollBar().value(),
                              list(view.history), view.doc.pdf.data)
                    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, position)
                    self.pump()
                    self.assertTrue(window.details_column.isVisible())
                    self.assertEqual(window.detail_switch.mode, "references")
                    self.assertIsNot(tab.quick, quick)
                    self.assertEqual(tab.quick.view.doc.source.document_id, other["id"])
                    quick = tab.quick
                    self.assertEqual((view.zoom, view.verticalScrollBar().value(), view.horizontalScrollBar().value(),
                                      list(view.history), view.doc.pdf.data), before)
                    metadata_before = references.get(workspace.library, main["id"])
                    quick.view.goto(2)
                    self.pump()
                    quick_before = (quick.view.zoom, quick.view.verticalScrollBar().value(),
                                    quick.view.horizontalScrollBar().value(), list(quick.view.history),
                                    quick.view.doc.pdf.data)
                    for click in range(2):
                        panel.filter.setText("no match")
                        if click == 0:
                            window.comments.show_mode("comments")
                            window.set_columns(details=False)
                            self.pump()
                        else:
                            quick_before = (quick.view.zoom, quick.view.verticalScrollBar().value(),
                                            quick.view.horizontalScrollBar().value(), list(quick.view.history),
                                            quick.view.doc.pdf.data)
                        position = view._band(0, Rect(*view.doc.links(0)[0].rect)).center().toPoint()
                        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, position)
                        self.pump()
                        self.assertTrue(window.details_column.isVisible())
                        self.assertEqual(window.detail_switch.mode, "references")
                        self.assertEqual(panel.expanded, first["id"])
                        self.assertEqual(panel.filter.text(), "")
                        self.assertEqual(panel.list.currentItem().data(Qt.UserRole + 1), first["id"])
                        self.assertIs(tab.quick, quick)
                        if click == 1:
                            self.assertEqual((quick.view.zoom, quick.view.verticalScrollBar().value(),
                                              quick.view.horizontalScrollBar().value(), list(quick.view.history),
                                              quick.view.doc.pdf.data), quick_before)
                    self.assertEqual(references.get(workspace.library, main["id"]), metadata_before)
                    second = next(ref for ref in panel.refs if ref["label"] == "2")
                    link = view.doc.links(0)[1]
                    position = view._band(0, Rect(*link.rect)).center().toPoint()
                    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, position)
                    self.pump()
                    self.assertEqual(panel.expanded, second["id"])
                    self.assertEqual(panel.filter.text(), "")
                    self.assertEqual(panel.list.currentItem().data(Qt.UserRole + 1), second["id"])
                    self.assertTrue(panel.list.viewport().rect().intersects(panel.list.visualItemRect(panel.list.currentItem())))
                    self.assertIs(tab.quick, quick)
                    self.assertEqual((view.zoom, view.verticalScrollBar().value(), view.horizontalScrollBar().value(),
                                      list(view.history), view.doc.pdf.data), before)
                finally:
                    if window:
                        for pane in window.all_panes():
                            pane.release()
                        window.close()
                        window.deleteLater()
                        self.app.processEvents()
                    workspace.close()
