"""The render process: its replacing queue and the real spawned process over pipes."""
import contextlib
import io
from multiprocessing.shared_memory import SharedMemory
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pathlib import Path

from support import QtTestCase, make_pdf, needs_pdf, needs_qt, pdf_available, qt_app

if pdf_available:
    from paperassist.native import annotations, worker


@needs_pdf
class WantQueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = worker.WantQueue()

    def test_a_new_want_list_replaces_the_old_one_for_that_document(self):
        self.queue.post(("want", 1, [(0, 1.0), (1, 1.0), (2, 1.0)]))
        self.queue.post(("want", 1, [(7, 1.0)]))
        self.assertEqual(self.queue.next_job({1}), (1, (7, 1.0)))

    def test_jobs_come_out_in_order_and_done_removes_only_that_job(self):
        self.queue.post(("want", 1, [(3, 2.0), (4, 2.0)]))
        doc_id, job = self.queue.next_job({1})
        self.queue.done(doc_id, job)
        self.assertEqual(self.queue.next_job({1}), (1, (4, 2.0)))

    def test_done_does_not_resurrect_a_list_the_ui_replaced_meanwhile(self):
        self.queue.post(("want", 1, [(3, 2.0)]))
        job = self.queue.next_job({1})
        self.queue.post(("want", 1, [(9, 2.0)]))  # arrives while page 3 is being drawn
        self.queue.done(*job)
        self.assertEqual(self.queue.next_job({1}), (1, (9, 2.0)))

    def test_an_empty_list_cancels_everything_for_a_hidden_document(self):
        self.queue.post(("want", 1, [(0, 1.0)]))
        self.queue.post(("want", 1, []))
        self.assertFalse(self.queue.has_work({1}))

    def test_wanted_pages_of_an_unopened_document_are_not_work(self):
        self.queue.post(("want", 5, [(0, 1.0)]))  # no busy loop if the file failed to open
        self.assertFalse(self.queue.has_work({1}))
        self.assertTrue(self.queue.has_work({5}))

    def test_commands_are_taken_once_and_closing_forgets_the_wanted_pages(self):
        self.queue.post(("open", 1, "a.pdf"))
        self.queue.post(("want", 1, [(0, 1.0)]))
        self.queue.post(("close", 1))
        self.assertEqual([c[0] for c in self.queue.take_commands()], ["open", "close"])
        self.assertEqual(self.queue.take_commands(), [])
        self.assertIsNone(self.queue.next_job({1}))


