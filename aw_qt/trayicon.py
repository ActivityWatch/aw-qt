import logging
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import aw_core
from PyQt6 import QtCore
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)

from .manager import Manager, Module

logger = logging.getLogger(__name__)


def get_env() -> Dict[str, str]:
    """
    Necessary for xdg-open to work properly when PyInstaller overrides LD_LIBRARY_PATH

    https://github.com/ActivityWatch/activitywatch/issues/208#issuecomment-417346407
    """
    env = dict(os.environ)  # make a copy of the environment
    lp_key = "LD_LIBRARY_PATH"  # for GNU/Linux and *BSD.
    lp_orig = env.get(lp_key + "_ORIG")
    if lp_orig is not None:
        env[lp_key] = lp_orig  # restore the original, unmodified value
    else:
        # This happens when LD_LIBRARY_PATH was not set.
        # Remove the env var as a last resort:
        env.pop(lp_key, None)
    return env


def open_url(url: str) -> None:
    if sys.platform == "linux":
        env = get_env()
        subprocess.Popen(["xdg-open", url], env=env)
    else:
        webbrowser.open(url)


def open_webui(root_url: str) -> None:
    print("Opening dashboard")
    open_url(root_url)


def open_apibrowser(root_url: str) -> None:
    print("Opening api browser")
    open_url(root_url + "/api")


def open_dir(d: str) -> None:
    """From: http://stackoverflow.com/a/1795849/965332"""
    if sys.platform == "win32":
        os.startfile(d)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", d])
    else:
        env = get_env()
        subprocess.Popen(["xdg-open", d], env=env)


