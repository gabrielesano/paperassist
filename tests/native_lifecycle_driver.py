"""Disposable real-app process used by the native lifecycle tests."""
import faulthandler
import io
import json
import signal
import sys
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from paperassist import paths
from paperassist.native import app as native_app
from paperassist.native.window import MainWindow
from paperassist.workspace import LibraryInUse, Workspace, library_lock
from support import make_pdf


def emit(kind, **values):
    print(json.dumps({"type": kind, **values}), flush=True)


def run(mode, root):
    def trace(message):
        print(message, file=sys.stderr, flush=True)

    receive = native_app.OpenFiles.receive
    close_event = MainWindow.closeEvent

    def traced_receive(opener, pdfs, quick=False):
        trace(f"receive {pdfs!r} kw={quick}")
        return receive(opener, pdfs, quick)

    def traced_close(window, event):
        trace(f"close requested: busy={window.file_opener.busy} tabs={window.tabs.count()} status={window.statusBar().currentMessage()!r}")
        try:
            return close_event(window, event)
        finally:
            trace(f"close accepted={event.isAccepted()} widgets={[(type(w).__name__, w.isVisible()) for w in app.topLevelWidgets()]}")

    directory = root / "library"
    workspace = Workspace(directory)
    source = make_pdf(root / "source.pdf", pages=2)
    document = workspace.library.import_stream(io.BytesIO(source.read_bytes()), source.name)
    workspace.close()
    paths.save_library(directory)
    app = QApplication([])
    state = {"started": False, "prompts": 0, "answered": 0}

    if mode == "fallback":
        target = root / "target"
        Workspace(target).close()

    def job(_job, cancel):
        cancel.wait(10)
        emit("draining")
        time.sleep(0.3)
        try:
            with library_lock(directory):
                locked = False
        except LibraryInUse:
            locked = True
        (root / "job-result.json").write_text(json.dumps({"cancelled": cancel.is_set(), "locked": locked}))

    def tick():
        boxes = [widget for widget in app.topLevelWidgets() if isinstance(widget, QMessageBox) and widget.isVisible()]
        if boxes:
            box = boxes[0]
            if state.get("box") is box:
                return
            state["box"] = box
            state["prompts"] += 1
            emit("prompt", count=state["prompts"], text=box.text())
            answer = (QMessageBox.Ok if mode == "fallback" else QMessageBox.Save if mode == "save"
                      else QMessageBox.Cancel if state["prompts"] == 1 else QMessageBox.Discard)

            def respond():
                state["answered"] += 1
                box.button(answer).click()
            QTimer.singleShot(150, respond)
            return
        # Cocoa dispatches events during window creation, before the Qt event loop starts.
        if not QThread.currentThread().loopLevel():
            return
        windows = [widget for widget in app.topLevelWidgets() if isinstance(widget, MainWindow) and widget.isVisible()]
        if not windows:
            return
        window = windows[0]
        if state["started"]:
            if mode == "fallback" and window.workspace is not state["old_workspace"]:
                emit("recovered", directory=str(window.workspace.data_dir), source=window.workspace.source)
                window.close()
            elif mode == "cancel" and state["answered"] == 1 and not state.get("cancelled"):
                state["cancelled"] = True
                pane = window.pane()
                emit("cancelled", modified=pane.modified, draft=window.comments.has_draft(pane), prompts=state["prompts"])
            return
        trace(f"driver ready: dialog={window.library_dialog!r} busy={window.file_opener.busy} status={window.statusBar().currentMessage()!r}")
        state.update(started=True, old_workspace=window.workspace)
        if window.library_dialog is not None:
            window.library_dialog.reject()
        window.acquisition.main_opened = lambda document_id: None
        if mode == "fallback":
            prepare = window.workspace.prepare_switch

            def disconnect(path, action):
                selected = prepare(path, action)
                selected.rename(root / "disconnected")
                return selected
            window.workspace.prepare_switch = disconnect
            window.show_preferences()
            dialog = window.preferences_dialog
            dialog.library_edit.setText(str(target))
            dialog.library_check.click()
            dialog.confirm_yes.click()
            return
        if mode == "job":
            window.workspace.submit(job)
        else:
            pane = window.open_library_document(document["id"]).main
            pane.view.doc.add_ink(0, [[(60, 100), (160, 150)]])
            pane.view._annotation_changed([0])
            window.comments.compose()
            window.comments.panel.text.setPlainText("Pending lifecycle comment")
        emit("ready", worker=window.client.process.pid)

    timer = QTimer(app, interval=20, timeout=tick)
    timer.start()
    QTimer.singleShot(15000, lambda: app.exit(9))
    previous = signal.getsignal(signal.SIGTERM)
    faulthandler.dump_traceback_later(8)
    with patch.object(native_app.OpenFiles, "receive", traced_receive), patch.object(MainWindow, "closeEvent", traced_close):
        code = native_app.run([])
    faulthandler.cancel_dump_traceback_later()
    timer.stop()
    emit("finished", code=code, restored=signal.getsignal(signal.SIGTERM) == previous,
         library=str(paths.saved_library()))
    return code


if __name__ == "__main__":
    root = Path(sys.argv[2]).resolve()
    with patch("paperassist.paths.config_file", return_value=root / "config.json"):
        raise SystemExit(run(sys.argv[1], root))
