"""Native reader widgets under Qt's offscreen platform: real events, the real render process."""
import collections
import tempfile
import time
import unittest

from pypdf import PdfReader
from pathlib import Path
from unittest.mock import patch

from test_native_support import make_pdf, needs_qt, pdf_available

if pdf_available:
    from PySide6.QtCore import QPoint, QSettings, Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from paperassist.native.document import SaveState
    from paperassist.native.engine import Link, Rect
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
        self.window = MainWindow(self.client, QSettings(str(self.dir / f"{self.id()}.ini"), QSettings.IniFormat))
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
        tab = self.open("a")
        quick = self.window.open_quick(FileSource(self.pdf("b")))
        self.assertEqual(tab.orientation(), Qt.Vertical)
        quick.layout_button.click()
        self.pump()
        self.assertEqual(tab.orientation(), Qt.Horizontal)
        self.assertTrue(self.window.settings.value("quick_side_by_side", False, bool))
        self.window.resize(600, 800)
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
        self.assertEqual(tab.sizes()[0], tab.main.minimumHeight())
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


if __name__ == "__main__":
    unittest.main()
