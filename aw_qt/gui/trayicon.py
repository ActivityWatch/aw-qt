import sys
import logging
import signal
import webbrowser
from functools import partial

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox, QMenu, QWidget
from PyQt5.QtGui import QIcon

from .. import resources
from .. import manager


def open_webui():
    print("Opening dashboard")
    webbrowser.open("http://localhost:5600/")


def open_apibrowser():
    print("Opening api browser")
    webbrowser.open("http://localhost:5600/api/")


def _build_modulemenu(menu, testing):
    menu.clear()

    for module in manager.modules.values():
        alive = module.is_alive()
        ac = menu.addAction(module.name, module.stop if alive else partial(module.start, testing=testing))
        ac.setCheckable(True)
        ac.setChecked(alive)
        menu.addAction("Show log", module.show_log)
        menu.addSeparator()


class TrayIcon(QSystemTrayIcon):
    def __init__(self, icon, parent=None, testing=False):
        QSystemTrayIcon.__init__(self, icon, parent)
        menu = QMenu(parent)
        # sagan_icon = QIcon(":/sagan-sympathetic.png")

        self.setToolTip("ActivityWatch" + (" (testing)" if testing else ""))

        # openWebUIIcon = QIcon.fromTheme("open")
        menu.addAction("Open Dashboard", open_webui)
        menu.addAction("Open API Browser", open_apibrowser)

        menu.addSeparator()

        modulesMenu = menu.addMenu("Modules")
        _build_modulemenu(modulesMenu, testing)

        menu.addSeparator()

        exitIcon = QIcon.fromTheme("application-exit", QIcon("media/application_exit.png"))
        menu.addAction(exitIcon, "Quit ActivityWatch", exit_dialog)

        self.setContextMenu(menu)

        def rebuild_modules_menu():
            _build_modulemenu(modulesMenu, testing)
            unexpected_exits = manager.get_unexpected_stops()
            if unexpected_exits:
                for module in unexpected_exits:
                    msg = """
Module {} quit unexpectedly
Output:
{}
                    """.format(module.name, module.stderr())
                    QMessageBox.warning(None, "ActivityWatch", msg)
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
    QApplication.quit()


def run(testing=False):
    logging.info("Creating trayicon...")
    # print(QIcon.themeSearchPaths())

    app = QApplication(sys.argv)

    # Without this, Ctrl+C will have no effect
    signal.signal(signal.SIGINT, exit)
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
