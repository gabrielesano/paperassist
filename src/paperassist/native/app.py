import argparse
import gc
import os
import signal
import socket
import sqlite3
import sys
from contextlib import ExitStack, contextmanager

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QSocketNotifier, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

import paperassist
from paperassist import paths
from paperassist.paths import locate_existing
from paperassist.workspace import LibraryInUse, Workspace, library_lock

from .about import COMPONENTS, component_info
from .document import PdfDocument
from .renderer import RenderClient
from .source import FileSource
from .theme import brand_icon
from .wayland import install_wayland_warning_filter
from .window import MainWindow
from .windows import hold_installation_mutex

SELF_TEST_SECONDS = 60  # a cold start of a frozen app on a busy CI runner can be slow


def self_test(path):
    """Open a PDF and draw page 1 through the render process without a window; True if the page is not blank.

    A build runs this inside the bundle: it proves that Qt, the PDF engine and the
    spawned render process all work there.
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"  # a CI runner has no display
    app = QApplication(sys.argv[:1])
    client = RenderClient()
    loop, pages = QEventLoop(), []
    client.page_ready.connect(lambda *page: (pages.append(page), loop.quit()))
    try:
        doc = PdfDocument(FileSource(path))
        client.want(client.open(doc.source.path), [(0, 1.0)])
        QTimer.singleShot(SELF_TEST_SECONDS * 1000, loop.quit)
        loop.exec()
    finally:
        client.shutdown()
    if not pages:
        print("self-test failed: the render process returned no page")
        return False
    _, _, _, width, height, rgb, _ = pages[0]
    dark = sum(value < 128 for value in rgb) / len(rgb)
    missing_meta = [
        title for title, names in COMPONENTS
        if "not installed" in component_info(names) or "unknown license" in component_info(names)
    ]
    ok = dark > 0 and not missing_meta
    print(f"self-test {'ok' if ok else 'failed'}: {len(doc)} pages, page 1 is {width}x{height}, "
          f"{len(doc.words(0))} words, {dark:.2%} dark pixels")
    if missing_meta:
        print(f"self-test metadata missing: {', '.join(missing_meta)}")
    doc.close()
    return ok


class LibraryMissing(RuntimeError):
    pass


@contextmanager
def open_workspace(data_dir):
    """The library for the window's lifetime. Closing the workspace comes before releasing the lock."""
    try:
        directory, source = locate_existing(data_dir)
    except FileNotFoundError as error:
        raise LibraryMissing(str(error)) from None
    with library_lock(directory):
        workspace = Workspace(directory, source)
        try:
            yield workspace
        finally:
            workspace.close()


@contextmanager
def termination_requests(app):
    """Deliver SIGTERM through the normal close guard, outside the signal handler."""
    closing = False

    def wakeup(*_args):
        nonlocal closing
        received = bytearray()
        while True:
            try:
                data = reader.recv(256)
                if not data:
                    break
                received.extend(data)
            except (BlockingIOError, InterruptedError):
                break
        if closing or signal.SIGTERM not in received:
            return
        closing = True
        try:
            windows = [window for window in app.topLevelWidgets()
                       if isinstance(window, MainWindow) and window.isVisible()]
            for window in windows:
                window.close()
            if not windows:
                app.quit()
        finally:
            closing = False

    reader, writer = socket.socketpair()
    reader.setblocking(False)
    writer.setblocking(False)
    notifier = QSocketNotifier(reader.fileno(), QSocketNotifier.Read, app)
    notifier.activated.connect(wakeup)
    previous_wakeup = signal.set_wakeup_fd(writer.fileno(), warn_on_full_buffer=False)
    previous = signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
    try:
        yield
    finally:
        notifier.setEnabled(False)
        signal.signal(signal.SIGTERM, previous)
        signal.set_wakeup_fd(previous_wakeup)
        notifier.deleteLater()
        QCoreApplication.sendPostedEvents(notifier, QEvent.DeferredDelete)
        writer.close()
        reader.close()


def run(argv=None):
    parser = argparse.ArgumentParser(prog="paperassist.native", description="PaperAssist native reader")
    parser.add_argument("-v", "--version", action="version", version=f"PaperAssist {paperassist.__version__}")
    parser.add_argument("pdfs", nargs="*", help="PDF files to open, one tab each; without any, the library opens")
    parser.add_argument("--data-dir", help="library directory (default: PAPERASSIST_DATA_DIR, the folder chosen in the app, "
                                           "or the per-user data directory)")
    parser.add_argument("--self-test", metavar="FILE.pdf", help="render page 1 of FILE.pdf without a window and exit 0 or 1")
    args = parser.parse_args(argv)
    hold_installation_mutex()
    if args.self_test:
        return 0 if self_test(args.self_test) else 1
    install_wayland_warning_filter()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("PaperAssist")
    app.setDesktopFileName("paperassist")
    app.setWindowIcon(brand_icon(64))
    with termination_requests(app):
        return _run_sessions(app, args)


def _run_sessions(app, args):
    data_dir = args.data_dir
    previous_library = None
    while True:
        with ExitStack() as session:
            try:
                workspace = session.enter_context(open_workspace(data_dir)) if not args.pdfs else None
            except (LibraryInUse, LibraryMissing, OSError, sqlite3.Error) as error:
                message = ("This library is open in another PaperAssist window or the browser; close it first."
                           if isinstance(error, LibraryInUse) else f"Could not open the library:\n{error}")
                if previous_library is None:
                    QMessageBox.warning(None, "PaperAssist", message)
                    return 1
                try:
                    paths.save_library(previous_library)
                except OSError as restore_error:
                    QMessageBox.warning(None, "PaperAssist", f"{message}\n\nCould not restore the previous choice:\n{restore_error}")
                    return 1
                QMessageBox.warning(None, "PaperAssist", f"{message}\n\nReopening the previous library: {previous_library}")
                previous_library, data_dir = None, None
                continue
            directory = workspace.data_dir if workspace else None
            code, target = show(app, workspace, args.pdfs)
        if not target:
            return code
        # prepare_switch persisted the chosen folder; reopen through settings, not as a fixed --data-dir.
        previous_library, data_dir = directory, None


def show(app, workspace, pdfs):
    client = RenderClient()
    window = MainWindow(client, workspace)
    window.show()
    for path in pdfs:
        window.open_source(FileSource(path))
    if workspace and not pdfs:
        window.show_library()
    try:
        return app.exec(), window.switch_target
    finally:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
        del window
        gc.collect()
        client.shutdown()
