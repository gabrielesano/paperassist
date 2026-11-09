"""Quick view and save rules on a real library: revisions, immutable originals and nothing written without Save."""
import hashlib
from unittest.mock import patch

from test_library_window import LibraryWindowCase
from test_native_support import needs_qt, pdf_available

if pdf_available:
    from paperassist import settings
    from paperassist.native.document import SaveState
    from paperassist.native.engine import Rect
    from paperassist.native.library_source import LibrarySource
    from paperassist.native.positions import CSS_PER_POINT, current_page
    from paperassist.native.rules import CANCEL, DISCARD, SAVE


@needs_qt
class QuickViewTests(LibraryWindowCase):
    def window(self):
        window = self.make_window()
        window.acquisition.main_opened = lambda document_id: None
        return window

    def test_a_local_reference_opens_one_quick_view_per_tab_and_does_not_propose_its_bibliography(self):
        window = self.window()
        with patch.object(window.acquisition, "main_opened") as proposed:
            first = self.open_document(window, self.docs[0])
            window.open_library_document_in_quick_view(self.docs[1]["id"])
            replaced = window.open_library_document_in_quick_view(self.docs[2]["id"])
            self.assertIs(first.quick, replaced)
            self.assertEqual(len(first.panes()), 2)
            self.assertEqual(window._shown_for["details"], first.main.key)  # the references column keeps the main paper
            second = self.open_document(window, self.docs[1])
            self.assertIsNone(second.quick)
            window.open_library_document_in_quick_view(self.docs[0]["id"])
            self.assertEqual((first.quick.key, second.quick.key), (f"library:{self.docs[2]['id']}", f"library:{self.docs[0]['id']}"))
            self.assertEqual([call.args[0] for call in proposed.call_args_list], [self.docs[0]["id"], self.docs[1]["id"]])

    def test_side_by_side_a_quick_view_fits_its_half_until_the_user_zooms(self):
        self.workspace.save_view(self.docs[1]["id"], {"page": 2, "scale": "1.5"})
        window = self.window()
        window.set_columns(outline=False, details=False)
        tab = self.open_document(window, self.docs[0])
        self.pump(lambda: tab.width() >= 720)
        quick = window.open_library_document_in_quick_view(self.docs[1]["id"])
        view = quick.view
        self.pump(lambda: current_page(view) == 1)
        self.assertEqual((view.fit, round(view.zoom, 3)), (False, round(1.5 * CSS_PER_POINT, 3)))  # stacked: the stored zoom
        quick.layout_button.click()
        self.pump(lambda: view.fit)
        self.assertFalse(tab.quick.vertical)
        self.assertLess(view.pages.width, view.viewport().width() + 1)  # the page no longer clips
        view.zoom_by(1.25)  # an explicit zoom is kept across layouts
        quick.layout_button.click()
        quick.layout_button.click()
        self.assertFalse(view.fit)

    def test_a_quick_view_opened_side_by_side_ignores_a_stored_fixed_zoom(self):
        settings.update_preferences({"quick_view_layout": "side"})
        self.workspace.save_view(self.docs[1]["id"], {"page": 2, "scale": "2"})
        window = self.window()
        window.set_columns(outline=False, details=False)
        tab = self.open_document(window, self.docs[0])
        self.pump(lambda: tab.width() >= 720)
        view = window.open_library_document_in_quick_view(self.docs[1]["id"]).view
        self.pump(lambda: current_page(view) == 1)
        self.assertTrue(view.fit)

    def test_promotion_to_a_dirty_owner_transfers_the_page_and_fits_width_without_saving(self):
        window = self.window()
        owner = self.open_document(window, self.docs[0]).main
        owner.view.doc.add_highlight(0, [Rect(40, 60, 200, 74)])
        owner.view.edited.emit()
        before = self.workspace.library.get_path(self.docs[0]["id"]).read_bytes()
        self.open_document(window, self.docs[1])
        quick = window.open_library_document_in_quick_view(self.docs[0]["id"])
        self.pump(lambda: quick.view.has_page(0))
        quick.view.zoom_by(1.5)
        quick.view.goto(2)
        self.pump()
        self.assertTrue(quick.view.read_only)
        window.promote_quick()
        self.pump()
        self.assertIs(window.pane(), owner)
        self.assertEqual(current_page(owner.view), 2)
        self.assertTrue(owner.view.fit)
        self.assertTrue(owner.modified)
        self.assertEqual(len(owner.view.doc.annotations(0)), 1)
        self.assertEqual(self.workspace.library.get_path(self.docs[0]["id"]).read_bytes(), before)


