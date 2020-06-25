import sys
import logging
import signal
import os
import subprocess
import webbrowser

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox, QMenu, QWidget, QPushButton
from PyQt5.QtGui import QIcon

import aw_core

from .manager import Manager

logger = logging.getLogger(__name__)


def get_env():
    """
    Necessary for xdg-open to work properly when PyInstaller overrides LD_LIBRARY_PATH

    https://github.com/ActivityWatch/activitywatch/issues/208#issuecomment-417346407
    """
    env = dict(os.environ)  # make a copy of the environment
    lp_key = 'LD_LIBRARY_PATH'  # for GNU/Linux and *BSD.
    lp_orig = env.get(lp_key + '_ORIG')
    if lp_orig is not None:
        env[lp_key] = lp_orig  # restore the original, unmodified value
    else:
        # This happens when LD_LIBRARY_PATH was not set.
        # Remove the env var as a last resort:
        env.pop(lp_key, None)
    return env


def open_url(url):
    if sys.platform == "linux":
        env = get_env()
        subprocess.Popen(["xdg-open", url], env=env)
    else:
        webbrowser.open(url)


def open_webui(root_url):
    print("Opening dashboard")
    open_url(root_url)


def open_apibrowser(root_url):
    print("Opening api browser")
    open_url(root_url + "/api")


def open_dir(d):
    """From: http://stackoverflow.com/a/1795849/965332"""
    if sys.platform == 'win32':
        os.startfile(d)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', d])
    else:
        subprocess.Popen(['xdg-open', d])


class TrayIcon(QSystemTrayIcon):
    def __init__(self, manager: Manager, icon, parent=None, testing=False) -> None:
        QSystemTrayIcon.__init__(self, icon, parent)
        self.setToolTip("ActivityWatch" + (" (testing)" if testing else ""))

        self.manager = manager
        self.testing = testing

        self._build_rootmenu()

    def _build_rootmenu(self):
        menu = QMenu(self.parent())

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

        def show_module_failed_dialog(module):
            box = QMessageBox(self.parent())
            box.setIcon(QMessageBox.Warning)
            box.setText("Module {} quit unexpectedly".format(module.name))
            box.setDetailedText(module.read_log())

            restart_button = QPushButton("Restart", box)
            restart_button.clicked.connect(module.start)
            box.addButton(restart_button, QMessageBox.AcceptRole)
            box.setStandardButtons(QMessageBox.Cancel)

            box.show()

        def rebuild_modules_menu():
            for module in modulesMenu.actions():
                name = module.text()
                alive = self.manager.modules[name].is_alive()
                module.setChecked(alive)
                # print(module.text(), alive)

            unexpected_exits = self.manager.get_unexpected_stops()
            if unexpected_exits:
                for module in unexpected_exits:
                    show_module_failed_dialog(module)
                    module.stop()

            # TODO: Do it in a better way, singleShot isn't pretty...
            QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

        QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

    def _build_modulemenu(self, moduleMenu):
        moduleMenu.clear()

        def add_module_menuitem(module):
            ac = moduleMenu.addAction(module.name, lambda: module.toggle())
            ac.setCheckable(True)
            ac.setChecked(module.is_alive())

        add_module_menuitem(self.manager.modules["aw-server"])

        for module_name in sorted(self.manager.modules.keys()):
            if module_name != "aw-server":
                add_module_menuitem(self.manager.modules[module_name])


def exit(manager: Manager):
    # TODO: Do cleanup actions
    # TODO: Save state for resume
    print("Shutdown initiated, stopping all services...")
    manager.stop_all()
    # Terminate entire process group, just in case.
    # os.killpg(0, signal.SIGINT)

    QApplication.quit()


def run(manager, testing=False):
    logger.info("Creating trayicon...")
    # print(QIcon.themeSearchPaths())

    app = QApplication(sys.argv)

    # Without this, Ctrl+C will have no effect
    signal.signal(signal.SIGINT, lambda *args: exit(manager))
    # Ensure cleanup happens on SIGTERM
    signal.signal(signal.SIGTERM, lambda *args: exit(manager))

    timer = QtCore.QTimer()
    timer.start(100)  # You may change this if you wish.
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Systray", "I couldn't detect any system tray on this system. Either get one or run the ActivityWatch modules from the console.")
        sys.exit(1)

    widget = QWidget()
    if sys.platform == "darwin":
        icon = QIcon(":/black-monochrome-logo.png")
        # Allow macOS to use filters for changing the icon's color
        icon.setIsMask(True)
    else:
        icon = QIcon(":/logo.png")

    trayIcon = TrayIcon(manager, icon, widget, testing=testing)
    trayIcon.show()

    QApplication.setQuitOnLastWindowClosed(False)

    # Run the application, blocks until quit
    return app.exec_()
