"""The Sources dialog: which services PaperAssist may ask and the access data they need.

Keys are write-only: the library settings return only whether a key is set and its last
four characters. Changes apply on an explicit Save; an access check uses what was saved.
"""
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

# `key` names what a source cannot run without ("token", "key"), or "optional" when a key only raises limits.
SOURCES = [
    ("crossref", "Crossref", None, "Open metadata and publisher links. The contact email puts requests in its polite pool.", None),
    ("arxiv", "arXiv", None, "Preprints. No setup needed.", None),
    ("openalex", "OpenAlex", "optional", "An optional key raises the rate limit.", None),
    ("semantic_scholar", "Semantic Scholar", "optional", "An optional key raises the rate limit.",
     ("https://www.semanticscholar.org/product/api", "Request a key")),
    ("ads", "NASA ADS", "token", "Astrophysics and physics records. Needs a free token.",
     ("https://ui.adsabs.harvard.edu/user/settings/token", "Get a token")),
    ("core", "CORE", "key", "Open-access repository copies. Needs a free key.", ("https://core.ac.uk/services/api", "Get a key")),
    ("unpaywall", "Unpaywall", None, "Open-access copies found by DOI. Needs the contact email above.", None),
]
CHECKS = set()  # a check outlives a closed dialog until its requests have returned


def status_text(source, view, configured, enabled):
    """The one-line state of a source, as the web dialog words it."""
    id_, _, need, _, _ = source
    key = view["keys"].get(id_, {})
    if not enabled:
        return "Turned off"
    if need == "optional":
        return f"Optional key set …{key['hint']}" if key.get("configured") else "Ready"
    if need:
        return f"Ready · {need} set …{key['hint']}" if key["configured"] else f"Needs a {need}"
    return "Ready" if configured else "Needs an email"


class CheckThread(QThread):
    """Runs the access check off the UI thread: it makes one network request per source."""
    done = Signal(object, str)  # results by source, or an error text

    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        CHECKS.add(self)
        self.finished.connect(lambda: CHECKS.discard(self))
        self.finished.connect(self.deleteLater)

    def run(self):
        try:
            self.done.emit(self.workspace.check_settings(), "")
        except Exception as error:
            self.done.emit({}, str(error) or type(error).__name__)


class SourceRow(QFrame):
    """One source: a switch, its state, an optional key field, help and the last check result."""

    def __init__(self, source, view, configured):
        super().__init__()
        self.source, self.view, self.configured = source, view, configured
        id_, name, need, help_text, link = source
        noun = need if need in ("token", "key") else "key"
        self.removing = False
        self.toggle = QCheckBox(name)
        self.toggle.setChecked(view["enabled"][id_])
        self.toggle.setAccessibleName(f"Use {name}")
        self.status = QLabel()
        self.result = QLabel(wordWrap=True)
        self.result.setAccessibleName(f"{name} access check")
        self.key_field = self.remove = None
        head = QHBoxLayout()
        head.addWidget(self.toggle, 1)
        head.addWidget(self.status)
        column = QVBoxLayout(self)
        column.addLayout(head)
        if need:
            stored = view["keys"][id_]["configured"]
            self.key_field = QLineEdit(echoMode=QLineEdit.Password, clearButtonEnabled=False)
            self.key_field.setPlaceholderText(f"Paste a new {noun} to replace it" if stored else f"Paste your {noun}")
            self.key_field.setAccessibleName(f"{name} {noun}")
            keys = QHBoxLayout()
            keys.addWidget(self.key_field, 1)
            if stored:
                # Removal is staged like every other change and applied by Save.
                self.remove = QPushButton("Remove")
                self.remove.setAccessibleName(f"Remove the {name} {noun}")
                self.remove.clicked.connect(self._toggle_removal)
                keys.addWidget(self.remove)
            column.addLayout(keys)
        text = help_text + (f' <a href="{link[0]}">{link[1]}</a>' if link else "")
        help_label = QLabel(text, wordWrap=True, openExternalLinks=True, textInteractionFlags=Qt.TextBrowserInteraction)
        help_label.setStyleSheet("color: #666")
        column.addWidget(help_label)
        column.addWidget(self.result)
        self.result.hide()
        self.toggle.toggled.connect(self._show_status)
        self._show_status()

    def _show_status(self):
        need = self.source[2]
        noun = need if need in ("token", "key") else "key"
        self.status.setText(f"The {noun} will be removed when you save" if self.removing
                            else status_text(self.source, self.view, self.configured, self.toggle.isChecked()))

    def _toggle_removal(self):
        self.removing = not self.removing
        self.key_field.setEnabled(not self.removing)
        self.key_field.clear()
        self.remove.setText("Keep" if self.removing else "Remove")
        self._show_status()

    def changes(self, found):
        id_ = self.source[0]
        if self.toggle.isChecked() != self.view["enabled"][id_]:
            found.setdefault("enabled", {})[id_] = self.toggle.isChecked()
        typed = self.key_field.text().strip() if self.key_field else ""
        if self.removing:
            found.setdefault("keys", {})[id_] = None
        elif typed:
            found.setdefault("keys", {})[id_] = typed

    def show_result(self, outcome):
        self.result.setVisible(bool(outcome))
        if outcome:
            self.result.setText(f"{'Accepted' if outcome['ok'] else 'Problem'}: {outcome['message']}")
            self.result.setStyleSheet("" if outcome["ok"] else "color: #b3261e")


