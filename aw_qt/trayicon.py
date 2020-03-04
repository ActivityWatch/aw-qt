import sys
import logging
import signal
import webbrowser
import os
import subprocess
from collections import defaultdict
from typing import Any, DefaultDict, List, Optional

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox, QMenu, QWidget, QPushButton
from PyQt5.QtGui import QIcon

import aw_core

from .manager import Manager, Module

logger = logging.getLogger(__name__)


def open_webui(root_url: str) -> None:
    print("Opening dashboard")
    webbrowser.open(root_url)


def open_apibrowser(root_url: str) -> None:
    print("Opening api browser")
    webbrowser.open(root_url + "/api")


def open_dir(d: str)-> None:
    """From: http://stackoverflow.com/a/1795849/965332"""
    if sys.platform == 'win32':
        os.startfile(d)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', d])
    else:
        subprocess.Popen(['xdg-open', d])


class TrayIcon(QSystemTrayIcon):
    def __init__(self, manager: Manager, icon: QIcon, parent: Optional[QWidget]=None, testing: bool=False) -> None:
        QSystemTrayIcon.__init__(self, icon, parent)
        self._parent = parent # QSystemTrayIcon also tries to save parent info but it screws up the type info
        self.setToolTip("ActivityWatch" + (" (testing)" if testing else ""))

        self.manager = manager
        self.testing = testing

        self._build_rootmenu()

    def _build_rootmenu(self) -> None:
        menu = QMenu(self._parent)

        root_url = "http://localhost:{port}".format(port=5666 if self.testing else 5600)

        if self.testing:
            menu.addAction("Running in testing mode")  # .setEnabled(False)
            menu.addSeparator()

        # openWebUIIcon = QIcon.fromTheme("open")
        menu.addAction("Open Dashboard", lambda: open_webui(root_url))
        menu.addAction("Open API Browser", lambda: open_apibrowser(root_url))

        menu.addSeparator()

        modulesMenu = menu.addMenu("Modules")
        self._build_modulemenu(modulesMenu)

        menu.addSeparator()
        menu.addAction("Open log folder", lambda: open_dir(aw_core.dirs.get_log_dir(None)))
        menu.addSeparator()

        exitIcon = QIcon.fromTheme("application-exit", QIcon("media/application_exit.png"))
        # This check is an attempted solution to: https://github.com/ActivityWatch/activitywatch/issues/62
        # Seems to be in agreement with: https://github.com/OtterBrowser/otter-browser/issues/1313
        #   "it seems that the bug is also triggered when creating a QIcon with an invalid path"
        if exitIcon.availableSizes():
            menu.addAction(exitIcon, "Quit ActivityWatch", lambda: exit(self.manager))
        else:
            menu.addAction("Quit ActivityWatch", lambda: exit(self.manager))

        self.setContextMenu(menu)

        def show_module_failed_dialog(module: Module) -> None:
            box = QMessageBox(self._parent)
            box.setIcon(QMessageBox.Warning)
            box.setText("Module {} quit unexpectedly".format(module.name))
            box.setDetailedText(module.read_log())

            restart_button = QPushButton("Restart", box)
            restart_button.clicked.connect(module.start)
            box.addButton(restart_button, QMessageBox.AcceptRole)
            box.setStandardButtons(QMessageBox.Cancel)

            box.show()

        def rebuild_modules_menu() -> None:
            for action in modulesMenu.actions():
                if action.isEnabled():
                    name = action.data().name
                    alive = self.manager.modules[name].is_alive()
                    action.setChecked(alive)
                    # print(module.text(), alive)

            # TODO: Do it in a better way, singleShot isn't pretty...
            QtCore.QTimer.singleShot(2000, rebuild_modules_menu)
        QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

        def check_module_status() -> None:
            unexpected_exits = self.manager.get_unexpected_stops()
            if unexpected_exits:
                for module in unexpected_exits:
                    show_module_failed_dialog(module)
                    module.stop()

            # TODO: Do it in a better way, singleShot isn't pretty...
            QtCore.QTimer.singleShot(2000, rebuild_modules_menu)
        QtCore.QTimer.singleShot(2000, check_module_status)

    def _build_modulemenu(self, moduleMenu: QMenu) -> None:
        moduleMenu.clear()

        def add_module_menuitem(module: Module) -> None:
            title = module.name
            ac = moduleMenu.addAction(title, lambda: module.toggle())

            ac.setData(module)
            ac.setCheckable(True)
            ac.setChecked(module.is_alive())

        # Merged from branch dev/autodetect-modules, still kind of in progress with making this actually work
        modules_by_location: DefaultDict[str, List[Module]] = defaultdict(lambda: list())
        for module in sorted(self.manager.modules.values(), key=lambda m: m.name):
            modules_by_location[module.location].append(module)

        for location, modules in sorted(modules_by_location.items(), key=lambda kv: kv[0]):
            header = moduleMenu.addAction(location)
            header.setEnabled(False)

        for module in sorted(modules, key=lambda m: m.name):
            add_module_menuitem(self.manager.modules[module.name])


def exit(manager: Manager) -> None:
    # TODO: Do cleanup actions
    # TODO: Save state for resume
    print("Shutdown initiated, stopping all services...")
    manager.stop_all()
    # Terminate entire process group, just in case.
    # os.killpg(0, signal.SIGINT)

    QApplication.quit()


def run(manager: Manager, testing: bool = False) -> Any:
    logger.info("Creating trayicon...")
    # print(QIcon.themeSearchPaths())

    app = QApplication(sys.argv)

    # FIXME: remove ignores after https://github.com/python/mypy/issues/2955 has been fixed
    # Without this, Ctrl+C will have no effect
    signal.signal(signal.SIGINT, lambda: exit(manager)) #type: ignore
    # Ensure cleanup happens on SIGTERM
    signal.signal(signal.SIGTERM, lambda: exit(manager)) #type: ignore

    timer = QtCore.QTimer()
    timer.start(100)  # You may change this if you wish.
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Systray", "I couldn't detect any system tray on this system. Either get one or run the ActivityWatch modules from the console.")
        sys.exit(1)

    widget = QWidget()
    if sys.platform == "darwin":
        from Foundation import NSUserDefaults
        style = NSUserDefaults.standardUserDefaults().stringForKey_('AppleInterfaceStyle')
        if style == "Dark":
            icon = QIcon(":/white-monochrome-logo.png")
        else:
            icon = QIcon(":/black-monochrome-logo.png")
    else:
        icon = QIcon(":/logo.png")

    trayIcon = TrayIcon(manager, icon, widget, testing=testing)
    trayIcon.show()

    QApplication.setQuitOnLastWindowClosed(False)

    logger.info("Initialized aw-qt and trayicon succesfully")
    # Run the application, blocks until quit
    return app.exec_()