@needs_qt
class SaveRuleTests(LibraryWindowCase):
    def window(self):
        window = self.make_window()
        window.acquisition.main_opened = lambda document_id: None
        window.answers = []
        window.ask_decision = lambda pane: window.answers.pop(0)
        return window

    def edit(self, pane):
        pane.view.doc.add_highlight(0, [Rect(40, 60, 200, 74)])
        pane.view.edited.emit()

    def library_state(self):
        """Revisions and the hash of every stored PDF: any write to a PDF would change this."""
        library = self.workspace.library
        files = sorted([*library.originals.iterdir(), *library.working.iterdir()])
        return ([library.get_document(doc["id"])["revision"] for doc in self.docs],
                {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files})

    def test_saves_make_increasing_revisions_and_leave_the_original_untouched(self):
        library, document_id = self.workspace.library, self.docs[0]["id"]
        original = library.get_path(document_id, annotated=False)
        original_bytes = original.read_bytes()
        window = self.window()
        pane = self.open_document(window, self.docs[0]).main
        saved = []
        for revision in (1, 2):
            self.edit(pane)
            window.save()
            saved.append(library.get_path(document_id).read_bytes())
            self.assertEqual((library.get_document(document_id)["revision"], pane.state), (revision, SaveState.SAVED))
            self.assertTrue(library.get_path(document_id).name.endswith(f"-{revision}.pdf"))
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual((library.working / f"{document_id}-1.pdf").read_bytes(), saved[0])  # earlier snapshots stay
        self.assertNotEqual(saved[0], saved[1])

    def test_a_stale_revision_cannot_overwrite_the_current_one(self):
        library, document_id = self.workspace.library, self.docs[0]["id"]
        window = self.window()
        pane = self.open_document(window, self.docs[0]).main
        self.edit(pane)
        window.save()
        current = library.get_path(document_id).read_bytes()
        stale = LibrarySource(library, {**library.get_document(document_id), "revision": 0})
        with self.assertRaisesRegex(RuntimeError, "saved again"):
            stale.save(pane.view.doc.pdf)
        self.assertEqual((library.get_document(document_id)["revision"], library.get_path(document_id).read_bytes()), (1, current))

    def test_no_pdf_is_written_by_anything_but_the_save_choice(self):
        window = self.window()
        first = self.open_document(window, self.docs[0])
        other = self.open_document(window, self.docs[1])
        window.tabs.setCurrentWidget(first)
        self.edit(first.main)
        before = self.library_state()
        window.tabs.setCurrentWidget(other)  # hiding a tab
        first.main.view.zoom_by(1.25)
        first.main.view.goto(2)
        window.tabs.setCurrentWidget(first)
        window.open_library_document_in_quick_view(self.docs[2]["id"])
        window.toggle_quick_layout()
        window.open_library_document_in_quick_view(self.docs[1]["id"])  # replacing, then a view of an open paper
        window.promote_quick()  # back to its tab: the paper has no second tab
        self.assertEqual(window.tabs.count(), 2)
        window.tabs.setCurrentWidget(first)
        window.answers.append(CANCEL)
        self.assertFalse(window.close_tab(0))
        window.answers.append(CANCEL)
        self.assertFalse(window.close())
        self.pump(seconds=1.5)  # neither a timer nor a pause saves
        self.assertEqual(self.library_state(), before)
        window.answers.append(DISCARD)
        self.assertTrue(window.close_tab(window.tabs.indexOf(first)))
        self.assertEqual(self.library_state(), before)
        tab = self.open_document(window, self.docs[0])
        self.edit(tab.main)
        window.answers.append(SAVE)
        self.assertTrue(window.close())
        revisions, hashes = self.library_state()
        self.assertEqual(revisions, [1, 0, 0])
        self.assertEqual(set(hashes) - set(before[1]), {f"{self.docs[0]['id']}-1.pdf"})

    def test_window_close_discard_writes_nothing_and_a_draft_alone_asks(self):
        window = self.window()
        tab = self.open_document(window, self.docs[0])
        before = self.library_state()
        window.comments.compose()
        window.comments.panel.text.setPlainText("Unfinished note")
        window.answers.append(CANCEL)
        self.assertFalse(window.close())
        self.assertTrue(window.isVisible() and window.comments.has_draft(tab.main))
        window.answers.append(DISCARD)
        self.assertTrue(window.close())
        self.assertEqual(self.library_state(), before)
        self.assertEqual(self.workspace.library.get_notes(self.docs[0]["id"]), [])

    def test_reading_position_and_preferences_never_dirty_or_write_the_pdf(self):
        window = self.window()
        tab = self.open_document(window, self.docs[2])
        pane, document_id = tab.main, self.docs[2]["id"]
        before = self.library_state()
        pane.view.zoom_by(1.25)
        pane.view.goto(3)
        window.set_columns(outline=False)
        window.open_library_document_in_quick_view(self.docs[1]["id"])
        window.toggle_quick_layout()
        self.pump(lambda: self.workspace.get_view(document_id) is not None)
        self.assertEqual(self.workspace.get_view(document_id)["page"], 4)
        self.assertIs(pane.state, SaveState.SAVED)
        self.assertFalse(window.tab().quick.modified)
        self.edit(pane)  # a dirty paper keeps saving its position without being saved itself
        pane.view.goto(1)
        self.pump(lambda: self.workspace.get_view(document_id)["page"] == 2)
        self.assertIs(pane.state, SaveState.UNSAVED)
        self.assertEqual(self.library_state(), before)
        tab.activate(pane)  # Save acts on the pane the user last used
        window.save()
        self.assertEqual(self.workspace.get_view(document_id)["page"], 2)
        self.assertEqual(self.library_state()[0], [0, 0, 1])
        self.assertEqual(settings.preferences()["outline_expanded"], False)