class SourcesDialog(QDialog):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace, self.view, self.rows, self.check_thread = workspace, None, [], None
        self.setWindowTitle("Sources")
        self.resize(640, 720)
        self.email = QLineEdit(placeholderText="name@example.org")
        self.email.setAccessibleName("Contact email")
        self.proxy = QLineEdit(placeholderText="https://login.proxy.example.edu/login?url=")
        self.proxy.setAccessibleName("Library proxy")
        form = QFormLayout()
        form.addRow("Contact &email", self.email)
        form.addRow("Library &proxy", self.proxy)
        self.list = QVBoxLayout()
        self.list.addStretch(1)
        host = QWidget()
        host.setLayout(self.list)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(host)
        scroll.setAccessibleName("Sources")
        self.save_button = QPushButton("Save")
        self.save_button.setDefault(True)
        self.check_button = QPushButton("Check access")
        self.message = QLabel(wordWrap=True)
        self.message.setAccessibleName("Status")
        close = QPushButton("Close")
        buttons = QHBoxLayout()
        for widget in (self.save_button, self.check_button, close):
            buttons.addWidget(widget)
        buttons.addWidget(self.message, 1)
        column = QVBoxLayout(self)
        column.addLayout(form)
        column.addWidget(scroll, 1)
        column.addLayout(buttons)
        self.save_button.clicked.connect(self.save)
        self.check_button.clicked.connect(self.check)
        close.clicked.connect(self.close)
        self.load()
        self.email.setFocus()

    def say(self, text, bad=False):
        self.message.setText(text)
        self.message.setStyleSheet("color: #b3261e" if bad else "")

    def load(self):
        """Show the saved settings; typed keys and staged removals are dropped."""
        settings = self.workspace.get_settings()
        self.view, providers = settings.view(), settings.providers()
        self.email.setText(self.view["email"])
        self.proxy.setText(self.view["proxy"])
        for row in self.rows:
            row.hide()  # leaves the layout at once; deletion itself waits for the event loop
            row.deleteLater()
        self.rows = [SourceRow(source, self.view, providers[source[0]]["configured"]) for source in SOURCES]
        for row in self.rows:
            self.list.insertWidget(self.list.count() - 1, row)  # the last item is the stretch

    def changes(self):
        """Only what differs from the saved settings; settings treat an omitted field as unchanged."""
        found = {}
        for name, field in (("email", self.email), ("proxy", self.proxy)):
            if field.text().strip() != self.view[name]:
                found[name] = field.text().strip() or None
        for row in self.rows:
            row.changes(found)
        return found

    def save(self):
        pending = self.changes()
        if not pending:
            return self.say("Nothing to save.")
        try:
            self.workspace.update_settings(pending)
        except ValueError as error:
            return self.say(str(error), True)  # the typed values stay for correction
        self.load()
        self.say("Saved.")

    def check(self):
        if self.changes():
            return self.say("Save your changes first; the check uses saved settings.", True)
        self.check_button.setEnabled(False)
        self.say("Checking access…")
        self.check_thread = CheckThread(self.workspace)
        self.check_thread.done.connect(self._checked)
        self.check_thread.start()

    def _checked(self, results, error):
        self.check_button.setEnabled(True)
        if error:
            return self.say(error, True)
        for row in self.rows:
            row.show_result(results.get(row.source[0]))
        self.say("" if results else "No source with access data to check yet.")


def show_sources(window):
    """Open the dialog for the window's library; loose files have no library to hold settings."""
    if window.workspace is None:
        box = QMessageBox(QMessageBox.Information, "Sources", "Sources belong to a library.",
                          QMessageBox.Ok, window)
        box.setInformativeText("Start PaperAssist with a library, without file arguments, to set up sources.")
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.open()
        return box
    dialog = SourcesDialog(window.workspace, window)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.open()
    return dialog
