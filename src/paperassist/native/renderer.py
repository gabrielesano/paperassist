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
        self._arrived.connect(lambda message: self.page_ready.emit(*message))
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        # recv blocks in a read that releases the GIL; the signal hops to the UI thread.
        while True:
            try:
                _, doc_id, page, scale, width, height, seconds = self._results.recv()
                self._arrived.emit((doc_id, page, scale, width, height, self._results.recv_bytes(), seconds))
            except (EOFError, OSError):
                return

    def _send(self, *message):
        try:
            self._commands.send(message)
        except (BrokenPipeError, OSError):
            pass  # the render process is gone; the pages stay as they are

    def open(self, path):
        doc_id = next(self._ids)
        self._send("open", doc_id, str(path))
        return doc_id

    def close(self, doc_id):
        self._send("close", doc_id)

    def want(self, doc_id, jobs):
        """Replace the queue of (page, scale) jobs for one document, most urgent first."""
        self._send("want", doc_id, jobs)

    def shutdown(self):
        self._send("quit")
        self.process.join(5)
        if self.process.is_alive():
            self.process.terminate()
