"""Follow a Workspace job from the UI thread: a timer polls its status, so no widget ever waits."""
from PySide6.QtCore import QObject, QTimer, Signal

DONE = ("completed", "failed", "cancelled")


class JobWatcher(QObject):
    progress = Signal(dict)  # the job record while it runs
    finished = Signal(dict)  # the final record: status, result or error

    def __init__(self, workspace, job, parent=None, interval=100):
        super().__init__(parent)
        self.workspace, self.job_id = workspace, job["id"]
        self.timer = QTimer(self, interval=interval, timeout=self._poll)
        self.timer.start()

    def cancel(self):
        self.workspace.cancel_job(self.job_id)

    def stop(self):
        self.timer.stop()

    def _poll(self):
        job = self.workspace.job_status(self.job_id)
        if job["status"] in DONE:
            self.timer.stop()
            self.finished.emit(job)
        else:
            self.progress.emit(job)
