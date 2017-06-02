import sys
import logging
import signal
import webbrowser
import os
import subprocess

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox, QMenu, QWidget
from PyQt5.QtGui import QIcon

import aw_core

from . import manager

logger = logging.getLogger(__name__)


def open_webui(root_url):
    print("Opening dashboard")
    webbrowser.open(root_url)


def open_apibrowser(root_url):
    print("Opening api browser")
    webbrowser.open(root_url + "/api")


def _build_modulemenu(menu, testing):
    menu.clear()

    def add_module_menuitem(module):
        ac = menu.addAction(module.name, lambda: module.toggle(testing))
        ac.setCheckable(True)
        ac.setChecked(module.is_alive())

    add_module_menuitem(manager.modules["aw-server"])

    for module_name in sorted(manager.modules.keys()):
        if module_name != "aw-server":
            add_module_menuitem(manager.modules[module_name])


def open_dir(d):
    """From: http://stackoverflow.com/a/1795849/965332"""
    if sys.platform == 'win32':
        os.startfile(d)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', d])
    else:
        subprocess.Popen(['xdg-open', d])


class TrayIcon(QSystemTrayIcon):
    def __init__(self, icon, parent=None, testing=False):
        QSystemTrayIcon.__init__(self, icon, parent)
        menu = QMenu(parent)
        # sagan_icon = QIcon(":/sagan-sympathetic.png")

        self.setToolTip("ActivityWatch" + (" (testing)" if testing else ""))

        root_url = "http://localhost:{port}".format(port=5666 if testing else 5600)

        if testing:
            menu.addAction("Running in testing mode")  # .setEnabled(False)
            menu.addSeparator()

        # openWebUIIcon = QIcon.fromTheme("open")
        menu.addAction("Open Dashboard", lambda: open_webui(root_url))
        menu.addAction("Open API Browser", lambda: open_apibrowser(root_url))

        menu.addSeparator()

        modulesMenu = menu.addMenu("Modules")
        _build_modulemenu(modulesMenu, testing)

        menu.addSeparator()
        menu.addAction("Open log folder", lambda: open_dir(aw_core.dirs.get_log_dir()))
        menu.addSeparator()

        exitIcon = QIcon.fromTheme("application-exit", QIcon("media/application_exit.png"))
        # This check is an attempted solution to: https://github.com/ActivityWatch/activitywatch/issues/62
        # Seems to be in agreement with: https://github.com/OtterBrowser/otter-browser/issues/1313
        #   "it seems that the bug is also triggered when creating a QIcon with an invalid path"
        if exitIcon.availableSizes():
            menu.addAction(exitIcon, "Quit ActivityWatch", exit_dialog)
        else:
            menu.addAction("Quit ActivityWatch", exit_dialog)

        self.setContextMenu(menu)

        def show_module_failed_dialog(module):
            box = QMessageBox(parent)
            box.setIcon(QMessageBox.Warning)
            box.setText("Module {} quit unexpectedly".format(module.name))
            box.setDetailedText(module.stderr())
            box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            box.show()

        def rebuild_modules_menu():
            for module in modulesMenu.actions():
                name = module.text()
                alive = manager.modules[name].is_alive()
                module.setChecked(alive)
                # print(module.text(), alive)

            unexpected_exits = manager.get_unexpected_stops()
            if unexpected_exits:
                for module in unexpected_exits:
                    show_module_failed_dialog(module)
                    module.stop()

            # TODO: Do it in a better way, singleShot isn't pretty...
            QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

        QtCore.QTimer.singleShot(2000, rebuild_modules_menu)


def exit_dialog():
    # TODO: Do cleanup actions
    # TODO: Save state for resume
    options = QMessageBox.Yes | QMessageBox.No
    default = QMessageBox.No
    answer = QMessageBox.question(None, "ActivityWatch", "Are you sure you want to quit?", options, default)

    if answer == QMessageBox.Yes:
        exit()


def exit(*args):
    # TODO: Stop all services
    print("Shutdown initiated, stopping all services...")

    for module in manager.modules.values():
        if module.is_alive():
            module.stop()

    # Terminate entire process group, just in case.
    # os.killpg(0, signal.SIGINT)

    QApplication.quit()


def run(testing=False):
    logger.info("Creating trayicon...")
    # print(QIcon.themeSearchPaths())

    app = QApplication(sys.argv)

    # Without this, Ctrl+C will have no effect
    signal.signal(signal.SIGINT, exit)
    # Ensure cleanup happens on SIGTERM
    signal.signal(signal.SIGTERM, exit)

    timer = QtCore.QTimer()
    timer.start(100)  # You may change this if you wish.
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Systray", "I couldn't detect any system tray on this system. Either get one or run the ActivityWatch modules from the console.")
        sys.exit(1)

    widget = QWidget()

    icon = QIcon(":/logo.png")
    trayIcon = TrayIcon(icon, widget, testing=testing)
    trayIcon.show()

    # trayIcon.showMessage("Title", "message")

    QApplication.setQuitOnLastWindowClosed(False)

    # Run the application, blocks until quit
    return app.exec_()


if __name__ == "__main__":
    run()
