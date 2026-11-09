"""A real application owner controlled through disposable test files."""
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from paperassist.native import app as native_app
from paperassist.native.window import MainWindow


def run(root, source):
    app = QApplication([])
    state = {'sequence': 0}

    def tick():
        window = next((widget for widget in app.topLevelWidgets()
                       if isinstance(widget, MainWindow) and widget.isVisible()), None)
        if window is None:
            return
        window.acquisition.main_opened = lambda document_id: None
        if window.file_opener.busy or not window.pane():
            return
        command = root / 'command.json'
        if command.exists():
            action = json.loads(command.read_text())
            command.unlink()
            pane = window.pane()
            if action['action'] == 'dirty':
                pane.view.doc.add_ink(0, [[(60, 100), (160, 150)]])
                pane.view._annotation_changed([0])
                window.comments.compose()
                window.comments.panel.text.setPlainText('Pending external-open comment')
            elif action['action'] == 'close':
                window.confirm = lambda panes: True
                window.close()
                return
            state['sequence'] = action['sequence']
        panes = list(window.all_panes())
        report = {'sequence': state['sequence'], 'pid': os.getpid(), 'tabs': window.tabs.count(),
                  'active': window.pane().key,
                  'panes': [{'key': pane.key, 'identity': id(pane.view.doc), 'modified': pane.modified,
                             'draft': window.comments.has_draft(pane)} for pane in panes],
                  'documents': window.workspace.library.list_documents()}
        pending = root / 'status.tmp'
        pending.write_text(json.dumps(report))
        pending.replace(root / 'status.json')

    timer = QTimer(app, interval=30, timeout=tick)
    timer.start()
    QTimer.singleShot(45000, lambda: app.exit(9))
    return native_app.run(['--data-dir', str(root / 'library'), source])


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]), sys.argv[2]))
