"""Qt side of the render process (see worker.py for the protocol and why it is a process)."""
import itertools
import threading

from PySide6.QtCore import QObject, Signal

from . import worker


class RenderClient(QObject):
    page_ready = Signal(int, int, float, int, int, object, float)  # doc, page, scale, width, height, rgb, seconds
    _arrived = Signal(object)

    def __init__(self):
        super().__init__()
        self.process, self._commands, self._results = worker.start()
        self._ids = itertools.count(1)
        self._pending = {}
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
                elif message[0] == "citations":
                    self._arrived.emit(message)
            except (EOFError, OSError):
                return

    def _deliver(self, message):
        if message[0] == "page":
            self.page_ready.emit(*message[1:])
        elif callback := self._pending.pop(message[1], None):
            callback(message[2], message[3])

    def _send(self, *message):
        try:
            self._commands.send(message)
            return True
        except (BrokenPipeError, OSError):
            return False  # the render process is gone; the pages stay as they are

    def open(self, path):
        doc_id = next(self._ids)
        self._send("open", doc_id, path if isinstance(path, bytes) else str(path))
        return doc_id

    def close(self, doc_id):
        self._send("close", doc_id)

    def want(self, doc_id, jobs):
        """Replace the queue of (page, scale) jobs for one document, most urgent first."""
        self._send("want", doc_id, jobs)

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
