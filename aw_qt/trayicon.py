import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlencode

import aw_core
from PyQt6 import QtCore
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)

from . import autostart
from .config import child_environment, dashboard_auth, persist_module_autostart
from .manager import (
    NOTIFY_MODULE,
    NOTIFY_SETTINGS_KEY,
    SUPPORTER_SETTINGS_KEY,
    Manager,
    Module,
    read_notify_enabled,
    write_notify_enabled,
)
from .profile import DEFAULT_PROFILE, TESTING_PROFILE

logger = logging.getLogger(__name__)


def get_env() -> Dict[str, str]:
    """
    Necessary for xdg-open to work properly when PyInstaller overrides LD_LIBRARY_PATH

    https://github.com/ActivityWatch/activitywatch/issues/208#issuecomment-417346407
    """
    env = child_environment()  # a copy, consistent with any in-flight profile switch
    if env.get("APPIMAGE"):
        # Inside an AppImage, PyInstaller's "original" library path belongs
        # to the AppImage, not the host. Its Qt plugins likewise cannot be
        # used by a system browser or file manager (ActivityWatch/activitywatch#1528).
        for key in (
            "LD_LIBRARY_PATH",
            "LD_LIBRARY_PATH_ORIG",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
        ):
            env.pop(key, None)
        return env
    lp_key = "LD_LIBRARY_PATH"  # for GNU/Linux and *BSD.
    lp_orig = env.get(lp_key + "_ORIG")
    if lp_orig is not None:
        env[lp_key] = lp_orig  # restore the original, unmodified value
    else:
        # This happens when LD_LIBRARY_PATH was not set.
        # Remove the env var as a last resort:
        env.pop(lp_key, None)
    return env


def _open_with_webbrowser(url: str) -> None:
    # Runs on a worker thread, where a Qt dialog is not safe: log the failure.
    # The query string can carry the dashboard API key: keep it out of the log.
    safe_url = url.split("?", 1)[0]
    try:
        # webbrowser has no env argument. Isolate it in a child so browsers
        # inherit get_env(), without racing other threads over os.environ.
        # In a frozen build sys.executable is aw-qt, not a Python interpreter.
        python = "python3" if getattr(sys, "frozen", False) else sys.executable
        result = subprocess.run(
            [
                python,
                "-c",
                # Reserve 3 for no browser; Python startup/runtime errors use 1/2.
                "import sys, webbrowser; sys.exit(0 if webbrowser.open(sys.argv[1]) else 3)",
                url,
            ],
            env=get_env(),
        )
    except Exception:
        logger.exception(f"Failed to open {safe_url}")
        return
    if result.returncode == 3:
        logger.warning(f"No browser found to open {safe_url}")
    elif result.returncode != 0:
        logger.warning(
            f"Browser helper exited with status {result.returncode} opening {safe_url}"
        )


def open_url(url: str) -> None:
    if sys.platform == "linux":
        env = get_env()
        try:
            subprocess.Popen(["xdg-open", url], env=env)
            return
        except OSError as exc:
            # No xdg-utils (minimal WM, container). An exception escaping this
            # Qt slot would abort aw-qt, so fall back to the stdlib launcher.
            logger.warning(f"xdg-open failed ({exc}), falling back to webbrowser")
            # webbrowser.open() can block until a foreground browser exits, and
            # this runs on the Qt thread. Keep the tray responsive.
            threading.Thread(
                target=_open_with_webbrowser, args=(url,), name="open-url", daemon=True
            ).start()
            return
    webbrowser.open(url)


def open_webui(root_url: str) -> None:
    print("Opening dashboard")
    open_url(root_url)


def open_apibrowser(root_url: str) -> None:
    print("Opening api browser")
    open_url(root_url + "/api")


# Attributed so the click is counted as `qt-tray` on activitywatch.net/go/
SUPPORT_URL = "https://activitywatch.net/go/?src=qt-tray"


def open_support() -> None:
    print("Opening support page")
    open_url(SUPPORT_URL)


