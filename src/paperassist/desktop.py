"""Native window around the existing loopback reader."""

import os
import signal
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from paperassist import paths, picker
from paperassist.server import LibraryInUse, running_url, serve


class ManagedServer:
    """Own a server thread, including shutdown across library switches."""

    def __init__(self, data_dir, source='default', port=8765, choose_folder=None):
        self.data_dir, self.source, self.port = data_dir, source, port
        self.choose_folder = choose_folder
        self.ready = threading.Event()
        self.server = self.error = None
        self.stopping = False

    def _run(self):
        try:
            serve('127.0.0.1', self.port, self.data_dir, source=self.source,
                  choose_folder=self.choose_folder, on_listen=self._on_listen)
        except Exception as error:
            self.error = error
        finally:
            self.ready.set()

    def _on_listen(self, server):
        self.server = server
        self.ready.set()
        if self.stopping:
            threading.Thread(target=server.shutdown).start()

    def start(self):
        self.thread = threading.Thread(target=self._run, name='paperassist-server')
        self.thread.start()
        self.ready.wait()
        if self.error:
            self.thread.join()
            raise self.error
        return f'http://127.0.0.1:{self.port}'

    def stop(self):
        self.stopping = True
        if self.server:
            self.server.shutdown()
        if hasattr(self, 'thread'):
            self.thread.join()


class DesktopBridge:
    """Ask the app before closing, without blocking the GUI thread."""

    def __init__(self, window, origin):
        self.window, self.origin = window, urlsplit(origin)[:2]
        self.pending = self.approved = False

    def closing(self):
        if self.approved or not self.window.events.loaded.is_set():
            return True
        if not self.pending:
            self.pending = True
            threading.Thread(target=self._request_close, daemon=True).start()
        return False

    def _finish_close(self, approved):
        self.pending = False
        if approved is True:
            self.approved = True
            self.window.destroy()

    def _request_close(self):
        try:
            if urlsplit(self.window.get_current_url() or '')[:2] != self.origin:
                self._finish_close(True)
                return
            self.window.evaluate_js(
                'Promise.resolve().then(() => window.paperassistDesktop ? '
                'window.paperassistDesktop.requestClose() : true).catch(() => false)',
                callback=lambda result: threading.Thread(target=self._finish_close,
                                                          args=(result,), daemon=True).start())
        except Exception as error:
            self.pending = False
            print(f'Could not request window close: {error}', file=sys.stderr)


def run(data_dir, source='default', port=8765):
    """Run on the main thread; stop only the backend this window owns."""
    backend = None
    def interrupt(signum, frame):
        # Qt's Ctrl+C handler quits its loop; raising in a Qt callback aborts Python.
        handler = signal.getsignal(signal.SIGINT)
        (handler if callable(handler) else signal.default_int_handler)(signum, frame)
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        try:
            os.environ.setdefault('QT_LOGGING_RULES', 'qt.qpa.wayland.textinput=false')
            import webview
        except ImportError:
            raise RuntimeError('Desktop support is not installed. Install "paperassist[desktop]"; '
                               'browser mode: paperassist serve --open.') from None
        window = None
        def choose_folder(start=None, title='Folder for manual downloads'):
            directory = Path(start).expanduser() if start else Path.home()
            try:
                selection = window.create_file_dialog(webview.FileDialog.FOLDER,
                    directory=str(directory if directory.is_dir() else Path.home()))
                return str(selection[0]) if selection else None
            except Exception as error:
                raise picker.PickerUnavailable(f'The folder window could not be opened: {error}') from error
        backend = ManagedServer(data_dir, source, port, choose_folder)
        try:
            url = backend.start()
        except LibraryInUse:
            backend = None
            url = running_url(data_dir)
            if not url:
                raise RuntimeError('This library is already in use, but its desktop address is unavailable. '
                                   'Close the other PaperAssist instance and try again.') from None
        window = webview.create_window('PaperAssist', url, width=1400, height=900,
                                       min_size=(800, 600), text_select=True)
        window.events.closing += DesktopBridge(window, url).closing
        profile = paths.config_file().parent / 'webview'
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        webview.start(private_mode=False, storage_path=str(profile),
                      **({'gui': 'qt'} if sys.platform.startswith('linux') else {}))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f'Could not start PaperAssist desktop: {error}', file=sys.stderr)
        return 1
    finally:
        try:
            if backend:
                backend.stop()
        finally:
            signal.signal(signal.SIGTERM, previous)
    return 0
