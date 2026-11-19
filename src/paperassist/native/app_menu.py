"""Brand button and application menu at the start of the Main toolbar."""
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QToolButton

from .shortcuts import show_help
from .sources_dialog import show_sources
from .theme import brand_icon


class AppMenu(QMenu):
    def __init__(self, button, host):
        super().__init__("PaperAssist", host, objectName="app-menu")
        self.button = button

    def _items(self):
        return [action for action in self.actions() if not action.isSeparator() and action.isEnabled()]

    def keyPressEvent(self, event):
        # QMenu handles Up, Down, Enter and Escape; add Home, End and Tab-dismiss.
        items = self._items()
        if event.key() == Qt.Key_Home and items:
            return self.setActiveAction(items[0])
        if event.key() == Qt.Key_End and items:
            return self.setActiveAction(items[-1])
        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            self.close()
            self.button.setFocus()
            self.button.focusNextPrevChild(event.key() == Qt.Key_Tab)
            return
        super().keyPressEvent(event)


class BrandButton(QToolButton):
    def __init__(self, host):
        super().__init__(host, objectName="brand-button", text="PaperAssist", focusPolicy=Qt.TabFocus)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIcon(brand_icon(20))
        self.setIconSize(QSize(20, 20))
        self._menu = AppMenu(self, host)
        self.clicked.connect(self.open_menu)

        self.preferences_action = QAction("Preferences…", host)
        self.preferences_action.setShortcut(QKeySequence("Ctrl+,"))
        self.preferences_action.setShortcutVisibleInContextMenu(True)
        self.preferences_action.setMenuRole(QAction.PreferencesRole)
        self.preferences_action.triggered.connect(lambda _=False: host.show_preferences())
        # Attaching the action to the window keeps Ctrl+, active while the popup menu is closed.
        host.addAction(self.preferences_action)
        self._menu.addAction(self.preferences_action)

        self.sources_action = QAction("Sources…", host)
        self.sources_action.triggered.connect(lambda _=False: show_sources(host))
        self._menu.addAction(self.sources_action)

        self.shortcuts_action = QAction("Keyboard shortcuts", host)
        self.shortcuts_action.triggered.connect(lambda _=False: show_help(host))
        self._menu.addAction(self.shortcuts_action)

        self._menu.addSeparator()

        self.about_action = QAction("About PaperAssist", host)
        self.about_action.setMenuRole(QAction.AboutRole)
        self.about_action.triggered.connect(lambda _=False: host.show_about())
        self._menu.addAction(self.about_action)

    def menu(self):
        return self._menu

    def keyPressEvent(self, event):
        # QToolButton clicks on Space; also open on Down and Enter like a standard menu button.
        if event.key() in (Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter):
            return self.open_menu()
        super().keyPressEvent(event)

    def open_menu(self):
        if items := self._menu._items():
            self._menu.setActiveAction(items[0])
        # popup() uses the main event loop instead of blocking tests and jobs inside exec().
        self._menu.popup(self.mapToGlobal(QPoint(0, self.height())))
