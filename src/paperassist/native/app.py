import argparse
import os
import sys

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from .document import PdfDocument
from .renderer import RenderClient
from .source import FileSource
from .window import MainWindow

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
    print(f"self-test {'ok' if dark else 'failed'}: {len(doc)} pages, page 1 is {width}x{height}, "
          f"{len(doc.words(0))} words, {dark:.2%} dark pixels")
    doc.close()
    return dark > 0


def run(argv=None):
    parser = argparse.ArgumentParser(prog="paperassist.native", description="PaperAssist native reader")
    parser.add_argument("pdfs", nargs="*", help="PDF files to open, one tab each")
    parser.add_argument("--self-test", metavar="FILE.pdf", help="render page 1 of FILE.pdf without a window and exit 0 or 1")
    args = parser.parse_args(argv)
    if args.self_test:
        return 0 if self_test(args.self_test) else 1
    app = QApplication(sys.argv[:1])
    app.setApplicationName("PaperAssist")
    client = RenderClient()
    window = MainWindow(client)
    window.show()
    for path in args.pdfs:
        window.open_source(FileSource(path))
    code = app.exec()
    client.shutdown()
    return code
