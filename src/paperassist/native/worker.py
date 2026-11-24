"""The render process and its Qt client.

PDFium is not thread-safe. Drawing lives in a separate process so a slow page
cannot stall the Qt event loop or overlap a structural engine call in the UI.

It is started with multiprocessing's explicit "spawn" context and pipes. Unlike
`sys.executable -m ...` this keeps working in a frozen app, where the executable is
the app itself and `freeze_support()` routes the child to `serve`.

Protocol, parent to child: ("open", id, path_or_bytes[, inherit_from]), ("close", id),
("want", id, jobs), ("search", id, request, term, first, direction), ("cancel_search", id),
("locate", req_id, id, ref) and ("quit",). A want list replaces the
previous one for that document, most urgent first, so a fast scroll overtakes pages nobody
looks at any more. Child to parent: ("page", id, page, scale, width, height, seconds)
followed by raw RGB bytes, ("matches", id, request, page_hits, done, error), or
("citations", req_id, result, error).
"""
import gc
import itertools
import multiprocessing
import threading
import time
from pathlib import Path

from paperassist.citations import CitationIndex

from .engine import Pdf
from .platform import trim_heap

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


def search_pages(pdf, term, first, direction):
    count = len(pdf.sizes)
    for step in range(count):
        page = (first + step * direction) % count
        yield pdf.search_page(page, term), step + 1 == count


def serve(inbox, outbox):
    queue, docs, indices, trimmed = WantQueue(), {}, {}, True
    searches = {}  # Each pane has at most one active search.
    threading.Thread(target=listen, args=(inbox, queue), daemon=True).start()
    while True:
        with queue.cond:
            # Return freed pixels after a quiet moment instead of churning the allocator per page.
            woke = queue.cond.wait_for(lambda: bool(searches) or queue.has_work(docs), timeout=None if trimmed else IDLE_TRIM_SECONDS)
            if not woke:
                gc.collect()
                trim_heap()
                trimmed = True
                continue
            commands = queue.take_commands()
        for kind, *args in commands:
            if kind == "open":
                doc_id, payload = args[0], args[1]
                inherit = args[2] if len(args) > 2 else None
                try:
                    pdf = Pdf(payload if isinstance(payload, bytes) else Path(payload).read_bytes())
                    docs[doc_id] = pdf
                    if inherit is not None and (index := indices.pop(inherit, None)) is not None:
                        index._pdf = pdf
                        indices[doc_id] = index
                except Exception:  # a vanished or broken file leaves blank pages, not a dead process
                    docs.pop(doc_id, None)
            elif kind == "search":
                doc_id, request, term, first, direction = args
                if doc_id in docs:
                    searches[doc_id] = request, search_pages(docs[doc_id], term, first, direction)
                else:
                    outbox.send(("matches", doc_id, request, [], True, "Document unavailable"))
            elif kind == "cancel_search":
                searches.pop(args[0], None)
            elif kind == "close":
                searches.pop(args[0], None)
                if document := docs.pop(args[0], None):
                    document.close()
                if index := indices.pop(args[0], None):
                    index.close()
                trimmed = False
            elif kind == "locate":
                req_id, doc_id, ref = args
                try:
                    if doc_id not in docs:
                        raise ValueError("Document is closed")
                    if doc_id not in indices:
                        indices[doc_id] = CitationIndex(docs[doc_id])
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
            if searches:
                # Yield after each page so new renders and cancellations take priority.
                doc_id = next(iter(searches))
                request, pages = searches.pop(doc_id)
                try:
                    hits, done = next(pages)
                except Exception as error:
                    outbox.send(("matches", doc_id, request, [], True, str(error) or "Search failed"))
                else:
                    if hits or done:
                        outbox.send(("matches", doc_id, request, hits, done, ""))
                    if not done:
                        searches[doc_id] = request, pages
                trimmed = False
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


def _make_render_client():
    # Imported lazily so the spawned worker process never loads Qt.
    from PySide6.QtCore import QObject, Signal

    class RenderClient(QObject):
        page_ready = Signal(int, int, float, int, int, object, float)  # doc, page, scale, width, height, rgb, seconds
        _arrived = Signal(object)

        def __init__(self):
            super().__init__()
            self.process, self._commands, self._results = start()
            self._ids = itertools.count(1)
            self._pending = {}
            self._searches = {}
            self._arrived.connect(self._deliver)
            threading.Thread(target=self._listen, daemon=True).start()

        def _listen(self):
            # recv blocks in a read that releases the GIL; the signal hops to the UI thread.
            while True:
                try:
                    message = self._results.recv()
                    if message[0] == "page":
                        _, doc_id, page, scale, width, height, seconds = message
                        self._arrived.emit(("page", doc_id, page, scale, width, height, self._results.recv_bytes(), seconds))
                    elif message[0] in ("citations", "matches"):
                        self._arrived.emit(message)
                except (EOFError, OSError):
                    self._arrived.emit(("disconnected",))
                    return

        def _deliver(self, message):
            if message[0] == "page":
                self.page_ready.emit(*message[1:])
            elif message[0] == "matches":
                _, doc_id, request, hits, done, error = message
                pending = self._searches.get(doc_id)
                if pending and request == pending[0]:
                    if done:
                        del self._searches[doc_id]
                    pending[1](hits, done, error)
            elif message[0] == "disconnected":
                pending, self._searches = self._searches, {}
                for _, callback in pending.values():
                    callback([], True, "Render process unavailable")
            elif callback := self._pending.pop(message[1], None):
                callback(message[2], message[3])

        def _send(self, *message):
            try:
                self._commands.send(message)
                return True
            except (BrokenPipeError, OSError):
                return False  # the render process is gone; the pages stay as they are

        def open(self, path, *, inherit_from=None):
            doc_id = next(self._ids)
            payload = path if isinstance(path, bytes) else str(path)
            if inherit_from is not None:
                self._send("open", doc_id, payload, inherit_from)
            else:
                self._send("open", doc_id, payload)
            return doc_id

        def close(self, doc_id):
            self._searches.pop(doc_id, None)
            self._send("close", doc_id)

        def want(self, doc_id, jobs):
            """Replace the queue of (page, scale) jobs for one document, most urgent first."""
            self._send("want", doc_id, jobs)

        def search(self, doc_id, term, page, direction, callback):
            request = next(self._ids)
            self._searches[doc_id] = request, callback
            if not self._send("search", doc_id, request, term, page, direction):
                self._searches.pop(doc_id, None)
                callback([], True, "Render process unavailable")

        def cancel_search(self, doc_id):
            if self._searches.pop(doc_id, None):
                self._send("cancel_search", doc_id)

        def locate(self, doc_id, ref, callback):
            req_id = next(self._ids)
            self._pending[req_id] = callback
            if not self._send("locate", req_id, doc_id, ref):
                self._pending.pop(req_id, None)
                callback(None, "Render process unavailable")

        def shutdown(self):
            self._send("quit")
            self.process.join(5)
            if self.process.is_alive():
                self.process.terminate()

    return RenderClient


def __getattr__(name):
    if name == "RenderClient":
        global RenderClient
        RenderClient = _make_render_client()
        return RenderClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
