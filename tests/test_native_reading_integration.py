"""Reading features share panes without changing the PDF save authority."""
from unittest.mock import patch

from test_library_window import LibraryWindowCase
from test_native_support import needs_qt, pdf_available

if pdf_available:
    from paperassist.native.engine import Link, Rect
    from paperassist.native.rules import CANCEL, DISCARD, SAVE


@needs_qt
class ReadingIntegrationTests(LibraryWindowCase):
    def window(self):
        window = self.make_window()
        window.acquisition.main_opened = lambda document_id: None
        return window

    def draft(self, window, text="Unfinished note"):
        window.comments.compose()
        window.comments.panel.text.setPlainText(text)

    def test_draft_survives_switching_and_cancel_then_saves_without_a_pdf_revision(self):
        window = self.window()
        first = self.open_document(window, self.docs[0])
        self.draft(window)
        self.assertTrue(window.comments.has_draft(first.main))
        self.assertFalse(first.main.modified)
        self.open_document(window, self.docs[1])
        window.tabs.setCurrentWidget(first)
        self.assertEqual(window.comments.panel.text.toPlainText(), "Unfinished note")
        window.ask_decision = lambda pane: CANCEL
        self.assertFalse(window.close_tab(0))
        self.assertTrue(window.comments.has_draft(first.main))
        window.ask_decision = lambda pane: SAVE
        self.assertTrue(window.close_tab(0))
        self.assertEqual(self.workspace.library.get_notes(self.docs[0]["id"])[0]["text"], "Unfinished note")
        self.assertEqual(self.workspace.library.get_document(self.docs[0]["id"])["revision"], 0)

    def test_all_close_decisions_precede_draft_discards_and_pdf_saves(self):
        window = self.window()
        tab = self.open_document(window, self.docs[0])
        self.draft(window)
        quick = window.open_library_document_in_quick_view(self.docs[1]["id"])
        quick.view.doc.add_highlight(0, [Rect(30, 60, 120, 74)])
        quick.view.edited.emit()
        answers = iter([DISCARD, CANCEL])
        window.ask_decision = lambda pane: next(answers)
        self.assertFalse(window.close_tab(0))
        self.assertTrue(window.comments.has_draft(tab.main))
        self.assertTrue(quick.modified)
        answers = iter([DISCARD, SAVE])
        self.assertTrue(window.close_tab(0))
        self.assertEqual(self.workspace.library.get_notes(self.docs[0]["id"]), [])
        self.assertEqual(self.workspace.library.get_document(self.docs[1]["id"])["revision"], 1)

    def test_a_failed_note_save_keeps_the_draft_and_tab(self):
        window = self.window()
        tab = self.open_document(window, self.docs[0])
        self.draft(window)
        window.ask_decision = lambda pane: SAVE
        with patch.object(self.workspace.library, "save_notes", side_effect=OSError("Disk full")):
            self.assertFalse(window.close_tab(0))
        self.assertIs(window.tab(), tab)
        self.assertTrue(window.comments.has_draft(tab.main))
        self.assertEqual(self.workspace.library.get_document(self.docs[0]["id"])["revision"], 0)

    def test_same_document_panes_share_notes_without_locking_pdf_edits(self):
        window = self.window()
        tab = self.open_document(window, self.docs[0])
        quick = window.open_library_document_in_quick_view(self.docs[0]["id"])
        self.assertIs(tab.main.view.notes_model, quick.view.notes_model)
        self.draft(window, "Shared note")
        self.assertTrue(window.comments.save_draft(quick))
        self.assertEqual(tab.main.view.notes_model.notes[0]["text"], "Shared note")
        self.assertFalse(tab.main.view.locked or quick.view.locked)
        window.tab().activate(tab.main)
        self.assertIs(window.comments.panel.view, tab.main.view)

    def test_new_tabs_offer_acquisition_but_switches_and_quick_views_do_not(self):
        window = self.window()
        with patch.object(window.acquisition, "main_opened") as opened:
            first = self.open_document(window, self.docs[0])
            self.open_document(window, self.docs[1])
            window.tabs.setCurrentWidget(first)
            window.open_library_document(self.docs[0]["id"])
            window.open_library_document_in_quick_view(self.docs[2]["id"])
            self.assertEqual([call.args[0] for call in opened.call_args_list], [self.docs[0]["id"], self.docs[1]["id"]])
            window.promote_quick()
            self.assertEqual(opened.call_args.args, (self.docs[2]["id"],))

    def test_internal_hook_consumes_only_handled_links_and_outlines_never_dirty_pdf(self):
        window = self.window()
        view = self.open_document(window, self.docs[0]).main.view
        link = Link(Rect(0, 0, 10, 10), None, 1, 20)
        view.internal_link_handler = lambda link: True
        view.follow(link)
        self.assertEqual(view.history, [])
        view.internal_link_handler = lambda link: False
        view.follow(link)
        self.assertEqual(len(view.history), 1)
        view.outline_rects([(0, Rect(20, 20, 80, 35))], milliseconds=10)
        self.assertFalse(view.doc.modified)
        self.pump(lambda: not view._outlines)
