"""Preferences dialog: interface chrome size, downloads folder, and library location."""
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QVBoxLayout,
)
from shiboken6 import isValid

from paperassist import paths, settings
from paperassist.workspace import WorkspaceConflict

from .interface_zoom import ZOOM_CHOICES, parse_zoom, window_style
from .theme import ERROR_INK


def _button(text, name, accessible, primary=False):
    btn = QPushButton(text, objectName=name, accessibleName=accessible)
    if primary:
        btn.setProperty("primary", True)
    return btn


class PreferencesDialog(QDialog):
    def __init__(self, host):
        super().__init__(host, objectName="preferences-dialog", windowTitle="Preferences")
        self.host, self.zoom_value, self.base_font = host, 1, QFont(host.font())
        self._return_focus = None
        self._saved_inbox, self._plan = "", None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(8)
        layout.setSizeConstraint(QLayout.SetFixedSize)
        layout.addWidget(QLabel("<b>Preferences</b>"))

        size_label = QLabel("Interface size")
        size_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(size_label)
        self.size_combo = QComboBox(objectName="preferences-zoom", accessibleName="Interface size")
        for value in ZOOM_CHOICES:
            self.size_combo.addItem(f"{round(value * 100)}%", value)
        layout.addWidget(self.size_combo)
        layout.addWidget(QLabel("Applies at once to the toolbar, panels and dialogs on this computer. "
                                "Documents keep their own zoom.", objectName="muted-note", wordWrap=True))

        self.library_section = QFrame()
        section = QVBoxLayout(self.library_section)
        section.setContentsMargins(0, 6, 0, 0)
        section.setSpacing(6)
        self.inbox_edit = QLineEdit(objectName="preferences-inbox", accessibleName="Folder for manual downloads")
        self.inbox_browse = _button("Browse…", "preferences-browse", "Browse for the manual downloads folder")
        self.inbox_save = _button("Save", "preferences-inbox-save", "Save manual downloads folder", primary=True)
        self.inbox_clear = _button("Clear", "preferences-inbox-clear", "Clear manual downloads folder")
        self.library_edit = QLineEdit(objectName="preferences-library", accessibleName="Library location")
        self.library_browse = _button("Browse…", "preferences-library-browse", "Browse for the library folder")
        self.library_check = _button("Use this folder", "preferences-library-use", "Use this library folder")
        self.library_note = QLabel(objectName="muted-note", wordWrap=True)

        for title, field, buttons, note in (
            ("Folder for manual downloads", self.inbox_edit, (self.inbox_browse, self.inbox_save, self.inbox_clear),
             QLabel("Save PDFs you download by hand here; PaperAssist copies matching ones "
                    "into the library and never changes this folder.", objectName="muted-note", wordWrap=True)),
            ("Library location", self.library_edit, (self.library_browse, self.library_check), self.library_note),
        ):
            label = QLabel(title)
            label.setStyleSheet("font-weight: 600;")
            label.setBuddy(field)
            section.addWidget(label)
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(field, 1)
            for button in buttons:
                row.addWidget(button)
            section.addLayout(row)
            section.addWidget(note)

        self.confirm_box = QFrame(objectName="preferences-library-confirm", visible=False)
        self.confirm_label = QLabel(objectName="preferences-library-question", wordWrap=True)
        self.confirm_yes = _button("Open library", "preferences-library-yes", "Confirm library folder", primary=True)
        self.confirm_no = _button("Cancel", "preferences-library-no", "Cancel library folder change")
        confirm_layout = QVBoxLayout(self.confirm_box)
        confirm_layout.setContentsMargins(12, 10, 12, 10)
        confirm_layout.setSpacing(8)
        confirm_layout.addWidget(self.confirm_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        for button in (self.confirm_yes, self.confirm_no):
            row.addWidget(button)
        row.addStretch(1)
        confirm_layout.addLayout(row)
        section.addWidget(self.confirm_box)

        self.message = QLabel(objectName="preferences-message", accessibleName="Preferences status",
                              wordWrap=True, visible=False)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        for widget in (self.library_section, self.message, buttons):
            layout.addWidget(widget)

        self.size_combo.currentIndexChanged.connect(self._on_size_changed)
        self.inbox_browse.clicked.connect(lambda: self._browse(self.inbox_edit, "Folder for manual downloads"))
        self.inbox_save.clicked.connect(self._save_inbox)
        self.inbox_clear.clicked.connect(lambda: self._save_inbox(clear=True))
        self.inbox_edit.returnPressed.connect(self._save_inbox)
        self.library_browse.clicked.connect(lambda: self._browse(self.library_edit, "Library folder"))
        self.library_check.clicked.connect(self._inspect_library)
        self.library_edit.returnPressed.connect(self._inspect_library)
        self.library_edit.textEdited.connect(self._close_confirm)
        self.confirm_yes.clicked.connect(self._confirm_switch)
        self.confirm_no.clicked.connect(self._close_confirm)
        self.finished.connect(self._restore_focus)
        self._refresh_workspace()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.ignore()
        else:
            super().keyPressEvent(event)

    def _restore_focus(self, _result):
        self.host.activateWindow()
        target = self._return_focus
        if target is None or not isValid(target) or not target.isVisible() or not target.isEnabled():
            pane = self.host.pane()
            target = pane.view if pane else self.host.app_menu_button
        target.setFocus()

    def _say(self, text, bad=False):
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {ERROR_INK};" if bad else "")
        self.message.setVisible(bool(text))

    def _close_confirm(self, *_):
        self._plan = None
        self.confirm_box.hide()

    def _refresh_workspace(self):
        self._close_confirm()
        ws = self.host.workspace
        self.library_section.setVisible(ws is not None)
        if ws is None:
            return
        self._saved_inbox = ws.get_settings().inbox
        self.inbox_edit.setText(self._saved_inbox)
        reason = paths.FIXED.get(ws.source)
        self.library_edit.setText(str(ws.data_dir))
        self.library_edit.setReadOnly(bool(reason))
        self.library_browse.setEnabled(not reason)
        self.library_check.setEnabled(not reason)
        self.library_note.setText(reason or "PaperAssist remembers this choice for your user account.")

    def _unsaved_changes(self):
        message = ""
        if any(p.modified or self.host.comments.has_draft(p) for p in self.host.all_panes()):
            message = "Save or discard changes in open papers first."
        elif self.inbox_edit.text().strip() != self._saved_inbox:
            message = "Save the other changes in this dialog first."
        if message:
            self._close_confirm()
            self._say(message, bad=True)
        return bool(message)

    def _browse(self, field, title):
        self._close_confirm()
        path = QFileDialog.getExistingDirectory(self, title, field.text().strip() or str(self.host.workspace.data_dir))
        if path:
            field.setText(path)
            self._say("Folder selected. Save to keep it." if field is self.inbox_edit else
                      'Folder selected. Choose "Use this folder" to continue.')

    def _save_inbox(self, *, clear=False):
        if not self.host.workspace:
            return
        value = "" if clear else self.inbox_edit.text().strip()
        if not clear and value == self._saved_inbox:
            return self._say("Nothing to save.")
        try:
            updated = self.host.workspace.update_settings({"inbox": value or None})
        except (ValueError, OSError) as error:
            return self._say(str(error), bad=True)
        self._saved_inbox = updated.inbox
        self.inbox_edit.setText(self._saved_inbox)
        self._say("Cleared." if clear else "Saved.")

    def _inspect_library(self):
        if not self.host.workspace or self.library_edit.isReadOnly():
            return
        self._say("")
        if self._unsaved_changes():
            return
        self._plan = self.host.workspace.inspect_library(self.library_edit.text())
        actionable = self._plan["action"] in ("open", "move")
        self.confirm_label.setText(self._plan["message"])
        self.confirm_yes.setVisible(actionable)
        self.confirm_yes.setText("Move library" if self._plan["action"] == "move" else "Open library")
        self.confirm_no.setText("Cancel" if actionable else "OK")
        self.confirm_box.show()
        (self.confirm_yes if actionable else self.confirm_no).setFocus()

    def _confirm_switch(self):
        if not self._plan or not self.host.workspace:
            return
        # Check unsaved work before prepare_switch, which persists the new library choice immediately.
        if self._unsaved_changes():
            return
        if (opener := getattr(self.host, 'file_opener', None)) and opener.busy:
            self._close_confirm()
            return self._say("PDFs are still opening. Wait for opening to finish before switching libraries.", bad=True)
        try:
            target = self.host.workspace.prepare_switch(self.library_edit.text(), self._plan["action"])
        except (WorkspaceConflict, ValueError, OSError) as error:
            self._close_confirm()
            return self._say(str(error), bad=True)
        self.host.switch_target = target
        self.accept()
        self.host.close()

    def apply_zoom(self, value):
        zoom = parse_zoom(value)
        self.zoom_value = zoom
        with QSignalBlocker(self.size_combo):
            self.size_combo.setCurrentIndex(ZOOM_CHOICES.index(zoom))
        self.host.setStyleSheet(window_style(self.host.styleSheet(), self.base_font, zoom))

    def _on_size_changed(self, _index):
        value = parse_zoom(self.size_combo.currentData())
        self.apply_zoom(value)
        try:
            settings.update_preferences({"ui_zoom": value})
            self._say("")
        except (OSError, ValueError) as error:
            self._say(f"Could not save interface size: {error}", bad=True)

    def open_preferences(self, focus="zoom"):
        if not self.isVisible():
            self._return_focus = self.host.focusWidget()
        stored = parse_zoom(settings.preferences().get("ui_zoom"))
        if stored != self.zoom_value:
            self.apply_zoom(stored)
        self._refresh_workspace()
        self._say("")
        self.open()
        self.activateWindow()
        key = focus if isinstance(focus, str) else "zoom"
        target = {"inbox": self.inbox_edit, "library": self.library_edit}.get(key, self.size_combo)
        (target if target.isEnabled() and target.isVisibleTo(self) else self.size_combo).setFocus()


def install(window):
    if existing := getattr(window, "preferences_dialog", None):
        return existing
    dialog = PreferencesDialog(window)
    window.preferences_dialog = dialog
    window.show_preferences = dialog.open_preferences
    dialog.apply_zoom(settings.preferences().get("ui_zoom"))
    return dialog
