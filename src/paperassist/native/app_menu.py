"""Brand button and application menu at the start of the Main toolbar."""
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QToolBar, QToolButton

from .about import install as install_about
from .preferences_dialog import install as install_preferences
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
        # QMenu handles Up, Down, Enter and Escape; add Home, End and Tab-dismiss from the web menu.
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


def install(window):
    install_preferences(window)
    install_about(window)
    if existing := getattr(window, "app_menu_button", None):
        return existing
    bar = next(toolbar for toolbar in window.findChildren(QToolBar) if toolbar.windowTitle() == "Main")
    button = BrandButton(window)
    menu = button.menu()

    preferences_action = QAction("Preferences…", window)
    preferences_action.setShortcut(QKeySequence("Ctrl+,"))
    preferences_action.setShortcutVisibleInContextMenu(True)
    preferences_action.setMenuRole(QAction.PreferencesRole)
    preferences_action.triggered.connect(lambda _=False: window.show_preferences())
    # Attaching the action to the window keeps Ctrl+, active while the popup menu is closed.
    window.addAction(preferences_action)
    menu.addAction(preferences_action)

    sources_action = QAction("Sources…", window)
    sources_action.triggered.connect(lambda _=False: show_sources(window))
    menu.addAction(sources_action)

    shortcuts_action = QAction("Keyboard shortcuts", window)
    shortcuts_action.triggered.connect(lambda _=False: show_help(window))
    menu.addAction(shortcuts_action)

    menu.addSeparator()

    about_action = QAction("About PaperAssist", window)
    about_action.setMenuRole(QAction.AboutRole)
    about_action.triggered.connect(lambda _=False: window.show_about())
    menu.addAction(about_action)

    first = bar.actions()[0] if bar.actions() else None
    if first is not None:
        bar.insertWidget(first, button)
        bar.insertSeparator(first)
    else:
        bar.addWidget(button)

    window.app_menu_button = button
    window.app_menu = menu
    window.preferences_action = preferences_action
    window.sources_action = sources_action
    window.shortcuts_action = shortcuts_action
    window.about_action = about_action
    return button