def open_dir(d: Union[str, Path]) -> None:
    """From: http://stackoverflow.com/a/1795849/965332"""
    d = str(d)
    if sys.platform == "win32":
        os.startfile(d)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", d], env=child_environment())
    else:
        # xdg-open can stay alive as long as the file manager. Poll without
        # blocking Qt, and use a file so a full stderr pipe cannot stall it.
        try:
            stderr = tempfile.TemporaryFile()
        except OSError as exc:
            _show_open_dir_error(d, str(exc))
            return
        try:
            process = subprocess.Popen(["xdg-open", d], env=get_env(), stderr=stderr)
        except OSError as exc:
            stderr.close()
            _show_open_dir_error(d, str(exc))
            return

        timer = QtCore.QTimer(QApplication.instance())

        def check_finished() -> None:
            returncode = process.poll()
            if returncode is None:
                return
            timer.stop()
            timer.deleteLater()
            if returncode == 0:
                stderr.close()
                return
            try:
                size = stderr.seek(0, os.SEEK_END)
                stderr.seek(max(0, size - 8192))
                details = stderr.read(8192).decode("utf-8", errors="replace").strip()
                if size > 8192:
                    details = "[Earlier diagnostics truncated]\n" + details
            except OSError as exc:
                details = f"Could not read opener diagnostics: {exc}"
            finally:
                stderr.close()
            _show_open_dir_error(d, f"xdg-open exited with status {returncode}.\n{details}")

        timer.timeout.connect(check_finished)
        timer.start(100)


def _show_open_dir_error(path: str, details: str) -> None:
    logger.warning("Could not open folder %s: %s", path, details)
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Could not open folder")
    box.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    box.setText("Open this folder manually in your file manager:")
    box.setInformativeText(path)
    box.setDetailedText(details)
    box.setStandardButtons(QMessageBox.StandardButton.Close)
    copy_button = box.addButton("Copy path", QMessageBox.ButtonRole.ActionRole)
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(path))
    box.exec()