@needs_pdf
class WorkerProcessTests(unittest.TestCase):
    """Starts the real worker the way a frozen app does: spawn context and pipes."""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.path = make_pdf(Path(cls._dir.name) / "paper.pdf", pages=3)

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def start(self):
        process, send, receive = worker.start()

        def stop():
            with contextlib.suppress(OSError):  # a test may have ended the process already
                send.send(("quit",))
            process.join(10)
        self.addCleanup(stop)
        return process, send, receive

    def page(self, receive):
        self.assertTrue(receive.poll(20), "the render process did not answer")
        header = receive.recv()
        return header, receive.recv_bytes()

    def test_pages_come_back_as_raw_rgb_of_the_announced_size(self):
        _, send, receive = self.start()
        send.send(("open", 1, str(self.path)))
        send.send(("want", 1, [(2, 1.5), (0, 1.5)]))
        (kind, doc_id, page, scale, width, height, seconds), rgb = self.page(receive)
        self.assertEqual((kind, doc_id, page, scale, (width, height)), ("page", 1, 2, 1.5, (600, 750)))
        self.assertEqual(len(rgb), width * height * 3)
        self.assertGreater(seconds, 0)
        self.assertEqual(self.page(receive)[0][2], 0)  # then the second wanted page, in the order asked

    @unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
    def test_the_process_keeps_no_handle_on_the_file_so_a_save_can_replace_it(self):
        process, send, receive = self.start()
        send.send(("open", 1, str(self.path)))
        send.send(("want", 1, [(0, 1.0)]))
        self.page(receive)  # the document is open and has drawn
        handles = [os.path.realpath(f"/proc/{process.pid}/fd/{fd}") for fd in os.listdir(f"/proc/{process.pid}/fd")]
        self.assertNotIn(str(self.path.resolve()), handles)

    def test_a_document_that_cannot_be_opened_does_not_kill_the_process(self):
        process, send, receive = self.start()
        send.send(("open", 1, str(self.path.with_name("missing.pdf"))))
        send.send(("want", 1, [(0, 1.0)]))
        send.send(("open", 2, str(self.path)))
        send.send(("want", 2, [(1, 1.0)]))
        self.assertEqual(self.page(receive)[0][1:3], (2, 1))
        self.assertTrue(process.is_alive())

    def test_quit_ends_the_process(self):
        process, send, _ = self.start()
        send.send(("quit",))
        process.join(10)
        self.assertFalse(process.is_alive())

    def test_the_process_leaves_when_the_ui_goes_away(self):
        process, send, _ = self.start()
        send.close()  # what the child sees if the parent dies
        process.join(10)
        self.assertFalse(process.is_alive())

    def test_locate_finds_citations_and_handles_unknown_document(self):
        with tempfile.TemporaryDirectory() as directory:
            cited = make_pdf(Path(directory) / "cited.pdf", pages=2)
            process, send, receive = self.start()
            send.send(("open", 1, str(cited)))
            ref = {"id": "1", "number": 1, "occurrences": [{"page": 1, "marker": "[1]"}]}
            send.send(("locate", 10, 1, ref))
            send.send(("locate", 11, 99, ref))
            self.assertTrue(receive.poll(20))
            kind1, req1, res1, err1 = receive.recv()
            self.assertTrue(receive.poll(20))
            kind2, req2, res2, err2 = receive.recv()
            self.assertEqual((kind1, req1, err1), ("citations", 10, ""))
            self.assertGreaterEqual(len(res1), 1)
            self.assertEqual((kind2, req2, res2), ("citations", 11, None))
            self.assertIn("closed", err2)
            self.assertTrue(process.is_alive())

    def test_open_shm_renders_and_closes_without_buffer_error(self):
        data = self.path.read_bytes()
        shm = SharedMemory(create=True, size=len(data))
        self.addCleanup(lambda: (shm.close(), contextlib.suppress(FileNotFoundError).__enter__()))
        shm.buf[:len(data)] = data
        process, send, receive = self.start()
        send.send(("open_shm", 1, shm.name, len(data), None, True))
        send.send(("want", 1, [(0, 1.0)]))
        (kind, doc_id, page, scale, width, height, _), rgb = self.page(receive)
        self.assertEqual((kind, doc_id, page, scale), ("page", 1, 0, 1.0))
        self.assertEqual(len(rgb), width * height * 3)
        send.send(("close", 1))
        self.assertTrue(receive.poll(10))
        self.assertEqual(receive.recv(), ("closed", 1))
        shm.close()
        shm.unlink()
        self.assertTrue(process.is_alive())

    def test_worker_highlights_command_filters_highlights_and_ink_highlights(self):
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        buf = io.BytesIO()
        writer.write(buf)
        data = annotations.apply(buf.getvalue(), "highlight", 0, rects=[(20, 20, 80, 40)], color=(1, 1, 0), opacity=1)
        data = annotations.apply(data, "ink", 0, strokes=[[(20, 80), (80, 100)]], color=(0, 1, 0), width=10, freehand=True)
        data = annotations.apply(data, "ink", 0, strokes=[[(20, 140), (80, 160)]], color=(0, 0, 0), width=6, freehand=False)

        def px(rgb, width, height, x, y):
            off = ((height - y) * width + x) * 3
            return tuple(rgb[off:off + 3])

        _, send, receive = self.start()
        send.send(("open", 1, data, None, True))
        send.send(("want", 1, [(0, 1.0)]))
        (_, _, _, _, w, h, _), rgb_on = self.page(receive)
        self.assertNotEqual(px(rgb_on, w, h, 50, 30), (255, 255, 255))
        self.assertNotEqual(px(rgb_on, w, h, 50, 90), (255, 255, 255))
        self.assertEqual(px(rgb_on, w, h, 50, 150), (0, 0, 0))

        send.send(("highlights", 1, False))
        send.send(("want", 1, [(0, 1.0)]))
        _, rgb_off = self.page(receive)
        self.assertEqual(px(rgb_off, w, h, 50, 30), (255, 255, 255))
        self.assertEqual(px(rgb_off, w, h, 50, 90), (255, 255, 255))
        self.assertEqual(px(rgb_off, w, h, 50, 150), (0, 0, 0))

        send.send(("highlights", 1, True))
        send.send(("want", 1, [(0, 1.0)]))
        _, rgb_restored = self.page(receive)
        self.assertEqual(rgb_restored, rgb_on)


