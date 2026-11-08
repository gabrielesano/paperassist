"""The citation-steps.js edge cases, native PDF geometry, and background panel loading."""
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_native_support import make_pdf, needs_pdf, needs_qt, pdf_available

if pdf_available:
    from paperassist.native.citations import (CitationIndex, build_text_index, citation_matches, citation_position,
        find_markers, match_rects, merge_links, nearest_item, next_citation_index, normalize, reveal_citation)
    from paperassist.native.engine import Pdf, Rect


def item(text, x, y, width=None):
    return {"str": text, "transform": [10, 0, 0, 10, x, y], "width": len(text) * 5 if width is None else width, "height": 10}


@needs_pdf
class CitationLogicTests(unittest.TestCase):
    def test_markers_match_across_spaces_line_breaks_and_dash_styles(self):
        index = build_text_index([item("see Bingert &", 0, 100), item("Peter 2011;", 0, 88), item("[3, 5–7]", 0, 76)])
        first, second = find_markers(index, ["Bingert &\nPeter 2011", "[3, 5-7]"])
        self.assertEqual(index["text"][first["start"]:first["end"]], "bingert&peter2011")
        self.assertEqual(index["text"][second["start"]:second["end"]], "[3,57]")

    def test_ligatures_and_soft_hyphens(self):
        index = build_text_index([item("Schmitt et al. 2o­12", 0, 0), item("ﬁrst Lu 1991", 0, 0)])
        self.assertEqual(find_markers(index, ["Schmitt et al. 2o12"])[0]["start"], 0)
        self.assertTrue(all(find_markers(index, ["Lu 1991", "first"])))

    def test_composed_combining_and_spacing_accents(self):
        index = build_text_index([item("see M¨uller 2004 and Mart´ınez et al. 2010", 0, 100),
                                  item("also Mu\u0308ller 2004, Gu\u0308rkan, Martınez", 0, 88)])
        found = find_markers(index, ["Müller 2004", "Martínez et al. 2010", "Müller 2004", "Gürkan"])
        self.assertTrue(all(found))
        self.assertEqual(index["owner"][found[0]["start"]], (0, 4))
        self.assertEqual(index["owner"][found[2]["start"]][0], 1)

    def test_hyphenated_line_end_surname(self):
        items = [item("as in van Balle-", 50, 300), item("gooijen et al. 2011", 50, 288)]
        index = build_text_index(items)
        self.assertEqual(len(match_rects(items, index, find_markers(index, ["van Ballegooijen et al. 2011"])[0])), 2)
        self.assertEqual(normalize("Schmidt-Ott 2001"), normalize("Schmidt Ott 2001"))

    def test_repeated_markers_match_in_order_and_never_reuse_a_place(self):
        index = build_text_index([item("Hood 2009 and Hood 2009 then Hood 2009", 0, 0)])
        found = find_markers(index, ["Hood 2009"] * 4)
        self.assertLess(found[0]["start"], found[1]["start"])
        self.assertLess(found[1]["start"], found[2]["start"])
        self.assertIsNone(found[3])

    def test_missing_or_empty_marker(self):
        self.assertEqual(find_markers(build_text_index([item("nothing here", 0, 0)]), ["Hood 2009", ""]), [None, None])

    def test_partial_item_rect(self):
        items = [item("abc Hood 2009 xyz", 100, 200)]
        index = build_text_index(items)
        self.assertEqual(match_rects(items, index, find_markers(index, ["Hood 2009"])[0]), [Rect(120, 200, 165, 210)])

    def test_two_lines_and_several_items_on_one_line(self):
        items = [item("as shown by Bingert &", 50, 300), item("Peter 2011 earlier", 50, 288)]
        index = build_text_index(items)
        self.assertEqual(len(match_rects(items, index, find_markers(index, ["Bingert & Peter 2011"])[0])), 2)
        items = [item("Hood et", 0, 50), item(" al. ", 35, 50), item("2009", 60, 50)]
        index = build_text_index(items)
        self.assertEqual(len(match_rects(items, index, find_markers(index, ["Hood et al. 2009"])[0])), 1)

    def test_rotated_text_follows_baseline(self):
        items = [{"str": "Hood 2009", "transform": [0, 10, -10, 0, 100, 100], "width": 45, "height": 10}]
        index = build_text_index(items)
        self.assertEqual(match_rects(items, index, find_markers(index, ["Hood 2009"])[0]), [Rect(90, 100, 100, 145)])

    def test_link_order_uses_nearest_text_item(self):
        items = [item("left column", 50, 500), item("right column", 320, 700)]
        self.assertEqual(nearest_item(items, [330, 702, 360, 710]), 1)
        self.assertEqual(nearest_item(items, [60, 502, 90, 510]), 0)

    def test_touching_links_merge_but_separate_events_destinations_and_pages_do_not(self):
        def link(rect, dest="a", page=1):
            return {"page": page, "dest": dest, "rects": [rect]}
        self.assertEqual(len(merge_links([link([10, 100, 60, 110]), link([63, 100, 90, 110])])), 1)
        self.assertEqual(len(merge_links([link([200, 100, 300, 110]), link([50, 88, 80, 98])])), 1)
        self.assertEqual(len(merge_links([link([10, 100, 60, 110]), link([200, 100, 230, 110])])), 2)
        self.assertEqual(len(merge_links([link([10, 100, 60, 110]), link([63, 100, 90, 110], "b")])), 2)
        self.assertEqual(len(merge_links([link([10, 100, 60, 110]), link([63, 100, 90, 110], "a", 2)])), 2)

    def test_native_top_left_links_merge_across_a_wrap(self):
        links = [{"page": 1, "dest": "a", "rects": [Rect(200, 100, 300, 110)]},
                 {"page": 1, "dest": "a", "rects": [Rect(50, 112, 80, 122)]}]
        self.assertEqual(len(merge_links(links, displayed=True)), 1)

    def test_series_starts_at_reading_position_then_advances_and_wraps(self):
        positions = [1.2, 2.5, 2.9, 7.1]
        self.assertEqual([next_citation_index(positions, None, x) for x in (1, 2.5, 2.6, 8)], [0, 1, 2, 0])
        self.assertEqual(next_citation_index(positions, 1, 7), 2)
        self.assertEqual(next_citation_index(positions, 3, 1), 0)
        self.assertEqual(next_citation_index([], None, 1), -1)

    def test_numeric_ranges_groups_duplicates_and_unknown_labels(self):
        refs = [{"id": str(n), "label": f"[{n}]."} for n in range(1, 5)]
        self.assertEqual([r["id"] for r in citation_matches(refs, "[1, 3–4]")], ["1", "3", "4"])
        for text in ("[4-2]", "[1-99]", "[1, 9]", "[1,]", "Figure 1"):
            self.assertEqual(citation_matches(refs, text), [])

    def test_author_year_and_named_destinations_preserve_ambiguous_choices(self):
        refs = [{"id": "a", "authors": "Smith, J.", "year": "2015", "destinations": ["ref.a"]},
                {"id": "b", "authors": "Smith, K.", "year": "2015"}]
        self.assertEqual(citation_matches(refs, "Smith et al. (2015)"), refs)
        self.assertEqual(citation_matches(refs, "unrelated", "ref.a"), refs[:1])
        self.assertEqual(citation_matches(refs, "Smith et al. (2015)", "unmapped"), refs)
        self.assertEqual(citation_matches(refs, "Section 2015"), [])
        self.assertEqual(citation_matches(refs, "Smith et al. (2015)", "section.1"), [])

    def test_unmapped_destination_falls_back_to_complete_numeric_marker(self):
        refs = [{"id": "a", "label": "1"}, {"id": "b", "label": "2"}]
        self.assertEqual(citation_matches(refs, "[1, 2]", "unknown"), refs)
        self.assertEqual(citation_matches(refs, "[1, 3]", "unknown"), [])
        self.assertEqual(citation_matches(refs, "[1]", "figure.1"), [])

    def test_pdfium_locates_repeated_occurrences_and_page_only_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            data = make_pdf(Path(folder) / "paper.pdf").read_bytes()
            index = CitationIndex(data)
            found = index.locate({"occurrences": [{"page": 1, "marker": "Smith [1]"}, {"page": 2, "marker": "missing marker"}]})
            self.assertEqual([citation["page"] for citation in found], [1, 2])
            self.assertTrue(found[0]["rects"])
            self.assertEqual(found[1]["rects"], [])

    def test_pdfium_names_merge_link_pieces_and_exclude_bibliography_page(self):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
        writer = PdfWriter()
        writer.add_blank_page(300, 400)
        writer.add_blank_page(300, 400)
        writer.add_named_destination("ref.Smith", 1)
        for page, box in ((0, (10, 300, 60, 310)), (0, (63, 300, 90, 310)), (1, (10, 300, 60, 310))):
            writer.add_annotation(page, DictionaryObject({NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject(FloatObject(n) for n in box), NameObject("/Dest"): TextStringObject("ref.Smith")}))
        stream = io.BytesIO()
        writer.write(stream)
        data = stream.getvalue()
        pdf = Pdf(data)
        self.addCleanup(pdf.close)
        self.assertEqual(pdf.links(0)[0].destination, "ref.Smith")
        found = CitationIndex(data).locate({"destinations": ["ref.Smith"]})
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]["rects"]), 2)

    def test_citation_position_follows_page_rotation(self):
        from types import SimpleNamespace
        from pypdf import PdfWriter
        positions = []
        for rotation in (0, 90, 180, 270):
            writer = PdfWriter()
            writer.add_blank_page(200, 400).rotate(rotation)
            data = io.BytesIO()
            writer.write(data)
            pdf = Pdf(data.getvalue())
            try:
                citation = {"page": 1, "rects": [pdf.frame(0).rect(10, 370, 50, 380)]}
                positions.append(citation_position(SimpleNamespace(doc=SimpleNamespace(sizes=pdf.sizes)), citation))
            finally:
                pdf.close()
        self.assertEqual(positions, [1.05, 1.05, 1.925, 1.75])



