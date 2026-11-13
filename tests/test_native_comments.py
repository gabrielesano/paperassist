"""Sidecar notes preserve the web format and PDF-coordinate phrase geometry."""
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import RectangleObject

from paperassist.library import Library
from test_native_support import needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from paperassist.native.comments import NotesModel, capture_anchor, line_bands, note_color, note_rects, quote
    from paperassist.native.document import PdfDocument
    from paperassist.native.engine import Pdf, Rect
    from paperassist.native.library_source import LibrarySource
    from paperassist.native.view import PdfView

    class Client(QObject):
        page_ready = Signal(int, int, float, int, int, bytes, float)

        def open(self, data):
            return 1

        def want(self, *args):
            pass

        def close(self, *args):
            pass


FIXTURES = Path(__file__).parent / "fixtures"


@needs_qt
class CommentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = Library(temporary.name)
        self.data = (FIXTURES / "pdfjs-annotations.pdf").read_bytes()
        self.document = self.library.import_stream(io.BytesIO(self.data), "synthetic.pdf")
        self.notes = json.loads((FIXTURES / "pdfjs-notes.json").read_text())
        self.library.save_notes(self.document["id"], self.notes)
        self.model = NotesModel(self.library, self.document["id"])
        self.client = Client()
        self.view = self.make_view()

    def make_view(self):
        view = PdfView(PdfDocument(LibrarySource(self.library, self.document)), self.client)
        view.set_comment_model(self.model)
        self.addCleanup(view.doc.close)
        self.addCleanup(view.deleteLater)
        return view

    def test_web_fixture_geometry_marks_the_same_two_lines(self):
        bands = note_rects(self.notes[0], self.view.doc.pdf)
        self.assertEqual(len(bands), 2)
        self.assertEqual([page for page, _ in bands], [0, 0])
        words = self.view.doc.words(0)
        for (_, rect), text in zip(bands, ("phrase", "Second")):
            word = next(word for word in words if word[4] == text)
            self.assertTrue(rect.contains((word[0] + word[2]) / 2, (word[1] + word[3]) / 2))

    def test_legacy_selection_and_page_notes_are_preserved(self):
        notes = [{"page": 2, "text": "legacy", "selection": "old quote", "future": {"id": 9}},
                 {"page": 1, "text": "page only"}]
        self.library.save_notes(self.document["id"], notes)
        self.model.reload()
        self.assertEqual(self.model.notes, notes)
        self.assertEqual(quote(notes[0]), "old quote")
        self.assertEqual(note_rects(notes[1], self.view.doc.pdf), [])

    def test_recolor_keeps_unknown_fields_and_order_and_does_not_save_pdf(self):
        note = {**self.notes[0], "future": [1, {"key": "value"}]}
        self.library.save_notes(self.document["id"], [note, {"page": 2, "text": "second"}])
        self.assertTrue(self.model.update(note, "#79c5df"))
        saved = self.library.get_notes(self.document["id"])
        self.assertEqual(saved[0], {**note, "color": "#79c5df"})
        self.assertEqual(saved[1], {"page": 2, "text": "second"})
        self.assertEqual(self.library.get_document(self.document["id"])["revision"], 0)
        self.assertEqual(self.library.get_path(self.document["id"]).read_bytes(), self.data)

    def test_deletion_undo_restores_original_position_and_has_only_one_level(self):
        first = self.notes[0]
        second = {"page": 2, "text": "second"}
        self.model.append(second)
        self.assertTrue(self.model.update(first))
        self.assertEqual(self.model.notes, [second])
        self.assertTrue(self.model.undo_delete())
        self.assertEqual(self.model.notes, [first, second])
        self.assertFalse(self.model.undo_delete())

    def test_last_pane_release_drops_cached_model_and_reopen_reloads_notes_without_undo(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QMainWindow
        from paperassist.native.comments import CommentsController

        class Pane(QObject):
            released = Signal()

            def __init__(self, view):
                super().__init__()
                self.view, self.key = view, 'same-document'

        host = QMainWindow()
        self.addCleanup(host.deleteLater)
        host.workspace = SimpleNamespace(library=self.library)
        active = [None]
        host.pane = lambda: active[0]
        controller = CommentsController(host)
        self.addCleanup(controller.panel.deleteLater)
        first, second = Pane(self.make_view()), Pane(self.make_view())
        controller.bind(first)
        controller.bind(second)
        model = first.view.notes_model
        self.assertIs(second.view.notes_model, model)
        self.assertTrue(model.update(self.notes[0]))
        self.assertIsNotNone(model.deleted)
        first.released.emit()
        self.assertIs(controller.models[first.key], model)
        self.assertIsNone(first.view.notes_model)
        second.released.emit()
        self.assertNotIn(first.key, controller.models)
        self.assertIsNone(second.view.notes_model)
        external = [{'page': 1, 'text': 'Changed after closing the tab'}]
        self.library.save_notes(self.document['id'], external)
        reopened = Pane(self.make_view())
        controller.bind(reopened)
        self.assertEqual(reopened.view.notes_model.notes, external)
        self.assertIsNone(reopened.view.notes_model.deleted)
        self.assertIsNot(reopened.view.notes_model, model)
        reopened.released.emit()

    def test_edit_refuses_a_record_that_changed_since_it_was_selected(self):
        self.library.save_notes(self.document["id"], [{**self.notes[0], "text": "changed elsewhere"}])
        self.assertFalse(self.model.update(self.notes[0], "#000000"))
        self.assertIn("changed", self.model.error)
        self.assertEqual(self.library.get_notes(self.document["id"])[0]["text"], "changed elsewhere")

    def test_failed_write_retains_saved_notes_and_reports_failure(self):
        before = deepcopy(self.model.notes)
        with patch.object(self.library, "save_notes", side_effect=OSError("disk full")):
            self.assertFalse(self.model.append({"page": 1, "text": "new"}))
        self.assertEqual(self.model.notes, before)
        self.assertIn("disk full", self.model.error)

    def test_smallest_band_wins_and_later_note_wins_equal_size_ties(self):
        def note(text, rect):
            return {"page": 1, "text": text, "anchor": {"version": 1, "pages": [{"page": 1, "rects": [rect]}]}}
        large, small, later = note("large", [10, 10, 100, 100]), note("small", [20, 20, 40, 40]), note("later", [20, 20, 40, 40])
        self.library.save_notes(self.document["id"], [small, large, later])
        self.model.reload()
        x, y = self.view.doc.pdf.frame(0).point(30, 30)
        self.assertEqual(self.view.comment_at(0, x, y), later)

    def test_two_panes_share_live_notes_and_deletion_indicators(self):
        other = self.make_view()
        self.assertEqual(len(other._comment_bands[0]), 2)
        self.model.update(self.notes[0])
        self.assertEqual(self.view._comment_bands, {})
        self.assertEqual(other._comment_bands, {})
        self.model.undo_delete()
        self.assertEqual(len(other._comment_bands[0]), 2)

    def test_selected_comment_delete_and_undo_do_not_dirty_pdf(self):
        self.view.select_comment(self.notes[0])
        self.view.locked = True
        self.view.delete_annotation()
        self.assertEqual(self.model.notes, [])
        self.view.undo()
        self.assertEqual(self.model.notes, self.notes)
        self.assertFalse(self.view.doc.modified)

    def test_escape_deselects_comment(self):
        self.view.select_comment(self.notes[0])
        QTest.keyClick(self.view, Qt.Key_Escape)
        self.assertIsNone(self.view.selected_comment)

    def test_native_selection_anchor_uses_pdf_space_on_all_rotations_and_crops(self):
        for rotation in (0, 90, 180, 270):
            writer = PdfWriter(clone_from=io.BytesIO(self.data))
            writer.pages[0].cropbox = RectangleObject((20, 30, 580, 760))
            writer.pages[0].rotate(rotation)
            output = io.BytesIO()
            writer.write(output)
            pdf = Pdf(output.getvalue())
            try:
                rect = pdf.frame(0).rect(50, 530, 300, 550)
                class Selection:
                    selection_lines = [(0, rect, "Synthetic phrase")]
                    doc = type("Document", (), {"pdf": pdf})()
                    def selected_text(self):
                        return "Synthetic phrase"
                anchor = capture_anchor(Selection())
                values = anchor["anchor"]["pages"][0]["rects"][0]
                for actual, expected in zip(values, (50, 530, 300, 550)):
                    self.assertAlmostEqual(actual, expected)
                self.assertEqual(note_rects(anchor, pdf), [(0, rect)])
            finally:
                pdf.close()

    def test_multi_page_selection_retains_each_page_and_quote(self):
        self.view.selection_lines = [(0, Rect(50, 200, 100, 215), "first"), (1, Rect(40, 100, 90, 115), "second")]
        note = capture_anchor(self.view)
        self.assertEqual(note["page"], 1)
        self.assertEqual(note["quote"], "first\nsecond")
        self.assertEqual([part["page"] for part in note["anchor"]["pages"]], [1, 2])

    def test_malformed_geometry_is_ignored_without_changing_records(self):
        note = {"anchor": {"version": 1, "pages": [None, {"page": 0, "rects": [[0, 0, 4, 4]]},
            {"page": 1, "rects": [[0, 0, float("nan"), 4], [0, 0, 0, 0], [1], "bad"]}]}}
        self.assertEqual(note_rects(note, self.view.doc.pdf), [])
        self.assertEqual(note_color({"color": "url(bad)"}), "#aa700f")

    def test_legacy_fragments_merge_per_line_and_leave_column_gutters_clear(self):
        rects = [Rect(60, 50, 100, 60), Rect(104, 50, 150, 60), Rect(60, 49, 150, 61),
                 Rect(10, 62, 90, 72), Rect(94, 62, 190, 72), Rect(10, 62, 190, 72),
                 Rect(10, 74, 40, 84), Rect(44, 74, 120, 84)]
        self.assertEqual(sorted(line_bands(rects), key=lambda rect: rect.y0),
                         [Rect(60, 49, 150, 61), Rect(10, 62, 190, 72), Rect(10, 74, 120, 84)])
        self.assertEqual(line_bands([Rect(10, 50, 60, 60), Rect(64, 50, 90, 60), Rect(140, 50, 190, 60)]),
                         [Rect(10, 50, 90, 60), Rect(140, 50, 190, 60)])

    def test_rotated_fragments_merge_along_the_baseline(self):
        self.assertEqual(line_bands([Rect(50, 10, 60, 40), Rect(50, 44, 60, 90),
                                    Rect(50, 10, 60, 90), Rect(62, 10, 72, 90)], vertical=True),
                         [Rect(50, 10, 60, 90), Rect(62, 10, 72, 90)])

    def test_focus_after_zoom_uses_phrase_position_and_preserves_pdf_bytes(self):
        self.view.zoom_by(1.5)
        self.view.select_comment(self.notes[0], scroll=True)
        self.assertEqual(self.view._comment_focus, note_rects(self.notes[0], self.view.doc.pdf))
        self.assertEqual(self.view.doc.pdf.data, self.data)
        self.assertFalse(self.view.doc.modified)

    def test_panel_rebuilds_only_on_note_or_view_change_and_keeps_composer_cursor_and_focus(self):
        from types import SimpleNamespace
        from PySide6.QtGui import QTextCursor
        from PySide6.QtWidgets import QMainWindow
        from paperassist.native.comments import CommentsPanel

        host = QMainWindow()
        self.addCleanup(host.deleteLater)
        pane = SimpleNamespace(view=self.view)
        controller = SimpleNamespace(
            compose=lambda: None,
            save_draft=lambda p: None,
            discard_draft=lambda p: None,
            draft_changed=SimpleNamespace(emit=lambda: None),
            window=SimpleNamespace(pane=lambda: pane),
        )
        panel = CommentsPanel(controller)
        host.setCentralWidget(panel)
        host.resize(400, 600)
        host.show()
        host.activateWindow()
        self.app.processEvents()

        self.model.append({"page": 2, "text": "Second comment"})
        with patch.object(panel, "_build_rows", wraps=panel._build_rows) as build_rows:
            panel.refresh(self.view)
            self.assertEqual(build_rows.call_count, 1)
            row_widgets = [row for _, row in panel._rows]

            # Repeated refreshes and comment selection changes update styles in place without rebuilding.
            panel.refresh(self.view)
            panel.refresh(self.view)
            self.view.select_comment(self.model.notes[0])
            panel.refresh(self.view)
            self.assertIn("#e6eeeb", row_widgets[0].styleSheet())
            self.assertEqual(row_widgets[1].styleSheet(), "")
            self.view.select_comment(self.model.notes[1])
            panel.refresh(self.view)
            self.assertEqual(row_widgets[0].styleSheet(), "")
            self.assertIn("#e6eeeb", row_widgets[1].styleSheet())
            self.view.select_comment(None)
            panel.refresh(self.view)
            self.assertEqual(row_widgets[0].styleSheet(), "")
            self.assertEqual(row_widgets[1].styleSheet(), "")
            self.assertEqual(build_rows.call_count, 1)

            # Draft composer keeps text, mid-word cursor position, and focus across refreshes.
            self.view.comment_draft = {"page": 1, "text": "Alpha Beta"}
            panel.refresh(self.view)
            panel.text.setFocus()
            cursor = panel.text.textCursor()
            cursor.setPosition(5, QTextCursor.MoveAnchor)
            panel.text.setTextCursor(cursor)
            self.app.processEvents()
            self.assertIs(self.app.focusWidget(), panel.text)
            for _ in range(3):
                panel.refresh(self.view)
            self.assertEqual(panel.text.toPlainText(), "Alpha Beta")
            self.assertEqual(panel.text.textCursor().position(), 5)
            self.assertIs(self.app.focusWidget(), panel.text)
            self.assertEqual(build_rows.call_count, 1)

            # Changing notes or switching the bound view rebuilds once each.
            self.model.append({"page": 1, "text": "Third comment"})
            panel.refresh(self.view)
            self.assertEqual(build_rows.call_count, 2)
            other = self.make_view()
            panel.refresh(other)
            self.assertEqual(build_rows.call_count, 3)
            panel.refresh(other)
            self.assertEqual(build_rows.call_count, 3)


if __name__ == "__main__":
    unittest.main()
