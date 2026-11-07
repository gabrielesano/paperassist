"""The render process.

PDFium is not thread-safe. Drawing lives in a separate process so a slow page
cannot stall the Qt event loop or overlap a structural engine call in the UI.

It is started with multiprocessing's explicit "spawn" context and pipes. Unlike
`sys.executable -m ...` this keeps working in a frozen app, where the executable is
the app itself and `freeze_support()` routes the child to `serve`.

Protocol, parent to child: ("open", id, path), ("close", id), ("want", id, jobs),
("locate", req_id, id, ref) and ("quit",). A want list replaces the previous one for that
document, most urgent first, so a fast scroll overtakes pages nobody looks at any more.
Child to parent: ("page", id, page, scale, width, height, seconds) followed by raw RGB
bytes, or ("citations", req_id, result, error).
"""
import multiprocessing
import threading
import time
from pathlib import Path

from .citations import CitationIndex
from .engine import PageRenderer, trim_caches
from .trim import trim_heap

IDLE_TRIM_SECONDS = 2


class WantQueue:
    """Open documents and the latest wanted (page, scale) list of each, shared by two threads."""

    def __init__(self):
        self.cond = threading.Condition()
        self.wanted, self.commands = {}, []

    def post(self, message):
        with self.cond:
            if message[0] == "want":
                self.wanted[message[1]] = message[2]
            else:
                self.commands.append(message)
                if message[0] == "close":
                    self.wanted.pop(message[1], None)
            self.cond.notify()

    def has_work(self, open_ids):
        return bool(self.commands) or self.next_job(open_ids) is not None

    def take_commands(self):
        commands, self.commands = self.commands, []
        return commands

    def next_job(self, open_ids):
        """The most urgent wanted page of the first document that has one, or None."""
        return next(((i, jobs[0]) for i, jobs in self.wanted.items() if jobs and i in open_ids), None)

    def done(self, doc_id, job):
        self.wanted[doc_id] = [j for j in self.wanted.get(doc_id, []) if j != job]


def listen(inbox, queue):
    """Read commands on their own thread so a new 'want' list can overtake queued pages."""
    while True:
        try:
            message = inbox.recv()
        except (EOFError, OSError):  # the parent died: leave with it
            message = ("quit",)
        queue.post(message)
        if message[0] == "quit":
            return


def serve(inbox, outbox):
    queue, docs, indices, trimmed = WantQueue(), {}, {}, True
    threading.Thread(target=listen, args=(inbox, queue), daemon=True).start()
    while True:
        with queue.cond:
            # Return freed pixels after a quiet moment instead of churning the allocator per page.
            woke = queue.cond.wait_for(lambda: queue.has_work(docs), timeout=None if trimmed else IDLE_TRIM_SECONDS)
            if not woke:
                trim_caches()
                trim_heap()
                trimmed = True
                continue
            commands = queue.take_commands()
        for kind, *args in commands:
            if kind == "open":
                try:
                    docs[args[0]] = PageRenderer(args[1] if isinstance(args[1], bytes) else Path(args[1]).read_bytes())
                except Exception:  # a vanished or broken file leaves blank pages, not a dead process
                    docs.pop(args[0], None)
            elif kind == "close":
                if document := docs.pop(args[0], None):
                    document.close()
                if index := indices.pop(args[0], None):
                    index.close()
            elif kind == "locate":
                req_id, doc_id, ref = args
                try:
                    if doc_id not in docs:
                        raise ValueError("Document is closed")
                    if doc_id not in indices:
                        indices[doc_id] = CitationIndex(docs[doc_id].data)
                    outbox.send(("citations", req_id, indices[doc_id].locate(ref), ""))
                except Exception as error:
                    outbox.send(("citations", req_id, None, str(error) or type(error).__name__))
                trimmed = False
            elif kind == "quit":
                for index in indices.values():
                    index.close()
                for document in docs.values():
                    document.close()
                return
        with queue.cond:
            job = queue.next_job(docs)
        if job is None:
            continue
        doc_id, (page, scale) = job
        start = time.perf_counter()
        try:
            rendered = docs[doc_id].render(page, scale)
        except Exception:
            rendered = None
        seconds = time.perf_counter() - start
        with queue.cond:  # the UI may have replaced the list meanwhile
            queue.done(doc_id, (page, scale))
        if rendered is None:
            continue
        outbox.send(("page", doc_id, page, scale, rendered.width, rendered.height, seconds))
        outbox.send_bytes(rendered.rgb)  # raw bytes skip a pickling copy of ~20 MB
        trimmed = False


def start():
    """Launch the render process; returns (process, send, receive) with plain pipe ends."""
    context = multiprocessing.get_context("spawn")
    commands_in, commands_out = context.Pipe(duplex=False)
    results_in, results_out = context.Pipe(duplex=False)
    process = context.Process(target=serve, args=(commands_in, results_out), daemon=True)
    process.start()
    commands_in.close()  # the parent keeps only its own ends, so EOF reaches the child if we die
    results_out.close()
    return process, commands_out, results_in
