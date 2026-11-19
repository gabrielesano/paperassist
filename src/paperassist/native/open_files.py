"""Receive external PDFs as managed copies while keeping reader edits and focus intact."""
from collections import deque
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication

from .instance import absolute_paths
from .jobs import JobWatcher


class FileOpenEvents(QObject):
    """Buffer Finder/Dock requests until the active library window is ready."""
    def __init__(self, app):
        super().__init__(app)
        self.pending, self.receiver = [], None
        app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.FileOpen:
            if path := event.file() or event.url().toLocalFile():
                self.pending.append(path)
                self.flush()
            return True
        return False

    def flush(self):
        if self.receiver and self.pending:
            paths, self.pending = self.pending, []
            try:
                self.receiver(paths)
            except RuntimeError:
                self.pending = paths + self.pending
                QTimer.singleShot(100, self.flush)


class OpenFiles(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending = deque()
        self.watcher = None
        self.wait = QTimer(self, singleShot=True, interval=100, timeout=self._next)

    @property
    def busy(self):
        return self.watcher is not None or bool(self.pending)

    def receive(self, paths, quick=False):
        if getattr(self.window, '_closing', False):
            raise RuntimeError('PaperAssist is closing this window. Finish the close dialog and try again.')
        paths = absolute_paths(paths)
        tab = self.window.tab() if quick else None
        self.activate()
        if not paths:
            if not self.window.tabs.count():
                self.window.show_library()
            return
        for path in paths:
            request = (path, tab)
            if request not in self.pending:
                self.pending.append(request)
        self._next()

    def activate(self):
        target = QApplication.activeModalWidget() or self.window
        if target.isMinimized():
            target.showNormal()
        target.show()
        target.raise_()
        target.activateWindow()
        if target.windowHandle():
            target.windowHandle().requestActivate()

    def _next(self):
        if self.watcher or not self.pending:
            return
        if QApplication.activeModalWidget():
            self.wait.start()
            return
        if self.window.library_dialog:
            self.window.library_dialog.hide()
        path, tab = self.pending.popleft()
        try:
            job = self.window.workspace.open_files([path])
        except (OSError, ValueError, RuntimeError) as error:
            self.window.statusBar().showMessage(f'Could not open {Path(path).name}: {error}')
            self.wait.start()
            return
        self.window.statusBar().showMessage(f'Opening {Path(path).name} as a managed library copy…')
        self.watcher = JobWatcher(self.window.workspace, job, self)
        self.watcher.finished.connect(lambda job: self._opened(job, tab))

    def _opened(self, job, tab):
        self.watcher.deleteLater()
        self.watcher = None
        result = job.get('result', {})
        errors = [entry['error'] for entry in result.get('errors', [])]
        for document in result.get('documents', []):
            if tab is not None:
                tabs = self.window.tabs
                if not any(tabs.widget(i) is tab for i in range(tabs.count())):
                    errors.append('The Quick view tab was closed. The PDF is available in Library.')
                    continue
                opened = self.window.open_library_document_in_quick_view(document['id'], tab=tab)
            else:
                opened = self.window.open_library_document(document['id'])
            if opened is None:
                errors.append(self.window.statusBar().currentMessage() or 'Opening was cancelled.')
        if job.get('error'):
            errors.append(job['error'])
        if errors:
            self.window.statusBar().showMessage('Could not open PDF: ' + '; '.join(errors))
        elif not self.pending:
            self.window.statusBar().showMessage('Opened from the library. Save keeps the original file unchanged.', 6000)
        QTimer.singleShot(0, self._next)

    def close(self):
        self.wait.stop()
        self.pending.clear()
        if self.watcher:
            self.watcher.cancel()
            self.watcher.stop()