class TrayIcon(QSystemTrayIcon):
    MAX_AUTO_RESTARTS = 3
    RESTART_WINDOW_SECONDS = 600  # 10 minutes

    def __init__(
        self,
        manager: Manager,
        icon: QIcon,
        parent: Optional[QWidget] = None,
        testing: bool = False,
    ) -> None:
        QSystemTrayIcon.__init__(self, icon, parent)
        self._parent = parent  # QSystemTrayIcon also tries to save parent info but it screws up the type info
        self.setToolTip("ActivityWatch" + (" (testing)" if testing else ""))

        self.manager = manager
        self.testing = testing
        self._restart_timestamps: Dict[str, List[float]] = {}

        self.root_url = f"http://localhost:{5666 if self.testing else 5600}"
        self.activated.connect(self.on_activated)

        self._build_rootmenu()

    def _recent_restart_count(self, module_name: str) -> int:
        """Count restarts within the sliding time window."""
        now = time.monotonic()
        timestamps = self._restart_timestamps.get(module_name, [])
        cutoff = now - self.RESTART_WINDOW_SECONDS
        return sum(1 for t in timestamps if t > cutoff)

    def _record_restart(self, module_name: str) -> None:
        """Record a restart timestamp and prune old entries."""
        now = time.monotonic()
        cutoff = now - self.RESTART_WINDOW_SECONDS
        timestamps = self._restart_timestamps.get(module_name, [])
        # Prune old timestamps and add current
        self._restart_timestamps[module_name] = [
            t for t in timestamps if t > cutoff
        ] + [now]

    def on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            open_webui(self.root_url)

    def _build_rootmenu(self) -> None:
        menu = QMenu(self._parent)

        if self.testing:
            menu.addAction("Running in testing mode")  # .setEnabled(False)
            menu.addSeparator()

        # openWebUIIcon = QIcon.fromTheme("open")
        menu.addAction("Open Dashboard", lambda: open_webui(self.root_url))
        menu.addAction("Open API Browser", lambda: open_apibrowser(self.root_url))

        menu.addSeparator()

        modulesMenu = menu.addMenu("Modules")
        self._build_modulemenu(modulesMenu)

        menu.addSeparator()
        menu.addAction(
            "Open log folder", lambda: open_dir(aw_core.dirs.get_log_dir(None))
        )
        menu.addAction(
            "Open config folder", lambda: open_dir(aw_core.dirs.get_config_dir(None))
        )
        menu.addSeparator()

        exitIcon = QIcon.fromTheme(
            "application-exit", QIcon("media/application_exit.png")
        )
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
            box.setIcon(QMessageBox.Icon.Warning)
            recent = self._recent_restart_count(module.name)
            box.setText(
                f"Module {module.name} quit unexpectedly"
                + (
                    f" after {recent} auto-restart attempts"
                    f" in {self.RESTART_WINDOW_SECONDS // 60} minutes"
                    if recent >= self.MAX_AUTO_RESTARTS
                    else ""
                )
            )
            box.setDetailedText(module.read_log(self.testing))

            restart_button = QPushButton("Restart", box)

            def on_manual_restart() -> None:
                self._restart_timestamps.pop(module.name, None)
                module.start(self.testing)

            restart_button.clicked.connect(on_manual_restart)
            box.addButton(restart_button, QMessageBox.ButtonRole.AcceptRole)
            box.setStandardButtons(QMessageBox.StandardButton.Cancel)

            box.show()

        def rebuild_modules_menu() -> None:
            for action in modulesMenu.actions():
                if action.isEnabled():
                    module: Module = action.data()
                    alive = module.is_alive()
                    action.setChecked(alive)

            QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

        QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

        def check_module_status() -> None:
            unexpected_exits = self.manager.get_unexpected_stops()
            for module in unexpected_exits:
                recent = self._recent_restart_count(module.name)
                if recent < self.MAX_AUTO_RESTARTS:
                    logger.info(
                        f"Auto-restarting crashed module {module.name} "
                        f"(attempt {recent + 1}/{self.MAX_AUTO_RESTARTS}"
                        f" in {self.RESTART_WINDOW_SECONDS // 60}min window)"
                    )
                    module.stop()  # Clean up state
                    module.start(self.testing)
                    self._record_restart(module.name)
                    self.showMessage(
                        "ActivityWatch",
                        f"Module {module.name} crashed and was auto-restarted",
                        QSystemTrayIcon.MessageIcon.Warning,
                        5000,
                    )
                else:
                    logger.warning(
                        f"Module {module.name} exceeded max auto-restarts "
                        f"({self.MAX_AUTO_RESTARTS})"
                    )
                    show_module_failed_dialog(module)
                    module.stop()

            QtCore.QTimer.singleShot(5000, check_module_status)

        QtCore.QTimer.singleShot(5000, check_module_status)

    def _build_modulemenu(self, moduleMenu: QMenu) -> None:
        moduleMenu.clear()

        def add_module_menuitem(module: Module) -> None:
            title = module.name

            def on_toggle(m: Module = module) -> None:
                m.toggle(self.testing)
                # Reset auto-restart timestamps on manual toggle
                self._restart_timestamps.pop(m.name, None)

            ac = moduleMenu.addAction(title, on_toggle)

            ac.setData(module)
            ac.setCheckable(True)
            ac.setChecked(module.is_alive())

        for location, modules in [
            ("bundled", self.manager.modules_bundled),
            ("system", self.manager.modules_system),
        ]:
            header = moduleMenu.addAction(location)
            header.setEnabled(False)

            for module in sorted(modules, key=lambda m: m.name):
                add_module_menuitem(module)


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

    # This is needed for the icons to get picked up with PyInstaller
    scriptdir = Path(__file__).parent

    # When run from source:
    #   __file__ is aw_qt/trayicon.py
    #   scriptdir is ./aw_qt
    #   logodir is ./media/logo
    QtCore.QDir.addSearchPath("icons", str(scriptdir.parent / "media/logo/"))

    # When run from .app:
    #   __file__ is ./Contents/MacOS/aw-qt
    #   scriptdir is ./Contents/MacOS
    #   logodir is ./Contents/Resources/aw_qt/media/logo
    QtCore.QDir.addSearchPath(
        "icons", str(scriptdir.parent.parent / "Resources/aw_qt/media/logo/")
    )

    # logger.info(f"search paths: {QtCore.QDir.searchPaths('icons')}")

    # Without this, Ctrl+C will have no effect
    signal.signal(signal.SIGINT, lambda *args: exit(manager))
    # Ensure cleanup happens on SIGTERM
    signal.signal(signal.SIGTERM, lambda *args: exit(manager))

    timer = QtCore.QTimer()
    timer.start(100)  # You may change this if you wish.
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.

    # root widget
    widget = QWidget()

    # Wait for system tray to become available (up to 10 s).
    # On some desktop environments (e.g. KDE Plasma), autostart programs
    # launch before the panel/system tray is loaded.  Qt docs note that
    # "if the system tray is currently unavailable but becomes available
    # later, QSystemTrayIcon will automatically add an entry."
    # See: https://github.com/ActivityWatch/aw-qt/issues/97
    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.info("System tray not yet available, waiting up to 10 s...")
        for i in range(10):
            time.sleep(1)
            # Process events so Qt can detect tray availability changes
            app.processEvents()
            if QSystemTrayIcon.isSystemTrayAvailable():
                logger.info(f"System tray became available after {i + 1}s")
                break
        else:
            QMessageBox.critical(
                widget,
                "Systray",
                "I couldn't detect any system tray on this system. Either get one or run the ActivityWatch modules from the console.",
            )
            sys.exit(1)

    if sys.platform == "darwin":
        icon = QIcon("icons:black-monochrome-logo.png")
        # Allow macOS to use filters for changing the icon's color
        icon.setIsMask(True)
    else:
        icon = QIcon("icons:logo.png")

    trayIcon = TrayIcon(manager, icon, widget, testing=testing)
    trayIcon.show()

    # Re-apply tooltip after show() to ensure it registers with the
    # platform's system tray backend.  On Windows 11 the tooltip can
    # appear empty when it is only set before the icon is visible.
    # See: https://github.com/ActivityWatch/aw-qt/issues/112
    trayIcon.setToolTip(trayIcon.toolTip())

    QApplication.setQuitOnLastWindowClosed(False)

    logger.info("Initialized aw-qt and trayicon successfully")
    # Run the application, blocks until quit
    return app.exec()
