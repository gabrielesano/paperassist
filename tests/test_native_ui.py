"""Native reader widgets under Qt's offscreen platform: real events, the real render process."""
import collections
import hashlib
import sys
import tempfile
import time
import unittest

from pypdf import PdfReader
from pathlib import Path
from unittest.mock import PropertyMock, patch

from test_native_support import make_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel

    from paperassist import settings
    from paperassist.native.document import SaveState
    from paperassist.native.engine import Link, Rect
    from paperassist.native.positions import current_page
    from paperassist.native.renderer import RenderClient
    from paperassist.native.rules import CANCEL, DISCARD, SAVE
    from paperassist.native.source import FileSource
    from paperassist.native.tab import KEY_STEP
    from paperassist.native.view import PdfView
    from paperassist.native.window import MainWindow

    class FailingSource(FileSource):
        """A disk that refuses writes until `healthy` is set."""
        healthy = False

        def save(self, doc):
            if not self.healthy:
                raise PermissionError("the folder is read-only")
            super().save(doc)


@needs_qt
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.client = RenderClient()
        cls._files = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._files.name)

    @classmethod
    def tearDownClass(cls):
        cls.client.shutdown()
        cls._files.cleanup()

    def setUp(self):
        config = patch("paperassist.paths.config_file", return_value=self.dir / f"{self.id()}-config.json")
        config.start()
        self.addCleanup(config.stop)
        self.window = MainWindow(self.client)
        self.window.resize(1200, 800)
        self.window.show()
        self.answers, self.asked, self.failures = collections.deque(), [], []
        self.window.ask_decision = lambda pane: self.asked.append(pane.title) or self.answers.popleft()
        self.window.report_failure = lambda pane: self.failures.append(pane.view.doc.error)
        self.addCleanup(self.close_window)

    def close_window(self):
        for pane in self.window.all_panes():
            pane.release()
        self.window.hide()
        self.window.deleteLater()

    # ---- helpers ------------------------------------------------------------------------
    def pdf(self, name, **kwargs):
        return make_pdf(self.dir / f"{self.id()}-{name}.pdf", **kwargs)

    def pump(self, until=None, seconds=0.2):
        end = time.perf_counter() + (10 if until else seconds)
        while time.perf_counter() < end and not (until and until()):
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(until() if until else True, "timed out")

    def open(self, name, **kwargs):
        tab = self.window.open_source(FileSource(self.pdf(name, **kwargs)))
        self.pump(lambda: tab.main.view.has_page(0))
        return tab

    def drag(self, view, page, first, last):
        """Mouse-drag from word `first` to word `last` of a page that is at the top of the view."""
        words = view.doc.words(page)
        view.goto(page)
        self.pump(lambda: view.has_page(page))

        def at(word, x):
            rect = Rect(*words[word][:4])
            return view._band(page, Rect(x(rect), (rect.y0 + rect.y1) / 2, x(rect), (rect.y0 + rect.y1) / 2)).topLeft().toPoint()
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, at(first, lambda r: r.x0 + 1))
        QTest.mouseMove(view.viewport(), at(last, lambda r: r.x1 - 1))
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, at(last, lambda r: r.x1 - 1))

    def highlight(self, pane, page=0):
        self.window.highlight_action.setChecked(True)
        self.drag(pane.view, page, 3, 20)

    def annotations(self, path):
        return sum(1 for page in PdfReader(path).pages for a in page.get('/Annots', [])
                   if a.get_object()['/Subtype'] != '/Link')

    def test_open_selects_the_existing_quick_view_owner_without_duplicate(self):
        tab = self.open("main-owner")
        source = FileSource(self.pdf("quick-owner"))
        quick = self.window.open_quick(source)
        self.pump(lambda: quick.view.has_page(0))
        self.open("other-owner")
        self.assertIs(self.window.open_source(source), tab)
        self.assertIs(self.window.pane(), quick)
        self.assertEqual(len([p for p in self.window.all_panes() if p.key == source.key]), 1)

    def test_full_accessible_tab_title_and_last_close_welcome(self):
        tab = self.open("a-long-title-that-must-not-be-truncated-for-accessibility")
        bar = self.window.tabs.tabBar()
        self.assertIn(tab.main.title, bar.accessibleTabName(0))
        self.assertLess(len(self.window.tabs.tabText(0)), len(tab.main.title))
        self.assertIs(self.window.reader_stack.currentWidget(), self.window.tabs)
        self.assertTrue(self.window.close_tab(0))
        self.assertIs(self.window.reader_stack.currentWidget(), self.window.welcome)
        self.assertTrue(self.window.welcome.isVisible())

    def test_tab_switch_preserves_view_search_edits_and_quick_view(self):
        first = self.open("retained-main")
        quick = self.window.open_quick(FileSource(self.pdf("retained-quick")))
        self.pump(lambda: quick.view.has_page(0))
        view = first.main.view
        self.highlight(first.main)
        view.zoom_by(1.25)
        first.main.find_bar.field.setText("nanoflares")
        first.main.find_bar._run(1)
        view.goto(2)
        self.pump()
        position, zoom = view.verticalScrollBar().value(), view.zoom
        data = view.doc.pdf.data
        self.open("retained-other")
        self.window.tabs.setCurrentWidget(first)
        self.pump(lambda: view.has_page(2))
        self.assertEqual(view.verticalScrollBar().value(), position)
        self.assertEqual(view.zoom, zoom)
        self.assertEqual(first.main.find_bar.field.text(), "nanoflares")
        self.assertTrue(view.hits)
        self.assertEqual(view.doc.pdf.data, data)
        self.assertTrue(first.main.modified)
        self.assertIs(first.quick, quick)

    def test_new_window_does_not_restore_tabs(self):
        self.open("session-local")
        other = MainWindow(self.client)
        self.assertEqual(other.tabs.count(), 0)
        other.close()
        other.deleteLater()

    def test_empty_window_hides_reference_actions_and_page_field(self):
        self.pump()
        self.assertFalse(self.window.details.acquire.isVisible())
        self.assertFalse(self.window.details.repair.isVisible())
        self.assertFalse(self.window.shortcuts.page_field.isVisible())
        self.open("empty-controls")
        self.assertTrue(self.window.shortcuts.page_field.isVisible())
        self.assertFalse(self.window.details.acquire.isVisible())
        self.window.close_tab(0)
        self.assertFalse(self.window.shortcuts.page_field.isVisible())

    def test_failed_open_keeps_existing_tabs_and_quick_view_usable(self):
        tab = self.open("valid-before-error")
        quick = self.window.open_quick(FileSource(self.pdf("valid-quick")))
        bad = self.dir / "invalid.pdf"
        bad.write_bytes(b"not a PDF")
        self.assertIsNone(self.window.open_source(FileSource(bad)))
        self.assertIsNone(self.window.open_quick(FileSource(bad)))
        self.assertEqual(self.window.tabs.count(), 1)
        self.assertIs(tab.quick, quick)
        self.assertIn("Could not open", self.window.statusBar().currentMessage())
        self.assertEqual(self.open("valid-after-error"), self.window.tab())
        self.assertEqual(self.window.statusBar().currentMessage(), "")

    def test_keyboard_focus_activates_main_and_quick_panes(self):
        tab = self.open("focus-main")
        quick = self.window.open_quick(FileSource(self.pdf("focus-quick")))
        self.window.activateWindow()
        self.pump()
        for pane in (tab.main, quick, tab.main):
            pane.view.setFocus(Qt.TabFocusReason)
            self.pump()
            self.assertIs(self.window.pane(), pane)
            pane.find_bar.open()
            self.pump()
            self.assertIs(self.window.pane(), pane)
            with patch.object(pane, "save", return_value=True) as save:
                with patch.object(type(pane), "modified", new_callable=PropertyMock, return_value=True):
                    self.window.save()
                save.assert_called_once()

    def test_find_ignores_case_and_accents_in_a_synthetic_pdf(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        page = writer.add_blank_page(400, 500)
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }))
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        content.set_data("BT /F1 12 Tf 40 400 Td (Café CAFÉ cafe) Tj ET".encode("cp1252"))
        page[NameObject("/Contents")] = writer._add_object(content)
        path = self.dir / "accented.pdf"
        writer.write(path)
        pane = self.window.open_source(FileSource(path)).main
        for query in ("cafe", "CAFÉ", "CaFé"):
            self.assertEqual(pane.view.find(query), 3, query)
        pane.find_bar.open()
        pane.find_bar.field.setText("cafe")
        QTest.keyClick(pane.find_bar.field, Qt.Key_Return)
        self.assertEqual(pane.view.hit, 0)
        QTest.keyClick(pane.find_bar.field, Qt.Key_Return)
        self.assertEqual(pane.view.hit, 1)
        QTest.keyClick(pane.find_bar.field, Qt.Key_Return, Qt.ShiftModifier)
        self.assertEqual(pane.view.hit, 0)

    def test_highlight_visibility_changes_rendering_without_editing_or_saving(self):
        pane = self.open("visibility").main
        self.highlight(pane)
        self.pump(lambda: pane.view.has_page(0))
        original = pane.view.doc.source.path.read_bytes()
        revision, state = pane.view.doc.pdf.data, pane.state
        before = pane.view.cache.get(0)[2].toImage()
        pane.view.set_highlights_visible(False)
        self.pump(lambda: pane.view.has_page(0))
        after = pane.view.cache.get(0)[2].toImage()
        self.assertNotEqual(before, after)
        self.assertEqual(pane.view.doc.pdf.data, revision)
        self.assertEqual(pane.state, state)
        self.assertEqual(pane.view.doc.source.path.read_bytes(), original)
        pane.view.set_highlights_visible(True)
        self.pump(lambda: pane.view.has_page(0))
        self.assertEqual(pane.view.cache.get(0)[2].toImage(), before)

    def test_split_sizes_are_remembered_separately_per_orientation(self):
        self.window.set_columns(outline=False, details=False)
        tab = self.open("split-main")
        self.window.open_quick(FileSource(self.pdf("split-quick")))
        self.pump()
        tab.setSizes([300, 250])
        tab._remember_share()
        vertical = tab.quick_share
        tab.set_side_by_side(True)
        tab.setSizes([600, 300])
        tab._remember_share()
        horizontal = tab.quick_share
        tab.set_side_by_side(False)
        self.assertEqual(tab.quick_share, vertical)
        tab.set_side_by_side(True)
        self.assertEqual(tab.quick_share, horizontal)

    # ---- reading ------------------------------------------------------------------------
    def test_a_paper_opens_fit_to_width_and_renders_in_the_other_process(self):
        tab = self.open("a")
        view = tab.main.view
        self.assertTrue(view.fit)
        self.assertAlmostEqual(view.zoom * 400 + 24, view.viewport().width(), delta=1)
        self.assertEqual(self.window.tabs.tabText(0), view.doc.source.title[:28])

    def test_zoom_leaves_fit_mode_and_rerenders_crisply_at_the_new_scale(self):
        view = self.open("a").main.view
        before = view.zoom
        view.zoom_by(0.5)
        self.assertFalse(view.fit)
        self.assertAlmostEqual(view.zoom, before / 2)
        self.pump(lambda: view.has_page(0))

    def test_pages_are_drawn_on_whole_device_pixels_at_fractional_ratios(self):
        view = self.open("a").main.view
        _, _, width, height = view.pages.rect(0)
        pixmap = QPixmap(round(width * 1.25) + 1, round(height * 1.25) + 1)  # The renderer rounds sizes outward
        pixmap.setDevicePixelRatio(1.25)
        with patch.object(PdfView, "devicePixelRatioF", return_value=1.25):
            box = view._target(0, pixmap)
        for value in (box.x(), box.y(), box.width(), box.height()):
            self.assertAlmostEqual(value * 1.25, round(value * 1.25), places=6)
        self.assertEqual((box.width() * 1.25, box.height() * 1.25), (pixmap.width(), pixmap.height()))  # 1:1, never resampled

    def test_dragging_selects_text_in_reading_order_and_ctrl_c_copies_it(self):
        view = self.open("a").main.view
        self.drag(view, 0, 2, 25)
        expected = " ".join(w[4] for w in view.doc.words(0)[2:26])
        self.assertEqual(" ".join(view.selected_text().split()), expected)
        QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), view.selected_text())

    def test_find_highlights_every_match_and_steps_through_them(self):
        pane = self.open("a").main
        pane.find_bar.open()
        pane.find_bar.field.setText("nanoflares")
        QTest.keyClick(pane.find_bar.field, Qt.Key_Return)
        self.assertEqual((len(pane.view.hits), pane.find_bar.status.text()), (32, "1 / 32"))
        QTest.keyClick(pane.find_bar.field, Qt.Key_Return)
        self.assertEqual(pane.find_bar.status.text(), "2 / 32")
        QTest.keyClick(pane.find_bar.field, Qt.Key_Escape)
        self.assertTrue(pane.find_bar.isHidden())
        self.assertEqual(pane.view.hits, [])

    def test_search_starts_from_the_current_page_in_both_directions_and_wraps(self):
        pane = self.open("search-pos").main
        view, bar = pane.view, pane.find_bar
        view.goto(2)
        self.pump(lambda: current_page(view) == 2)
        self.assertEqual(current_page(view), 2)

        # Forward search starts at the first match on/after the current page
        bar.open()
        bar.field.setText("Page")
        QTest.keyClick(bar.field, Qt.Key_Return)
        self.assertEqual((len(view.hits), view.hit, view.hits[view.hit][0]), (4, 2, 2))
        self.assertEqual(bar.status.text(), "3 / 4")
        QTest.keyClick(bar.field, Qt.Key_Return)
        self.assertEqual((view.hit, view.hits[view.hit][0], bar.status.text()), (3, 3, "4 / 4"))
        # Wraps around to document start
        QTest.keyClick(bar.field, Qt.Key_Return)
        self.assertEqual((view.hit, view.hits[view.hit][0], bar.status.text()), (0, 0, "1 / 4"))

        # Direct view.find forward starts from the current page
        view.goto(2)
        self.pump(lambda: current_page(view) == 2)
        self.assertEqual(view.find("nanoflares"), 32)
        self.assertEqual((view.hit, view.hits[view.hit][0]), (16, 2))

        # Forward search wraps when all matches precede the current page
        view.goto(2)
        self.pump(lambda: current_page(view) == 2)
        bar.field.setText("Page 1")
        QTest.keyClick(bar.field, Qt.Key_Return)
        self.assertEqual((view.hit, view.hits[view.hit][0], bar.status.text()), (0, 0, "1 / 1"))

        # Backward search (Shift+Enter / find -1) starts on/before the current page
        view.goto(2)
        self.pump(lambda: current_page(view) == 2)
        bar.field.setText("Page")
        QTest.keyClick(bar.field, Qt.Key_Return, Qt.ShiftModifier)
        self.assertEqual((view.hit, view.hits[view.hit][0], bar.status.text()), (2, 2, "3 / 4"))
        QTest.keyClick(bar.field, Qt.Key_Return, Qt.ShiftModifier)
        self.assertEqual((view.hit, view.hits[view.hit][0], bar.status.text()), (1, 1, "2 / 4"))

        # Direct view.find backwards picks the last match on/before current page
        view.goto(2)
        self.pump(lambda: current_page(view) == 2)
        self.assertEqual(view.find("nanoflares", -1), 32)
        self.assertEqual((view.hit, view.hits[view.hit][0]), (23, 2))

        # Backward search wraps when all matches succeed the current page
        view.goto(1)
        self.pump(lambda: current_page(view) == 1)
        bar.field.setText("Journal")
        QTest.keyClick(bar.field, Qt.Key_Return, Qt.ShiftModifier)
        self.assertEqual((view.hit, view.hits[view.hit][0], bar.status.text()), (0, 3, "1 / 1"))

    def test_a_citation_link_navigates_and_back_returns(self):
        view = self.open("a").main.view
        cite = view.doc.search("[1]")[0][1]
        center = view._band(0, cite).center().toPoint()
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, center)
        last = view.pages.page_at(view.verticalScrollBar().value() + 100)
        self.assertEqual(last, 3)
        view.go_back()
        self.assertEqual(view.verticalScrollBar().value(), 0)

    def test_external_links_only_open_safe_schemes(self):
        view = self.open("a").main.view
        with patch("paperassist.native.view.QDesktopServices.openUrl") as open_url:
            view.follow(Link(Rect(0, 0, 1, 1), "file:///etc/passwd", None, 0))
            view.follow(Link(Rect(0, 0, 1, 1), "https://example.org/paper", None, 0))
        self.assertEqual(open_url.call_count, 1)

    def test_a_hidden_tab_releases_its_pixmaps_and_redraws_when_shown_again(self):
        first = self.open("a").main.view
        self.assertGreater(first.cache.total(), 0)
        self.open("b")
        self.assertEqual(first.cache.total(), 0)
        self.window.tabs.setCurrentIndex(0)
        self.pump(lambda: first.has_page(0))

    def test_opening_a_paper_that_has_a_tab_selects_it(self):
        tab = self.open("a")
        self.open("b")
        self.assertIs(self.window.open_source(tab.main.view.doc.source), tab)
        self.assertEqual((self.window.tabs.count(), self.window.tabs.currentIndex()), (2, 0))

    # ---- highlights and saving ----------------------------------------------------------
    def test_a_highlight_is_unsaved_everywhere_and_nothing_is_written_until_save(self):
        tab = self.open("a")
        path = tab.main.view.doc.source.path
        original = path.read_bytes()
        self.highlight(tab.main)
        self.assertIs(tab.main.state, SaveState.UNSAVED)
        self.assertTrue(self.window.tabs.tabText(0).endswith(" ●"))
        self.assertTrue(self.window.windowTitle().startswith("●"))
        self.assertIn("Unsaved changes", self.window.state_label.text())
        self.open("b")  # switching tabs, then opening and closing a quick view, is not a save
        self.window.open_quick(FileSource(self.pdf("c")))
        self.window.close_quick()
        self.window.tabs.setCurrentIndex(0)
        self.pump(seconds=0.5)
        self.assertEqual(path.read_bytes(), original)

    def test_save_writes_an_incremental_update_and_clears_the_state(self):
        tab = self.open("a")
        path = tab.main.view.doc.source.path
        original = path.read_bytes()
        self.highlight(tab.main)
        self.window.save()
        self.assertIs(tab.main.state, SaveState.SAVED)
        self.assertTrue(path.read_bytes().startswith(original))
        self.assertEqual(self.annotations(path), 1)
        self.assertFalse(self.window.tabs.tabText(0).endswith(" ●"))
        self.assertIn("Saved", self.window.state_label.text())
        self.pump(lambda: tab.main.view.has_page(0))
        self.assertEqual(tab.main.view.doc.pending.get(0), None)  # the render process draws it now

    def test_a_failed_save_keeps_the_unsaved_state_shows_why_and_can_be_retried(self):
        source = FailingSource(self.pdf("a"))
        tab = self.window.open_source(source)
        self.pump(lambda: tab.main.view.has_page(0))
        self.highlight(tab.main)
        self.window.save()
        self.assertIs(tab.main.state, SaveState.FAILED)
        self.assertEqual(self.failures, ["the folder is read-only"])
        self.assertTrue(self.window.tabs.tabText(0).endswith(" !"))
        self.assertIn("read-only", self.window.state_label.text())
        self.answers.append(SAVE)
        self.assertFalse(self.window.close_tab(0))  # Save in the close dialog fails too: the tab stays
        source.healthy = True
        self.window.save()
        self.assertIs(tab.main.state, SaveState.SAVED)
        self.assertEqual(self.annotations(source.path), 1)

    # ---- closing ------------------------------------------------------------------------
    def test_closing_a_clean_tab_asks_nothing(self):
        self.open("a")
        self.assertTrue(self.window.close_tab(0))
        self.assertEqual((self.asked, self.window.tabs.count()), ([], 0))

    def test_closing_a_modified_tab_offers_save_discard_cancel(self):
        tab = self.open("a")
        path = tab.main.view.doc.source.path
        original = path.read_bytes()
        self.highlight(tab.main)
        self.answers.append(CANCEL)
        self.assertFalse(self.window.close_tab(0))
        self.assertEqual((self.window.tabs.count(), tab.main.state), (1, SaveState.UNSAVED))
        self.answers.append(DISCARD)
        self.assertTrue(self.window.close_tab(0))
        self.assertEqual(path.read_bytes(), original)
        tab = self.window.open_source(FileSource(path))
        self.pump(lambda: tab.main.view.has_page(0))
        self.highlight(tab.main)
        self.answers.append(SAVE)
        self.assertTrue(self.window.close_tab(0))
        self.assertEqual(self.annotations(path), 1)

    def test_a_tab_with_two_modified_panes_collects_both_decisions_before_closing_anything(self):
        tab = self.open("a")
        quick = self.window.open_quick(FileSource(self.pdf("b")))
        self.pump(lambda: quick.view.has_page(0))
        self.highlight(tab.main)
        self.highlight(quick)
        self.answers.extend([SAVE, CANCEL])
        self.assertFalse(self.window.close_tab(0))
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(self.annotations(tab.main.view.doc.source.path), 0)  # the first Save was not applied
        self.assertEqual(self.annotations(quick.view.doc.source.path), 0)
        self.assertEqual((self.window.tabs.count(), tab.quick), (1, quick))
        self.answers.extend([SAVE, DISCARD])
        self.assertTrue(self.window.close_tab(0))
        self.assertEqual(self.annotations(tab.main.view.doc.source.path), 1)
        self.assertEqual(self.annotations(quick.view.doc.source.path), 0)

    def test_closing_the_window_asks_for_every_modified_tab_and_cancel_keeps_it_open(self):
        first = self.open("a")
        second = self.open("b")
        self.highlight(second.main)
        self.window.tabs.setCurrentWidget(first)
        self.highlight(first.main)
        self.answers.extend([DISCARD, CANCEL])
        self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())
        self.assertEqual(len(self.asked), 2)
        self.answers.extend([DISCARD, DISCARD])
        self.assertTrue(self.window.close())

    # ---- quick view ---------------------------------------------------------------------
    def test_quick_view_opens_below_and_closes_without_a_prompt_when_clean(self):
        tab = self.open("a")
        quick = self.window.open_quick(FileSource(self.pdf("b")))
        self.pump(lambda: quick.view.has_page(0))
        self.assertTrue(quick.quick and not quick.bar.isHidden())
        self.assertGreater(quick.mapTo(tab, QPoint(0, 0)).y(), tab.main.mapTo(tab, QPoint(0, 0)).y())
        QTest.keyClick(quick.view, Qt.Key_Escape)
        self.assertIsNone(tab.quick)
        self.assertEqual(self.asked, [])

    def test_the_layout_switch_is_remembered_and_narrow_windows_always_stack(self):
        self.window.set_columns(outline=False, details=False)  # the side columns leave the reader too narrow to split
        tab = self.open("a")
        quick = self.window.open_quick(FileSource(self.pdf("b")))
        self.assertEqual(tab.orientation(), Qt.Vertical)
        quick.layout_button.click()
        self.pump()
        self.assertEqual(tab.orientation(), Qt.Horizontal)
        self.assertEqual(settings.preferences()["quick_view_layout"], "side")
        self.window.set_columns(outline=True)
        self.window.resize(800, 800)
        self.pump()
        self.assertEqual(tab.orientation(), Qt.Vertical)
        self.window.resize(1200, 800)
        self.pump()
        self.assertEqual(tab.orientation(), Qt.Horizontal)

    def test_the_separator_moves_with_the_keyboard_within_its_limits(self):
        tab = self.open("a")
        self.window.open_quick(FileSource(self.pdf("b")))
        self.pump()
        handle = tab.handle(1)
        before = tab.sizes()[0]
        QTest.keyClick(handle, Qt.Key_Down)
        self.assertEqual(tab.sizes()[0], before + KEY_STEP)
        QTest.keyClick(handle, Qt.Key_Home)
        self.assertGreaterEqual(tab.quick_share, 0.2)
        self.assertLess(tab.quick_share, 0.3)
        QTest.keyClick(handle, Qt.Key_End)
        self.assertLessEqual(tab.quick_share, 0.75)
        self.assertGreater(tab.quick_share, 0.5)

    def test_replacing_a_modified_quick_view_asks_and_cancel_keeps_it(self):
        tab = self.open("a")
        quick = self.window.open_quick(FileSource(self.pdf("b")))
        self.pump(lambda: quick.view.has_page(0))
        self.highlight(quick)
        self.answers.append(CANCEL)
        self.assertIsNone(self.window.open_quick(FileSource(self.pdf("c"))))
        self.assertIs(tab.quick, quick)
        self.answers.append(DISCARD)
        replacement = self.window.open_quick(FileSource(self.pdf("c")))
        self.assertIs(tab.quick, replacement)

    def test_promoting_moves_the_quick_pane_with_its_unsaved_edits_into_a_new_tab(self):
        tab = self.open("a")
        quick = self.window.open_quick(FileSource(self.pdf("b")))
        self.pump(lambda: quick.view.has_page(0))
        self.highlight(quick)
        view = quick.view
        self.window.promote_quick()
        self.assertEqual(self.window.tabs.count(), 2)
        promoted = self.window.tab()
        self.assertIsNone(tab.quick)
        self.assertEqual((promoted.main, promoted.main.view, promoted.main.modified), (quick, view, True))
        self.assertTrue(quick.bar.isHidden())
        self.assertEqual(self.asked, [])  # promotion never asks and never saves
        self.assertEqual(self.annotations(view.doc.source.path), 0)

    def test_a_quick_view_of_a_paper_with_unsaved_edits_is_read_only_and_labeled(self):
        tab = self.open("a")
        path = tab.main.view.doc.source.path
        self.highlight(tab.main)
        quick = self.window.open_quick(FileSource(path))
        self.pump(lambda: quick.view.has_page(0))
        self.assertTrue(quick.view.locked)
        stale = quick.view.doc.pdf
        self.assertIn("Last saved version", quick.title_label.text())
        self.drag(quick.view, 1, 3, 20)  # the highlight tool is still on
        self.assertIs(quick.state, SaveState.SAVED)
        self.assertEqual(quick.view.doc.pending, {})
        tab.activate(tab.main)
        self.window.save()
        self.assertFalse(quick.view.locked)
        self.assertIsNot(quick.view.doc.pdf, stale)  # it read the saved file again
        self.assertNotIn("Last saved", quick.title_label.text())

    def test_only_one_view_can_hold_edits_for_a_paper(self):
        tab = self.open("a")
        quick = self.window.open_quick(FileSource(tab.main.view.doc.source.path))
        self.pump(lambda: quick.view.has_page(0))
        self.highlight(quick)  # the quick view edits first, so the main pane is the one that waits
        self.assertTrue(tab.main.view.locked)
        self.assertIs(quick.state, SaveState.UNSAVED)
        self.window.highlight_action.setChecked(True)
        self.drag(tab.main.view, 1, 3, 20)
        self.assertIs(tab.main.state, SaveState.SAVED)

    def test_promoting_a_clean_view_of_an_open_paper_returns_to_its_tab(self):
        tab = self.open("a")
        other = self.open("b")
        self.window.open_quick(FileSource(tab.main.view.doc.source.path))
        self.window.promote_quick()
        self.assertEqual((self.window.tabs.count(), self.window.tab()), (2, tab))
        self.assertIsNone(other.quick)

    def test_the_main_paper_keeps_its_place_and_zoom_while_the_quick_view_comes_and_goes(self):
        self.window.set_columns(outline=False, details=False)
        tab = self.open("place-main")
        view = tab.main.view
        view.zoom_by(1.25)
        view.goto(2)
        self.pump()
        place = (view.verticalScrollBar().value(), view.zoom, current_page(view))

        def main_place():
            return (view.verticalScrollBar().value(), view.zoom, current_page(view))
        first = self.window.open_quick(FileSource(self.pdf("place-quick-1")))
        self.pump(lambda: first.view.has_page(0))
        first.view.zoom_by(1.5)  # panes zoom and scroll on their own
        first.view.goto(1)
        self.assertEqual(main_place(), place)
        self.assertNotEqual(first.view.zoom, view.zoom)
        second = self.window.open_quick(FileSource(self.pdf("place-quick-2")))
        self.assertIsNot(second, first)
        self.assertEqual(main_place(), place)
        self.window.toggle_quick_layout()  # side by side narrows the main paper but keeps its place
        self.pump()
        self.assertEqual((view.zoom, current_page(view)), place[1:])
        self.window.close_quick()
        self.assertEqual((view.zoom, current_page(view)), place[1:])

    def test_dragging_the_separator_resizes_within_the_limits(self):
        self.window.set_columns(outline=False, details=False)
        tab = self.open("drag-main")
        self.window.open_quick(FileSource(self.pdf("drag-quick")))
        self.pump()
        handle = tab.handle(1)
        centre = handle.rect().center()
        QTest.mousePress(handle, Qt.LeftButton, Qt.NoModifier, centre)
        QTest.mouseMove(handle, QPoint(centre.x(), centre.y() - tab.height()))
        QTest.mouseRelease(handle, Qt.LeftButton, Qt.NoModifier, QPoint(centre.x(), centre.y() - tab.height()))
        self.assertAlmostEqual(tab.quick_share, 0.75, places=2)
        centre = handle.rect().center()
        QTest.mousePress(handle, Qt.LeftButton, Qt.NoModifier, centre)
        QTest.mouseMove(handle, QPoint(centre.x(), centre.y() + tab.height()))
        QTest.mouseRelease(handle, Qt.LeftButton, Qt.NoModifier, QPoint(centre.x(), centre.y() + tab.height()))
        # Native desktops can constrain the window height; the pane's pixel minimum still applies.
        smallest = max(0.2, tab.quick.minimumHeight() / sum(tab.sizes()))
        self.assertAlmostEqual(tab.quick_share, smallest, places=2)

    def test_the_quick_view_title_never_overlaps_its_buttons(self):
        self.open("title-main")
        for name in ("short", "a-very-long-paper-title-" * 6):
            quick = self.window.open_quick(FileSource(self.pdf(name)))
            self.pump(lambda: quick.view.has_page(0))
            if quick.title_label.isVisible():
                self.assertLessEqual(quick.title_label.geometry().right(), quick.quick_buttons[0].geometry().left())
            hidden = [button.defaultAction() for button in quick.quick_buttons if not button.isVisible()]
            if hidden:
                quick.bar.overflow.popup()
                self.assertTrue(all(action in quick.bar.overflow.menu.actions() for action in hidden))
                quick.bar.overflow.menu.hide()

    def test_a_narrow_reader_stacks_and_says_why_the_layout_switch_waits(self):
        self.window.set_columns(outline=False, details=False)
        tab = self.open("narrow-main")
        quick = self.window.open_quick(FileSource(self.pdf("narrow-quick")))
        self.assertTrue(quick.layout_button.isEnabled())
        self.window.set_columns(outline=True, details=True)
        self.window.resize(900, 700)
        self.pump()
        self.assertLess(tab.width(), 720)
        self.assertFalse(quick.layout_button.isEnabled())
        self.assertIn("too narrow", quick.layout_button.defaultAction().toolTip())
        self.window.resize(1400, 700)
        self.pump()
        self.assertTrue(quick.layout_button.isEnabled())

    def test_closing_a_modified_quick_view_offers_save_discard_cancel(self):
        tab = self.open("close-main")
        path = self.pdf("close-quick")
        quick = self.window.open_quick(FileSource(path))
        self.pump(lambda: quick.view.has_page(0))
        original = path.read_bytes()
        self.highlight(quick)
        self.answers.append(CANCEL)
        quick.quick_buttons[2].click()  # the Close button of the quick view
        self.assertEqual((tab.quick, quick.modified, len(self.asked)), (quick, True, 1))
        self.assertEqual(path.read_bytes(), original)
        self.answers.append(DISCARD)
        quick.quick_buttons[2].click()
        self.assertIsNone(tab.quick)
        self.assertEqual(path.read_bytes(), original)
        quick = self.window.open_quick(FileSource(path))
        self.pump(lambda: quick.view.has_page(0))
        self.highlight(quick)
        self.answers.append(SAVE)
        QTest.keyClick(quick.view, Qt.Key_Escape)
        self.assertIsNone(tab.quick)
        self.assertEqual(self.annotations(path), 1)

    def test_replacing_a_modified_quick_view_with_save_writes_only_the_replaced_paper(self):
        tab = self.open("replace-main")
        first_path, second_path = self.pdf("replace-first"), self.pdf("replace-second")
        first = self.window.open_quick(FileSource(first_path))
        self.pump(lambda: first.view.has_page(0))
        self.highlight(first)
        main_bytes = tab.main.view.doc.source.path.read_bytes()
        second_bytes = second_path.read_bytes()
        self.answers.append(SAVE)
        replacement = self.window.open_quick(FileSource(second_path))
        self.assertIs(tab.quick, replacement)
        self.assertEqual(self.annotations(first_path), 1)
        self.assertEqual((second_path.read_bytes(), tab.main.view.doc.source.path.read_bytes()), (second_bytes, main_bytes))

    def test_a_fresh_quick_view_of_a_paper_that_is_a_main_goes_back_to_it_when_promoted(self):
        tab = self.open("owner")
        quick = self.window.open_quick(FileSource(tab.main.view.doc.source.path))
        self.pump(lambda: quick.view.has_page(0))
        quick.view.zoom_by(1.5)
        quick.view.goto(2)
        self.pump()
        self.assertEqual(current_page(quick.view), 2)
        self.window.promote_quick()
        self.pump()
        self.assertEqual((self.window.tabs.count(), self.window.tab(), tab.quick), (1, tab, None))
        self.assertIs(self.window.pane(), tab.main)
        self.assertEqual(current_page(tab.main.view), 2)
        self.assertTrue(tab.main.view.fit)

    def test_promoting_keeps_the_page_and_zoom_of_the_quick_view(self):
        self.open("promote-main")
        quick = self.window.open_quick(FileSource(self.pdf("promote-quick")))
        self.pump(lambda: quick.view.has_page(0))
        quick.view.zoom_by(1.5)
        quick.view.goto(2)
        self.pump()
        place = (quick.view.zoom, quick.view.fit, current_page(quick.view))
        self.window.promote_quick()
        self.pump()
        view = self.window.tab().main.view
        self.assertIs(view, quick.view)
        self.assertEqual((view.zoom, view.fit, current_page(view)), place)

    def test_a_close_request_during_a_save_is_refused_and_asks_nothing(self):
        outcomes = []

        class ClosingSource(FileSource):
            def save(inner, doc):
                outcomes.append((self.window.close_tab(0), self.window.close()))
                FileSource.save(inner, doc)
        source = ClosingSource(self.pdf("saving"))
        tab = self.window.open_source(source)
        self.pump(lambda: tab.main.view.has_page(0))
        self.highlight(tab.main)
        self.window.save()
        self.assertEqual(outcomes, [(False, False)])
        self.assertEqual((self.asked, self.window.tabs.count(), tab.main.state), ([], 1, SaveState.SAVED))
        self.assertEqual(self.annotations(source.path), 1)
        self.assertTrue(self.window.isVisible())

    def test_nothing_is_written_to_any_pdf_until_the_user_chooses_save(self):
        def files():
            return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.dir.glob(f"{self.id()}-*.pdf")}
        first, other = self.open("first"), self.open("second")
        self.window.tabs.setCurrentWidget(first)
        self.highlight(first.main)
        before = files()
        self.assertEqual(len(before), 2)
        self.window.tabs.setCurrentWidget(other)  # hiding a tab
        first.main.view.zoom_by(1.25)
        first.main.view.goto(2)
        self.window.tabs.setCurrentWidget(first)
        third = self.window.open_quick(FileSource(self.pdf("third")))  # opening, laying out and replacing quick views
        self.window.toggle_quick_layout()
        self.window.open_quick(FileSource(self.pdf("fourth")))
        self.window.promote_quick()  # promotion
        self.window.tabs.setCurrentWidget(first)
        mirror = self.window.open_quick(FileSource(first.main.view.doc.source.path))  # a read-only view of the edited paper
        self.assertTrue(mirror.view.locked)
        self.window.promote_quick()  # a locked view of an open paper goes back to its owner
        self.assertIs(self.window.tab(), first)
        self.answers.append(CANCEL)  # Cancel
        self.assertFalse(self.window.close_tab(self.window.tabs.indexOf(first)))
        self.pump(seconds=1.5)  # no timer saves either
        self.assertEqual({k: v for k, v in files().items() if k in before}, before)
        self.answers.append(DISCARD)
        self.assertTrue(self.window.close_tab(self.window.tabs.indexOf(first)))
        self.assertEqual({k: v for k, v in files().items() if k in before}, before)
        self.assertIs(third.state, SaveState.SAVED)
        path = next(iter(before))
        tab = self.window.open_source(FileSource(path))
        self.pump(lambda: tab.main.view.has_page(0))
        self.highlight(tab.main)
        self.answers.append(SAVE)  # only the Save choice writes, and only that file
        self.assertTrue(self.window.close_tab(self.window.tabs.indexOf(tab)))
        changed = [k for k, v in files().items() if before.get(k, v) != v]
        self.assertEqual(changed, [path])

    def test_annotation_toolbar_palette_undo_and_exact_revision_rendering(self):
        from paperassist.native.annotation_bar import COLORS, install
        from paperassist.native.engine import PageRenderer
        from PySide6.QtGui import QImage
        bar = install(self.window)
        view = self.open('palette').main.view
        original = view.doc.source.path.read_bytes()
        for index in range(5):
            self.window.highlight_action.setChecked(True)
            bar.refresh()
            bar.colors.setCurrentIndex(index)
            bar.colors.activated.emit(index)
            self.drag(view, 0, index * 7, index * 7 + 4)
            self.pump(lambda: view.has_page(0))
            color = view.doc.annotations(0)[-1]['color']
            self.assertEqual([round(v * 255) for v in color], list(bytes.fromhex(COLORS[index][1][1:])))
        self.assertEqual(view.doc.source.path.read_bytes(), original)
        image = view.cache.get(0)[2].toImage().convertToFormat(QImage.Format_RGB888)
        actual = bytes(image.constBits())
        packed = b''.join(actual[y * image.bytesPerLine():y * image.bytesPerLine() + image.width() * 3]
                          for y in range(image.height()))
        renderer = PageRenderer(view.doc.pdf.data)
        self.addCleanup(renderer.close)
        self.assertEqual(packed, bytes(renderer.render(0, view._scale()).rgb))
        view.setFocus()
        for _ in range(5):
            QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
        self.assertFalse(view.doc.modified)
        self.assertEqual(view.doc.annotations(0), [])
        self.assertEqual(view.doc.source.path.read_bytes(), original)

    def test_annotation_toolbar_draw_select_recolor_thickness_delete_and_undo(self):
        from paperassist.native.annotation_bar import install
        bar = install(self.window)
        view = self.open('ink').main.view
        bar.refresh()
        bar.draw.trigger()
        bar.thickness.setValue(4)
        first = view._band(0, Rect(40, 180, 40, 180)).topLeft().toPoint()
        middle = view._band(0, Rect(100, 200, 100, 200)).topLeft().toPoint()
        last = view._band(0, Rect(180, 180, 180, 180)).topLeft().toPoint()
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, first)
        QTest.mouseMove(view.viewport(), middle)
        QTest.mouseMove(view.viewport(), last)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, last)
        self.pump(lambda: view.has_page(0))
        self.assertEqual(view.doc.annotations(0)[0]['width'], 4)
        bar.select.trigger()
        self.assertFalse(bar.width_action.isVisible())
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, middle)
        self.assertTrue(bar.width_action.isVisible())
        self.assertEqual(view.selected_annotation, (0, 1))  # the citation link is /Annots[0]
        bar.colors.activated.emit(2)
        bar.thickness.setValue(8)
        item = view.doc.annotations(0)[0]
        self.assertEqual(item['width'], 8)
        self.assertEqual([round(v * 255) for v in item['color']], [121, 197, 223])
        view.setFocus()
        QTest.keyClick(view, Qt.Key_Delete)
        self.assertEqual(view.doc.annotations(0), [])
        QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(len(view.doc.annotations(0)), 1)
        self.assertEqual(view.doc.annotations(0)[0]['width'], 8)
        self.assertTrue(view.save())
        self.pump(lambda: view.has_page(0))
        self.assertFalse(view.doc.modified)

    def test_locked_view_annotation_commands_leave_everything_unchanged(self):
        view = self.open('locked-edit').main.view
        self.highlight(self.window.pane())
        before = view.doc.pdf.data
        view.selected_annotation = (0, 1)
        view.locked = True
        view.set_annotation_color((1, 0, 0))
        view.set_annotation_width(8)
        view.delete_annotation()
        view.undo()
        self.assertEqual(view.doc.pdf.data, before)

    def test_welcome_screen_unified_toolbar_zoom_label_and_dirty_save_indicator(self):
        bar = self.window.annotation_bar
        keys = self.window.shortcuts
        self.assertTrue(self.window.welcome.isVisibleTo(self.window))
        self.assertEqual(self.window.welcome.objectName(), 'welcome-screen')
        self.assertEqual(self.window.welcome.open_file_button.accessibleName(), 'Open a PDF file')
        self.assertFalse(keys.page_field.isVisibleTo(self.window))
        self.assertFalse(self.window.save_action.isEnabled())
        self.assertFalse(bar.isVisibleTo(self.window))

        tab = self.open('unified-toolbar')
        pane = tab.main
        self.assertLess(pane.layout().indexOf(pane.find_bar), pane.layout().indexOf(pane.view))
        self.assertTrue(keys.page_field.isVisibleTo(self.window))
        self.assertEqual(self.window.fit_action.text(), 'Fit width')
        pane.view.zoom_by(1.25)
        self.assertTrue(self.window.fit_action.text().endswith('%'))
        self.window.fit_action.trigger()
        self.assertEqual(self.window.fit_action.text(), 'Fit width')

        save_btn = self.window.main_bar.widgetForAction(self.window.save_action)
        self.assertFalse(save_btn.property('dirty'))
        self.highlight(pane)
        self.assertTrue(save_btn.property('dirty'))
        self.window.save()
        self.assertFalse(save_btn.property('dirty'))

    def test_contextual_annotation_bar_auto_shows_and_color_chips_pick_colors(self):
        from paperassist.native.annotation_bar import install as install_annotation_bar

        bar = install_annotation_bar(self.window)
        view = self.open('contextual-bar').main.view
        self.assertFalse(bar.isVisibleTo(self.window))

        self.window.highlight_action.setChecked(True)
        self.assertTrue(bar.isVisibleTo(self.window))
        green_hex, green_chip = bar.colors.buttons[1]
        self.assertEqual(green_hex, '#8FD3AA')
        green_chip.click()
        self.assertEqual(bar.colors.currentIndex(), 1)
        self.assertTrue(green_chip.isChecked())
        self.assertEqual([round(c * 255) for c in view.highlight_color], [143, 211, 170])

        bar.select.trigger()
        self.assertFalse(bar.isVisibleTo(self.window))

        view.set_highlights_visible(False)
        bar.refresh()
        self.assertTrue(bar.isVisibleTo(self.window))
        view.set_highlights_visible(True)
        bar.refresh()
        self.assertFalse(bar.isVisibleTo(self.window))


@needs_qt
class BindingTests(unittest.TestCase):
    @unittest.skipIf(sys.version_info >= (3, 12), "None is immortal from Python 3.12")
    def test_qt_calls_keep_their_reference_to_none(self):
        # PySide6 6.12.0 lost one reference to None per void call and crashed Python 3.11 within minutes.
        QApplication.instance() or QApplication([])
        label = QLabel()
        before = sys.getrefcount(None)
        for _ in range(1000):
            label.setText("x")
        self.assertGreater(sys.getrefcount(None), before - 100)


if __name__ == "__main__":
    unittest.main()