@needs_qt
class CitationPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def delete(self, widget):
        """Destroy now, on this thread: the Mock client keeps the view in a reference cycle, and a
        cycle collected later on a Workspace job thread would stop the view's running timer there and crash Qt."""
        from PySide6.QtCore import QCoreApplication, QEvent
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(widget, QEvent.DeferredDelete)

    def pump(self, condition):
        end = time.monotonic() + 10
        while not condition() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(condition())

    def test_reload_waits_for_running_load_or_analysis(self):
        from unittest.mock import Mock
        from paperassist.native.references_panel import ReferencesPanel

        for analysis in (False, True):
            with self.subTest(analysis=analysis):
                workspace = Mock()
                workspace.submit.return_value = {"id": "load"}
                workspace.analyze_references.return_value = {"id": "analysis"}
                panel = ReferencesPanel(workspace)
                panel.document_id = 1
                if analysis:
                    panel.analyze(1)
                else:
                    panel.show_for(1)
                first = panel.analyzing[1]
                panel.reload(1)
                panel.reload(1)
                self.assertIs(panel.analyzing[1], first)
                self.assertEqual(workspace.submit.call_count, 0 if analysis else 1)
                first.stop()
                first.finished.emit({"status": "completed", "result": {}})
                self.assertIsNot(panel.analyzing[1], first)
                self.assertEqual(workspace.submit.call_count, 1 if analysis else 2)
                panel.close()

    def test_grouped_citation_choices_escape_ampersand_mnemonic(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from PySide6.QtGui import QKeySequence
        from PySide6.QtWidgets import QPushButton
        from paperassist.native.references_panel import ReferencesPanel

        workspace = Mock()
        panel = ReferencesPanel(workspace)
        self.addCleanup(panel.close)
        class View:
            pass
        view = View()
        panel.view = view
        link = SimpleNamespace(destination=None, rect=Rect(10, 10, 50, 20))
        doc = SimpleNamespace(_links={0: [link]}, words=lambda page: [(10, 10, 50, 20, "[1, 2]")])
        view.doc = doc
        panel.refs = [
            {"id": 1, "label": "1", "title": "A&A & Keppens"},
            {"id": 2, "label": "2", "title": "Second paper", "local_document_id": 22},
        ]
        panel._fill()
        opened = Mock()
        panel.open_requested.connect(opened)
        self.assertTrue(panel._citation_link(view, link))
        buttons = panel.choices.findChildren(QPushButton)
        self.assertEqual(len(buttons), 2)
        keppens_button = next(b for b in buttons if "Keppens" in b.text())
        self.assertIn("&&", keppens_button.text())
        self.assertTrue(QKeySequence.mnemonic(keppens_button.text()).isEmpty())
        self.assertEqual(keppens_button.text(), "1 A&&A && Keppens")
        keppens_button.click()
        self.assertEqual(panel.expanded, 1)
        self.assertEqual(panel.list.currentItem().data(257), 1)
        opened.assert_not_called()
        self.assertTrue(panel._citation_link(view, link))
        next(b for b in panel.choices.findChildren(QPushButton) if "Second paper" in b.text()).click()
        opened.assert_called_once_with(22)

    def test_named_bibliography_link_waits_for_loading_and_reveals_result(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from paperassist.native.references_panel import ReferencesPanel

        class View:
            pass

        panel = ReferencesPanel(None)
        self.addCleanup(panel.close)
        panel.document_id = 7
        watcher = panel.analyzing[7] = Mock()
        view = panel.view = View()
        citation = SimpleNamespace(destination="bm_apjaa348cbib74", rect=Rect(10, 10, 50, 20))
        affiliation = SimpleNamespace(destination="bm_apjaa348caf1", rect=Rect(60, 10, 90, 20))
        view.doc = SimpleNamespace(_links={0: [citation, affiliation]}, words=lambda page: [(10, 10, 50, 20, "2014")])
        self.assertFalse(panel._citation_link(view, affiliation))
        self.assertTrue(panel._citation_link(view, citation))
        self.assertIn("Loading references", panel.status.text())
        ref = {"id": 1, "label": "Reale 2014", "destinations": [citation.destination]}
        panel._job_done(7, {"status": "completed", "result": {"references": [ref], "status": "partial"}}, "load")
        self.assertEqual(panel.expanded, 1)
        self.assertEqual(panel.list.currentItem().data(257), 1)
        watcher.deleteLater.assert_called_once()

    def test_unmatched_bibliography_link_stays_in_reader_and_offers_retry(self):
        from types import SimpleNamespace
        from paperassist.native.references_panel import ReferencesPanel

        class View:
            pass

        panel = ReferencesPanel(None)
        self.addCleanup(panel.close)
        panel.document_id = 7
        view = panel.view = View()
        link = SimpleNamespace(destination="bm_apjaa348cbib74", rect=Rect(10, 10, 50, 20))
        view.doc = SimpleNamespace(_links={0: [link]}, words=lambda page: [(10, 10, 50, 20, "2014")])
        for refs in ([], [{"id": 1, "label": "Other", "destinations": ["bib-other"]}]):
            panel.refs = refs
            self.assertTrue(panel._citation_link(view, link))
            self.assertIn("not found", panel.status.text())
            self.assertFalse(panel.retry.isHidden())

    def test_load_and_analysis_errors_name_the_operation(self):
        from unittest.mock import Mock
        from paperassist.native.references_panel import ReferencesPanel
        for operation in ("load", "analyze"):
            workspace = Mock()
            start = workspace.submit if operation == "load" else workspace.analyze_references
            panel = ReferencesPanel(workspace)
            panel.document_id = 1
            start.side_effect = RuntimeError("queue full")
            run = panel._load if operation == "load" else panel.analyze
            run(1)
            self.assertIn(f"Could not {operation}", panel.status.text())
            start.side_effect = None
            start.return_value = {"id": "test-job"}
            run(1)
            watcher = panel.analyzing[1]
            watcher.stop()
            watcher.finished.emit({"status": "failed", "error": "unreadable"})
            self.assertIn(f"Could not {operation}", panel.status.text())
            panel.close()

    def test_stale_extraction_is_a_workspace_job_never_a_synchronous_get(self):
        from paperassist import references
        from paperassist.workspace import Workspace
        from test_library_window import cited_pdf
        from paperassist.native.references_panel import ReferencesPanel

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            workspace = Workspace(root / "library")
            self.addCleanup(workspace.close)
            doc = workspace.library.import_stream(io.BytesIO(cited_pdf(root / "p.pdf").read_bytes()), "p.pdf")
            references.analyze(workspace.library, doc["id"])
            panel = ReferencesPanel(workspace)
            self.addCleanup(panel.close)

            # A bumped extraction version triggers reanalysis inside the background Workspace job, never on the UI thread.
            ui_thread = threading.get_ident()
            analyze_threads = []
            real_analyze = references.analyze
            def tracked_analyze(lib, doc_id):
                analyze_threads.append(threading.get_ident())
                return real_analyze(lib, doc_id)

            with patch.object(references, "EXTRACTION_VERSION", references.EXTRACTION_VERSION + 1), \
                 patch.object(references, "analyze", side_effect=tracked_analyze):
                panel.show_for(doc["id"])
                self.assertIn(doc["id"], panel.analyzing)
                self.pump(lambda: doc["id"] in panel.loaded and not panel.analyzing)
            self.assertEqual(len(analyze_threads), 1)
            self.assertNotEqual(analyze_threads[0], ui_thread)

    def test_resolving_a_fresh_analysis_runs_off_the_ui_thread_and_is_cached(self):
        from paperassist import references
        from paperassist.workspace import Workspace
        from test_library_window import cited_pdf
        from paperassist.native.acquisition import AcquisitionController
        from paperassist.native.window import MainWindow
        from paperassist.native.renderer import RenderClient

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            with patch("paperassist.paths.config_file", return_value=root / "config.json"):
                workspace = Workspace(root / "library")
                self.addCleanup(workspace.close)
                client = RenderClient()
                self.addCleanup(client.shutdown)
                docs = [
                    workspace.library.import_stream(io.BytesIO(cited_pdf(root / f"p{n}.pdf", pages=n).read_bytes()), f"p{n}.pdf")
                    for n in (3, 4)
                ]
                for doc in docs:
                    references.analyze(workspace.library, doc["id"])

                window = MainWindow(client, workspace)
                window.acquisition.main_opened = lambda document_id: None
                window.resize(1200, 800)
                window.show()
                self.addCleanup(lambda: (window.close(), window.deleteLater()))

                ui_thread = threading.get_ident()
                disk_reads = []
                real_load = references._load
                def tracked_load(lib, doc_id):
                    disk_reads.append((doc_id, threading.get_ident()))
                    return real_load(lib, doc_id)

                with patch.object(references, "_load", side_effect=tracked_load):
                    # Opening each analyzed paper reads its stored payload once, off the UI thread.
                    window.open_library_document(docs[0]["id"])
                    self.pump(lambda: window.details.list.count() == 2 and not window.details.analyzing)
                    self.assertEqual(len(disk_reads), 1)
                    self.assertNotEqual(disk_reads[0][1], ui_thread)

                    window.open_library_document(docs[1]["id"])
                    self.pump(lambda: window.details.list.count() == 2 and not window.details.analyzing)
                    self.assertEqual(len(disk_reads), 2)

                    # Switching tabs and expanding cards or browser links never touch the disk.
                    disk_reads.clear()
                    window.tabs.setCurrentIndex(0)
                    self.app.processEvents()
                    window.tabs.setCurrentIndex(1)
                    self.app.processEvents()
                    window.tabs.setCurrentIndex(0)
                    self.app.processEvents()
                    self.assertEqual(disk_reads, [])

                    first_item = window.details.list.item(0)
                    window.details._expand(first_item)
                    ref = window.details.refs[0]
                    window.details._reference_open(ref)
                    card = window.details.list.itemWidget(first_item)
                    self.assertIn((window.details.document_id, str(ref["id"])), window.acquisition.expanded)
                    self.assertEqual(disk_reads, [])

    def test_stepping_multiple_references_uses_render_worker_with_zero_process_spawns(self):
        import multiprocessing
        from paperassist import references
        from paperassist.workspace import Workspace
        from test_library_window import cited_pdf
        from paperassist.native.renderer import RenderClient
        from paperassist.native.window import MainWindow

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            with patch("paperassist.paths.config_file", return_value=root / "config.json"):
                workspace = Workspace(root / "library")
                self.addCleanup(workspace.close)
                client = RenderClient()
                self.addCleanup(client.shutdown)
                doc = workspace.library.import_stream(io.BytesIO(cited_pdf(root / "p.pdf", pages=3).read_bytes()), "p.pdf")
                references.analyze(workspace.library, doc["id"])

                window = MainWindow(client, workspace)
                window.acquisition.main_opened = lambda document_id: None
                window.resize(1200, 800)
                window.show()
                self.addCleanup(lambda: (window.close(), window.deleteLater()))
                tab = window.open_library_document(doc["id"])
                self.pump(lambda: window.details.list.count() == 2 and tab.main.view.has_page(0))

                spawns = []
                real_ctx = multiprocessing.get_context
                def forbid_spawn(*args, **kwargs):
                    spawns.append(args)
                    return real_ctx(*args, **kwargs)

                with patch("multiprocessing.get_context", side_effect=forbid_spawn):
                    for ref in list(window.details.refs):
                        window.details.step(ref)
                        self.pump(lambda r=ref: r["id"] in window.details.located.get(tab.main.view, {}))
                self.assertEqual(spawns, [])
                self.assertEqual(len(window.details.located[tab.main.view]), 2)

    def test_reveal_and_rotated_position_do_not_edit_pdf(self):
        from paperassist.native.document import PdfDocument
        from paperassist.native.source import FileSource
        from paperassist.native.view import PdfView
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            source = FileSource(make_pdf(Path(folder) / "paper.pdf", rotate=90))
            doc = PdfDocument(source)
            self.addCleanup(doc.close)
            view = PdfView(doc, Mock())
            self.addCleanup(self.delete, view)
            view.resize(500, 400)
            view.show()
            self.app.processEvents()
            citation = {"page": 1, "rects": [Rect(20, 30, 50, 40)]}
            before = doc.pdf.data
            self.assertAlmostEqual(citation_position(view, citation), 1 + 30 / doc.sizes[0][1])
            self.assertTrue(reveal_citation(view, citation))
            self.assertFalse(doc.modified)
            self.assertEqual(doc.pdf.data, before)
            self.assertTrue(view._outlines)
            self.assertFalse(reveal_citation(view, {"page": 2, "rects": []}))


if __name__ == "__main__":
    unittest.main()
