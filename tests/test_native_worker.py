"""The render process: its replacing queue and the real spawned process over pipes."""
import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_native_support import make_pdf, needs_pdf, needs_qt, pymupdf

if pymupdf:
    from paperassist.native import worker


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
            with pymupdf.open() as doc:
                doc.new_page()
                doc.save(blank)
            passed, failed = self.run_self_test(text), self.run_self_test(blank)
        self.assertEqual((passed.returncode, failed.returncode), (0, 1), passed.stderr + failed.stderr)
        self.assertIn("self-test ok", passed.stdout)
        self.assertIn("self-test failed", failed.stdout)


if __name__ == "__main__":
    unittest.main()
