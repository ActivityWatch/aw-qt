import sys
import logging
import threading
import signal
import webbrowser

from PyQt5 import QtCore, QtGui
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox, QMenu, QWidget
from PyQt5.QtGui import QIcon

from . import resources
from . import manager

cwd = "/home/erb/Programming/activitywatch/aw-traygui"

logging.basicConfig()


def open_webui():
    print("Opening dashboard")
    webbrowser.open("http://localhost:27170/")


def open_apibrowser():
    print("Opening api browser")
    webbrowser.open("http://localhost:5600/")


def _build_modulemenu(menu):
    menu.clear()

    running_modules = filter(lambda m: m.is_running(), manager.modules)
    stopped_modules = filter(lambda m: not m.is_running(), manager.modules)

    runningLabel = menu.addAction("Running")
    runningLabel.setEnabled(False)

    for m_running in running_modules:
        menu.addAction(m_running.name, m_running.stop)

    menu.addSeparator()

    stoppedLabel = menu.addAction("Stopped")
    stoppedLabel.setEnabled(False)

    for m_stopped in stopped_modules:
        menu.addAction(m_stopped.name, m_stopped.start)


class SystemTrayIcon(QSystemTrayIcon):
    def __init__(self, icon, parent=None):
        QSystemTrayIcon.__init__(self, icon, parent)
        menu = QMenu(parent)

        self.setToolTip("This is a tooltip\nThis is a second line")

        # openWebUIIcon = QIcon.fromTheme("open", QIcon(cwd + "/img/sagan-smiling.jpg"))
        openWebUIIcon = QIcon(":/sagan-sympathetic.png")
        menu.addAction(openWebUIIcon, "Open Dashboard", open_webui)

        openAPIBrowserIcon = QIcon(":/sagan-sympathetic.png")
        menu.addAction(openAPIBrowserIcon, "Open API Browser", open_apibrowser)

        menu.addSeparator()

        modulesMenu = menu.addMenu("Modules")
        _build_modulemenu(modulesMenu)

        menu.addSeparator()

        exitIcon = QIcon.fromTheme("application-exit", QIcon("media/application_exit.png"))
        menu.addAction(exitIcon, "Quit ActivityWatch", exit_dialog)

        self.setContextMenu(menu)

        def rebuild_modules_menu():
            _build_modulemenu(modulesMenu)
            # TODO: Do it in a better way, singleShot isn't pretty...
            QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

        QtCore.QTimer.singleShot(2000, rebuild_modules_menu)


def exit_dialog():
    # TODO: Do cleanup actions
    # TODO: Save state for resume
    options = QMessageBox.Yes | QMessageBox.No
    default = QMessageBox.No
    answer = QMessageBox.question(None, '', "Are you sure you want to quit?", options, default)

    if answer == QMessageBox.Yes:
        exit()


def exit(*args):
    # TODO: Stop all services
    QApplication.quit()


def run():
    logging.info("Creating trayicon...")
    # print(QIcon.themeSearchPaths())

    app = QApplication(sys.argv)

    # Without this, Ctrl+C will have no effect
    signal.signal(signal.SIGINT, exit)
    timer = QtCore.QTimer()
    timer.start(100)  # You may change this if you wish.
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Systray", "I couldn't detect any system tray on this system.")
        sys.exit(1)

    widget = QWidget()

    trayIcon = SystemTrayIcon(QIcon(":/sagan-sympathetic.png"), widget)
    trayIcon.show()

    QApplication.setQuitOnLastWindowClosed(False)

    # Run the application, blocks until quit
    exit_message = app.exec_()

    # Exit
    sys.exit(exit_message)


if __name__ == "__main__":
    run()