class TrayIcon(QSystemTrayIcon):
    MAX_AUTO_RESTARTS = 3
    RESTART_WINDOW_SECONDS = 600  # 10 minutes

    # (probe sequence number, enabled), emitted from the autostart probe thread
    _autostart_probed = QtCore.pyqtSignal(int, bool)

    def __init__(
        self,
        manager: Manager,
        icon: QIcon,
        parent: Optional[QWidget] = None,
        testing: bool = False,
        port: Optional[int] = None,
        profile: str = DEFAULT_PROFILE,
        persist_toggles: bool = True,
    ) -> None:
        QSystemTrayIcon.__init__(self, icon, parent)
        self._parent = parent  # QSystemTrayIcon also tries to save parent info but it screws up the type info
        self.setToolTip(
            "ActivityWatch" + (f" ({profile})" if profile != DEFAULT_PROFILE else "")
        )

        self.manager = manager
        self.testing = testing
        self.profile = profile
        # Whether a module start/stop from the menu is saved to
        # autostart_modules (off when --autostart-modules overrides the config).
        self.persist_toggles = persist_toggles
        self._restart_timestamps: Dict[Module, List[float]] = {}
        self._autostart_action: Optional[QAction] = None
        self._autostart_probe_seq = 0
        self._autostart_probe_running = False
        self._autostart_probed.connect(self._apply_autostart_probe)
        self._notify_action: Optional[QAction] = None
        self._support_action: Optional[QAction] = None
        self._support_network = QNetworkAccessManager(self)
        self._support_reply: Optional[QNetworkReply] = None
        self._notify_reply: Optional[QNetworkReply] = None
        self._notify_generation = 0

        if port is None:
            port = 5666 if testing else 5600
        self.port = port
        self.root_url = f"http://localhost:{port}"
        self.activated.connect(self.on_activated)

        self._build_rootmenu()

    def _recent_restart_count(self, module: Module) -> int:
        """Count restarts for this copy within the sliding time window."""
        now = time.monotonic()
        timestamps = self._restart_timestamps.get(module, [])
        cutoff = now - self.RESTART_WINDOW_SECONDS
        return sum(1 for t in timestamps if t > cutoff)

    def _record_restart(self, module: Module) -> None:
        """Record a restart timestamp for this copy and prune old entries."""
        now = time.monotonic()
        cutoff = now - self.RESTART_WINDOW_SECONDS
        timestamps = self._restart_timestamps.get(module, [])
        # Prune old timestamps and add current
        self._restart_timestamps[module] = [
            t for t in timestamps if t > cutoff
        ] + [now]

    def _refresh_autostart_action(self, force: bool = False) -> None:
        """Sync the menu item with the actual OS-level autostart state.

        The probe runs on a worker thread: on Linux it can fall through to
        `systemctl --user is-enabled`, which may take seconds. The last value
        stays in place meanwhile. Only the newest probe's result is applied,
        and a probe already in flight is reused unless `force` is set (after
        a toggle, an older probe may report the state from before it).
        """
        if self._autostart_action is None:
            return
        if self._autostart_probe_running and not force:
            return
        if self._autostart_probe_seq == 0:
            self._autostart_action.setEnabled(False)
            self._autostart_action.setText("Start at login (checking…)")
        self._autostart_probe_seq += 1
        seq = self._autostart_probe_seq
        self._autostart_probe_running = True

        def probe() -> None:
            enabled = autostart.is_enabled()
            try:
                self._autostart_probed.emit(seq, enabled)
            except RuntimeError:
                pass  # The tray icon was deleted while the probe ran.

        threading.Thread(target=probe, name="autostart-probe", daemon=True).start()

    def _apply_autostart_probe(self, seq: int, enabled: bool) -> None:
        if seq != self._autostart_probe_seq:
            return  # Superseded by a newer probe.
        self._autostart_probe_running = False
        if self._autostart_action is not None:
            self._autostart_action.setChecked(enabled)
            self._autostart_action.setText("Start at login")
            self._autostart_action.setEnabled(True)

    def _refresh_notify_action(self) -> None:
        """Sync the menu item with the shared `aw-notify.enabled` setting.

        Reads asynchronously like the support item, so a slow server never
        stalls the menu. An unreachable server or unreadable reply says nothing
        about the setting: the last value stays rather than showing "off".
        """
        if self._notify_action is None or self._notify_reply is not None:
            return
        request = QNetworkRequest(
            QtCore.QUrl(f"{self.root_url}/api/0/settings/{NOTIFY_SETTINGS_KEY}")
        )
        request.setTransferTimeout(500)
        reply = self._support_network.get(request)
        if reply is None:
            return
        self._notify_reply = reply
        generation = self._notify_generation
        deadline = QtCore.QTimer(reply)
        deadline.setSingleShot(True)
        deadline.timeout.connect(reply.abort)
        deadline.start(500)

        def finished() -> None:
            deadline.stop()
            enabled: Optional[bool] = None
            error = reply.error()
            if error == QNetworkReply.NetworkError.NoError:
                try:
                    data = json.loads(reply.readAll().data())
                    enabled = isinstance(data, dict) and data.get("enabled") is True
                except (ValueError, UnicodeDecodeError):
                    # An unreadable response says nothing about the saved setting.
                    pass
            elif error == QNetworkReply.NetworkError.ContentNotFoundError:
                enabled = False  # Key not created yet: notifications are off.
            # A toggle while the read was in flight wins over the older answer.
            if (
                enabled is not None
                and self._notify_action is not None
                and generation == self._notify_generation
            ):
                self._notify_action.setChecked(enabled)
            self._notify_reply = None
            reply.deleteLater()
            if generation != self._notify_generation:
                # The toggle's post-write refresh met this in-flight read.
                # Re-read now rather than losing confirmation of the saved state.
                self._refresh_notify_action()

        reply.finished.connect(finished)

    def _refresh_support_action(self) -> None:
        """Refresh asynchronously, leaving the last visibility in place meanwhile."""
        if self._support_action is None or self._support_reply is not None:
            return
        request = QNetworkRequest(
            QtCore.QUrl(f"{self.root_url}/api/0/settings/{SUPPORTER_SETTINGS_KEY}")
        )
        request.setTransferTimeout(500)
        reply = self._support_network.get(request)
        if reply is None:
            self._support_action.setVisible(True)
            return
        self._support_reply = reply
        # A total deadline also bounds a server that slowly trickles bytes.
        deadline = QtCore.QTimer(reply)
        deadline.setSingleShot(True)
        deadline.timeout.connect(reply.abort)
        deadline.start(500)

        def finished() -> None:
            deadline.stop()
            supporter = False
            if reply.error() == QNetworkReply.NetworkError.NoError:
                try:
                    supporter = json.loads(reply.readAll().data()) is True
                except (ValueError, UnicodeDecodeError):
                    # Invalid settings fail open, just like an unavailable server.
                    pass
            if self._support_action is not None:
                self._support_action.setVisible(not supporter)
            self._support_reply = None
            reply.deleteLater()

        reply.finished.connect(finished)

    def _on_notify_toggled(self) -> None:
        """Persist the opt-in flag, then start/stop aw-notify right away."""
        if self._notify_action is None:
            return
        checked = self._notify_action.isChecked()
        self._notify_generation += 1
        # Hold _notify_lock so the autostart background thread cannot claim a
        # second aw-notify instance between this write and the start/stop
        # decision. Spawn runs after release: it takes _spawn_env_lock.
        claimed = None
        with self.manager._notify_lock:
            if write_notify_enabled(self.port, checked):
                if checked:
                    claimed = self.manager._start_notify_locked()
                else:
                    self.manager.stop_notify()
            else:
                # Qt toggles before emitting triggered: undo the unsaved change
                # even if the refresh below cannot reach the server either.
                self._notify_action.setChecked(not checked)
                logger.error("Failed to save the aw-notify setting, is the server running?")
                box = QMessageBox(self._parent)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setText("Could not save the notifications setting. Is aw-server running?")
                box.show()
        self.manager._spawn_claimed(claimed)
        # Always reflect the real state, the toggle may have failed
        self._refresh_notify_action()

    def _on_autostart_toggled(self) -> None:
        """Enable/disable starting ActivityWatch at login."""
        if self._autostart_action is None:
            return
        # The action is checkable, so it already holds the state the user asked for
        checked = self._autostart_action.isChecked()
        try:
            if checked:
                autostart.enable()
            else:
                autostart.disable()
        except autostart.AutostartError as e:
            logger.error(f"Failed to change autostart setting: {e}")
            box = QMessageBox(self._parent)
            box.setIcon(QMessageBox.Icon.Warning)
            action = "enable" if checked else "disable"
            box.setText(f"Could not {action} starting ActivityWatch at login.")
            box.setDetailedText(str(e))
            box.show()
        # Always reflect the real state, the toggle may have failed
        self._refresh_autostart_action(force=True)

    def _open_dashboard(self) -> None:
        # Resolve at click time so rotating the key does not require a tray restart.
        # Never hand a Rust credential to a Python or ambiguously selected server.
        with self.manager._notify_lock:
            servers = [
                module for module in self.manager.modules
                if module.started and module.name in ("aw-server", "aw-server-rust")
            ]
            preparing = any(
                name in self.manager._starting
                for name in ("aw-server", "aw-server-rust")
            )
        auth = None
        if (
            not preparing
            and len(servers) == 1
            and servers[0].name == "aw-server-rust"
            # The public /info probe cannot establish an adopted listener's identity.
            and not getattr(servers[0], "_external_server", False)
            and servers[0].is_alive()
        ):
            auth = dashboard_auth(self.profile, self.port)
            if auth and getattr(servers[0], "dashboard_endpoint", None) != (
                self.profile, auth[0], self.port
            ):
                # Config edits cannot redirect a running child's credential.
                auth = None
            owned = getattr(servers[0], "dashboard_listener_owned", None)
            if auth and callable(owned) and not owned():
                # Launch identity is not listener ownership.
                auth = None
        url = self.root_url
        if auth:
            address, key = auth
            host = f"[{address}]" if ":" in address else address
            url = f"http://{host}:{self.port}?" + urlencode({"token": key})
        open_webui(url)

    def on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_dashboard()

    def _build_rootmenu(self) -> None:
        menu = QMenu(self._parent)

        if self.profile != DEFAULT_PROFILE:
            label = (
                "Running in testing mode"
                if self.profile == TESTING_PROFILE
                else f"Running in profile: {self.profile}"
            )
            menu.addAction(label)  # .setEnabled(False)
            menu.addSeparator()

        # openWebUIIcon = QIcon.fromTheme("open")
        menu.addAction("Open Dashboard", lambda: self._open_dashboard())
        menu.addAction("Open API Browser", lambda: open_apibrowser(self.root_url))
        self._support_action = menu.addAction("Support ActivityWatch", open_support)
        self._refresh_support_action()
        # The flag is set from the web UI's Settings page, so re-read it on open
        menu.aboutToShow.connect(self._refresh_support_action)

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

        if NOTIFY_MODULE in [m.name for m in self.manager.modules]:
            self._notify_action = menu.addAction(
                "Enable notifications", self._on_notify_toggled
            )
            self._notify_action.setCheckable(True)
            self._notify_action.setChecked(read_notify_enabled(self.port, timeout=0.5))
            # The flag can also be changed from the web UI, re-read it on open
            menu.aboutToShow.connect(self._refresh_notify_action)

        if autostart.is_supported():
            self._autostart_action = menu.addAction(
                "Start at login", self._on_autostart_toggled
            )
            self._autostart_action.setCheckable(True)
            self._refresh_autostart_action()
            # Autostart can also be changed outside of aw-qt, so re-read the
            # actual state every time the menu is opened
            menu.aboutToShow.connect(self._refresh_autostart_action)

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

        def show_module_failed_dialog(
            module: Module, launch_error: Optional[Exception] = None
        ) -> None:
            box = QMessageBox(self._parent)
            box.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
            box.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            box.setIcon(QMessageBox.Icon.Warning)
            recent = self._recent_restart_count(module)
            box.setText(
                f"Module {module.name} quit unexpectedly"
                + (
                    f" after {recent} auto-restart attempts"
                    f" in {self.RESTART_WINDOW_SECONDS // 60} minutes"
                    if recent >= self.MAX_AUTO_RESTARTS
                    else ""
                )
            )
            if launch_error is not None:
                box.setInformativeText(f"Could not restart module: {launch_error}")
            try:
                log = module.read_log(self.testing)
            except (OSError, UnicodeError):
                logger.exception(f"Failed to read log for module {module.name}")
                log = "Unable to read the module log. See the aw-qt log for details."
            box.setDetailedText(log)

            restart_button = QPushButton("Restart", box)

            def on_manual_restart() -> None:
                self._restart_timestamps.pop(module, None)
                try:
                    if not module.is_alive():
                        self.manager.restart(module)
                except Exception as exc:
                    # Still unlaunchable: an exception escaping this Qt slot
                    # would abort the tray. Say why instead.
                    logger.exception(f"Failed to restart module {module.name}")
                    error_box = QMessageBox(self._parent)
                    error_box.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
                    error_box.setTextFormat(QtCore.Qt.TextFormat.PlainText)
                    error_box.setIcon(QMessageBox.Icon.Warning)
                    error_box.setText(f"Could not start module {module.name}: {exc}")
                    error_box.show()

            restart_button.clicked.connect(on_manual_restart)
            box.addButton(restart_button, QMessageBox.ButtonRole.AcceptRole)
            box.setStandardButtons(QMessageBox.StandardButton.Cancel)

            box.show()

        def rebuild_modules_menu() -> None:
            for action in modulesMenu.actions():
                if action.isEnabled():
                    module: Module = action.data()
                    alive = self.manager.get_module_status(module)
                    # Preserve the last menu state when liveness is unknown.
                    if alive is not None:
                        action.setChecked(alive)

            QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

        QtCore.QTimer.singleShot(2000, rebuild_modules_menu)

        def handle_unexpected_stops() -> None:
            unexpected_exits = self.manager.get_unexpected_stops()
            for module in unexpected_exits:
                try:
                    handle_unexpected_stop(module)
                except Exception:
                    # One broken module must not starve the rest of this poll.
                    logger.exception(f"Failed to handle stopped module {module.name}")

        def handle_unexpected_stop(module: Module) -> None:
            if module.exited_cleanly():
                # A clean exit (code 0) is intentional, not a crash — e.g.
                # aw-notify exits 0 while notifications are disabled by its
                # opt-in gate. Restarting it here would loop every interval.
                logger.info(
                    f"Module {module.name} exited cleanly (exit 0); "
                    "not auto-restarting"
                )
                module.clear_exited_process()
                # A clean exit ends the crash episode: drop the restart
                # history so a later single crash is not mistaken for a
                # repeated failure. Mirrors the manual toggle/restart paths.
                self._restart_timestamps.pop(module, None)
                return
            recent = self._recent_restart_count(module)
            if recent < self.MAX_AUTO_RESTARTS:
                logger.info(
                    f"Auto-restarting crashed module {module.name} "
                    f"(attempt {recent + 1}/{self.MAX_AUTO_RESTARTS}"
                    f" in {self.RESTART_WINDOW_SECONDS // 60}min window)"
                )
                # Count the attempt before launching: a relaunch that raises
                # still uses up the budget.
                self._record_restart(module)
                try:
                    if not self.manager.restart(module):
                        return  # Shutdown cancelled this restart.
                except Exception as exc:
                    # restart() already cleared `started`, so later polls will
                    # not see this module again. Report it now, don't drop it.
                    logger.exception(
                        f"Failed to auto-restart module {module.name}; it will "
                        "not be retried automatically (use Restart or the "
                        "Modules menu)"
                    )
                    show_module_failed_dialog(module, launch_error=exc)
                    return
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
                try:
                    show_module_failed_dialog(module)
                finally:
                    # Clear this crash even if the dialog cannot be shown.
                    module.stop()

        def check_module_status() -> None:
            try:
                handle_unexpected_stops()
            except Exception:
                # Even failure to collect stopped modules must not end polling.
                logger.exception("Failed to handle a module that stopped unexpectedly")
            QtCore.QTimer.singleShot(5000, check_module_status)

        QtCore.QTimer.singleShot(5000, check_module_status)

    def _on_module_clicked(self, module: Module) -> None:
        """Start or stop ``module`` from the menu, and keep that choice.

        Only an explicit click lands here. Crash restarts and shutdown go
        through ``Module.start``/``stop`` directly and never touch the config.
        """
        enabled = self.manager.toggle(module)
        # Reset auto-restart timestamps for only the manually toggled copy.
        self._restart_timestamps.pop(module, None)
        if not self.persist_toggles:
            return
        if self.manager.resolve(module.name) is not module:
            # autostart_modules holds names, and a name autostarts the bundled
            # copy. Saving a click on the system copy would start (or stop)
            # the other one next time.
            logger.info(
                f"Not saving tray toggle of {module.name} ({module.type}): "
                "autostart would run the bundled copy"
            )
            return
        try:
            persist_module_autostart(module.name, enabled, self.profile)
        except Exception:
            # An exception escaping a Qt slot aborts the app; the module has
            # already been started/stopped, so just log it.
            logger.exception(f"Failed to save autostart change for {module.name}")

    def _build_modulemenu(self, moduleMenu: QMenu) -> None:
        moduleMenu.clear()

        def add_module_menuitem(module: Module) -> None:
            title = module.name

            def on_toggle(m: Module = module) -> None:
                self._on_module_clicked(m)

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


def run(
    manager: Manager,
    testing: bool = False,
    port: Optional[int] = None,
    profile: str = DEFAULT_PROFILE,
    persist_toggles: bool = True,
) -> Any:
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

    trayIcon = TrayIcon(
        manager,
        icon,
        widget,
        testing=testing,
        port=port,
        profile=profile,
        persist_toggles=persist_toggles,
    )
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
