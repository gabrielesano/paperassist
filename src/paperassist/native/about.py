"""About dialog: version, license, active library path, and installed runtime components."""
from importlib import metadata

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

import paperassist

# PySide6 ships as PySide6-Essentials in minimal native installs and as PySide6 in full ones.
COMPONENTS = (
    ("Qt for Python (PySide6)", ("PySide6", "PySide6-Essentials")),
    ("PDFium through pypdfium2", ("pypdfium2",)),
    ("pypdf", ("pypdf",)),
)


def component_info(distributions):
    """Read version and license from installed package metadata, supporting PEP 639 License-Expression."""
    for name in distributions:
        try:
            meta = metadata.metadata(name)
            break
        except metadata.PackageNotFoundError:
            continue
    else:
        return "not installed", "unknown license"
    lic = (meta.get("License-Expression") or meta.get("License") or "").strip()
    if not lic or lic.upper() == "UNKNOWN":
        classifiers = [
            item.rsplit("::", 1)[-1].strip()
            for item in (meta.get_all("Classifier") or ())
            if item.startswith("License ::")
        ]
        lic = ", ".join(classifiers) or "unknown license"
    return meta["Version"], lic.splitlines()[0].strip()


class AboutDialog(QDialog):
    def __init__(self, host):
        super().__init__(host, objectName="about-dialog", windowTitle="About PaperAssist")
        self.host = host
        self.setMinimumWidth(420)

        self.summary_label = self._selectable(
            f"PaperAssist {paperassist.__version__}: a lightweight local reader and shared library "
            "for research papers."
        )
        self.license_label = self._selectable("License: GPL-3.0-or-later", muted=True)
        self.library_label = self._selectable("", muted=True)
        self.components_label = self._selectable("", muted=True)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        self._close_button = buttons.button(QDialogButtonBox.Close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(8)
        for widget in (QLabel("<b>About PaperAssist</b>"), self.summary_label,
                       self.license_label, self.library_label, self.components_label, buttons):
            layout.addWidget(widget)

        self.finished.connect(lambda _: host.activateWindow())
        # Package metadata is read on open: reading it costs a noticeable part of window startup.

    def _selectable(self, text, muted=False):
        label = QLabel(text, wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse)
        if muted:
            label.setObjectName("muted-note")
        return label

    def refresh(self):
        library = str(self.host.workspace.data_dir) if self.host.workspace is not None else "No library open"
        self.library_label.setText(f"Library: {library}")
        lines = ["{} {} ({})".format(title, *component_info(names)) for title, names in COMPONENTS]
        self.components_label.setText("Components:\n" + "\n".join(lines))

    def text_content(self):
        return "\n".join(label.text() for label in (
            self.summary_label, self.license_label, self.library_label, self.components_label))

    def open_about(self):
        self.refresh()
        self.adjustSize()
        self.open()
        self.activateWindow()
        self._close_button.setFocus()


def install(window):
    if existing := getattr(window, "about_dialog", None):
        return existing
    dialog = AboutDialog(window)
    window.about_dialog = dialog
    window.show_about = dialog.open_about
    return dialog