@needs_qt
class RenderClientTests(QtTestCase):
    use_client = False

    def _pdf_with_annotations(self):
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        buf = io.BytesIO()
        writer.write(buf)
        data = annotations.apply(buf.getvalue(), "highlight", 0, rects=[(20, 20, 80, 40)], color=(1, 1, 0), opacity=1)
        data = annotations.apply(data, "ink", 0, strokes=[[(20, 80), (80, 100)]], color=(0, 1, 0), width=10, freehand=True)
        return annotations.apply(data, "ink", 0, strokes=[[(20, 140), (80, 160)]], color=(0, 0, 0), width=6, freehand=False)

    def test_open_bytes_uses_shared_memory_and_cleans_up_on_close_and_shutdown(self):
        qt_app()
        client = worker.RenderClient()
        self.addCleanup(client.shutdown)
        pages = []
        client.page_ready.connect(lambda *args: pages.append(args))
        with tempfile.TemporaryDirectory() as directory:
            data = make_pdf(Path(directory) / "shm.pdf", pages=2).read_bytes()
        with patch.object(client, "_send", wraps=client._send) as send:
            doc1 = client.open(data)
        self.assertEqual(send.call_args.args[0], "open_shm")
        self.assertIn(doc1, client._shm)
        shm_name_1 = client._shm[doc1].name
        client.want(doc1, [(0, 1.0)])
        self.pump(lambda: len(pages) == 1)
        self.assertEqual(pages[0][:2], (doc1, 0))

        # Closing cleans up the segment on the listener thread without pumping Qt events.
        client.close(doc1)
        deadline = time.monotonic() + 5.0
        while doc1 in client._shm and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotIn(doc1, client._shm)
        if os.name != "nt":
            with self.assertRaises(FileNotFoundError):
                SharedMemory(name=shm_name_1, create=False)

        doc2 = client.open(data)
        self.assertIn(doc2, client._shm)
        shm_name_2 = client._shm[doc2].name
        client.shutdown()
        self.assertEqual(client._shm, {})
        if os.name != "nt":
            with self.assertRaises(FileNotFoundError):
                SharedMemory(name=shm_name_2, create=False)

    def test_open_falls_back_to_pipe_bytes_when_shared_memory_fails(self):
        qt_app()
        client = worker.RenderClient()
        self.addCleanup(client.shutdown)
        pages = []
        client.page_ready.connect(lambda *args: pages.append(args))
        with tempfile.TemporaryDirectory() as directory:
            data = make_pdf(Path(directory) / "fallback.pdf", pages=1).read_bytes()
        with patch.object(worker, "SharedMemory", side_effect=OSError("no shm")), \
             patch.object(client, "_send", wraps=client._send) as send:
            doc_id = client.open(data)
        self.assertEqual(send.call_args.args[0], "open")
        self.assertEqual(client._shm, {})
        client.want(doc_id, [(0, 1.0)])
        self.pump(lambda: len(pages) == 1)
        self.assertEqual(pages[0][:2], (doc_id, 0))

    def test_set_highlights_toggles_rendering_without_reopening(self):
        qt_app()
        client = worker.RenderClient()
        self.addCleanup(client.shutdown)
        pages = []
        client.page_ready.connect(lambda *args: pages.append(args))
        data = self._pdf_with_annotations()
        doc_id = client.open(data)

        def px(rgb, width, height, x, y):
            off = ((height - y) * width + x) * 3
            return tuple(rgb[off:off + 3])

        client.want(doc_id, [(0, 1.0)])
        self.pump(lambda: len(pages) == 1)
        _, _, _, w, h, rgb_on, _ = pages[-1]
        self.assertNotEqual(px(rgb_on, w, h, 50, 30), (255, 255, 255))
        self.assertNotEqual(px(rgb_on, w, h, 50, 90), (255, 255, 255))
        self.assertEqual(px(rgb_on, w, h, 50, 150), (0, 0, 0))

        with patch.object(client, "open", side_effect=AssertionError("must not reopen")) as _:
            client.set_highlights(doc_id, False)
            client.want(doc_id, [(0, 1.0)])
            self.pump(lambda: len(pages) == 2)
            _, _, _, _, _, rgb_off, _ = pages[-1]
            self.assertEqual(px(rgb_off, w, h, 50, 30), (255, 255, 255))
            self.assertEqual(px(rgb_off, w, h, 50, 90), (255, 255, 255))
            self.assertEqual(px(rgb_off, w, h, 50, 150), (0, 0, 0))

            client.set_highlights(doc_id, True)
            client.want(doc_id, [(0, 1.0)])
            self.pump(lambda: len(pages) == 3)
            self.assertEqual(pages[-1][5], rgb_on)


@needs_qt
class SelfTestTests(unittest.TestCase):
    """`--self-test` is what a build runs inside the frozen bundle, so its exit code must tell the truth."""

    def run_self_test(self, path):
        return subprocess.run([sys.executable, "-m", "paperassist.native", "--self-test", str(path)],
                              capture_output=True, text=True, timeout=120)

    def test_a_page_with_text_passes_and_a_blank_page_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            text = make_pdf(Path(directory) / "text.pdf", pages=1)
            blank = Path(directory) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(400, 500)
            writer.write(blank)
            passed, failed = self.run_self_test(text), self.run_self_test(blank)
        self.assertEqual((passed.returncode, failed.returncode), (0, 1), passed.stderr + failed.stderr)
        self.assertIn("self-test ok", passed.stdout)
        self.assertIn("self-test failed", failed.stdout)


if __name__ == "__main__":
    unittest.main()
