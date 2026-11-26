"""Exercise the tray's scheduled crash-detection callback without a Qt event loop."""

import subprocess
import time
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

from PyQt6 import QtCore

from aw_qt.manager import Manager, Module
from aw_qt.trayicon import TrayIcon


def make_tray(module, manager, timestamps=None):
    """Build a TrayIcon stand-in carrying the attributes _build_rootmenu uses."""
    tray = SimpleNamespace(
        manager=manager,
        _parent=None,
        profile="default",
        port=5600,
        root_url="http://localhost:5600",
        testing=True,
        _build_modulemenu=MagicMock(),
        _on_notify_toggled=MagicMock(),
        _refresh_notify_action=MagicMock(),
        _refresh_support_action=MagicMock(),
        setContextMenu=MagicMock(),
        showMessage=MagicMock(),
        _restart_timestamps=dict(timestamps or {}),
        MAX_AUTO_RESTARTS=TrayIcon.MAX_AUTO_RESTARTS,
        RESTART_WINDOW_SECONDS=TrayIcon.RESTART_WINDOW_SECONDS,
    )
    tray._recent_restart_count = partial(TrayIcon._recent_restart_count, tray)
    tray._record_restart = partial(TrayIcon._record_restart, tray)
    return tray


def enter_tray_patches(stack, module):
    """Enter the Qt and aw-notify surfaces _build_rootmenu touches."""
    stack.enter_context(patch("aw_qt.trayicon.QMenu"))
    stack.enter_context(patch("aw_qt.trayicon.QIcon"))
    stack.enter_context(patch("aw_qt.trayicon.autostart.is_supported", return_value=False))
    stack.enter_context(patch("aw_qt.trayicon.read_notify_enabled", return_value=False))

    def start(testing, launch_guard):
        with launch_guard() as allowed:
            if allowed:
                module._process = MagicMock(spec=subprocess.Popen)
                module._process.returncode = None
                module.started = True

    stack.enter_context(patch.object(module, "start", side_effect=start))
    stack.enter_context(patch.object(module, "stop", wraps=module.stop))
    single_shot = stack.enter_context(
        patch("aw_qt.trayicon.QtCore.QTimer.singleShot")
    )
    return single_shot


def scheduled_status_callback(single_shot):
    """Return the callback scheduled on the 5s status-check timer."""
    callbacks = [
        call.args[1] for call in single_shot.call_args_list if call.args[0] == 5000
    ]
    assert len(callbacks) == 1
    return callbacks[0]


def make_module(name, returncode):
    module = Module(name, Path(f"/bundled/{name}"), "bundled")
    process = MagicMock(spec=subprocess.Popen)
    process.returncode = returncode
    module._process = process
    module.started = True
    return module, process


def make_manager(module):
    with patch.object(Manager, "discover_modules"):
        manager = Manager(testing=True)
    manager.modules = [module]
    return manager


@pytest.mark.parametrize("returncode", [0, 1, -15])
def test_status_check_only_restarts_crashes(returncode):
    module, process = make_module("aw-notify", returncode)
    manager = make_manager(module)
    tray = make_tray(module, manager)

    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, module)
        TrayIcon._build_rootmenu(tray)
        check_status = scheduled_status_callback(single_shot)
        check_status()

        if returncode == 0:
            module.start.assert_not_called()
            module.stop.assert_not_called()
            tray.showMessage.assert_not_called()
            assert tray._restart_timestamps == {}
        else:
            module.stop.assert_called_once_with()
            module.start.assert_called_once_with(True, launch_guard=ANY)
            tray.showMessage.assert_called_once()
            assert tray._recent_restart_count(module) == 1

        assert module.started is (returncode != 0)
        if returncode == 0:
            assert module._process is None
        else:
            assert module.is_alive()
        assert module._last_process is process
        single_shot.assert_called_with(5000, check_status)
        # The cleared process must not be handled again on the next poll.
        check_status()
        assert module.start.call_count == (0 if returncode == 0 else 1)


def test_clean_exit_resets_restart_history():
    """A clean exit ends the crash episode.

    If the restart history survived a clean exit, a module that had already
    used its auto-restart budget would, on its next single crash, look like a
    repeated failure and be stopped instead of restarted.
    """
    module, _process = make_module("aw-notify", 0)
    manager = make_manager(module)
    now = time.monotonic()
    tray = make_tray(
        module,
        manager,
        timestamps={module: [now] * TrayIcon.MAX_AUTO_RESTARTS},
    )
    assert tray._recent_restart_count(module) == TrayIcon.MAX_AUTO_RESTARTS

    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, module)
        TrayIcon._build_rootmenu(tray)
        scheduled_status_callback(single_shot)()

        assert tray._restart_timestamps.get(module) is None
        module.start.assert_not_called()
    assert tray._recent_restart_count(module) == 0


@pytest.mark.parametrize("checked", [False, True])
@pytest.mark.parametrize("saved", [False, True])
def test_notify_toggle_retains_only_saved_state_when_refresh_fails(
    checked, saved, monkeypatch
):
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QApplication

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    action = QAction("Enable notifications")
    action.setCheckable(True)
    action.setChecked(not checked)
    tray = SimpleNamespace(
        _notify_action=action,
        port=5600,
        manager=MagicMock(),
        _parent=None,
        _notify_generation=0,
        # The async re-read is covered in test_supporter_menu.py.
        _refresh_notify_action=MagicMock(),
    )
    action.triggered.connect(lambda: TrayIcon._on_notify_toggled(tray))
    with (
        patch("aw_qt.trayicon.write_notify_enabled", return_value=saved) as write,
        patch("aw_qt.trayicon.QMessageBox"),
    ):
        action.trigger()
    write.assert_called_once_with(5600, checked)
    assert action.isChecked() == (checked if saved else not checked)
    assert tray.manager._start_notify_locked.call_count == int(saved and checked)
    assert tray.manager.stop_notify.call_count == int(saved and not checked)
    assert tray._notify_generation == 1
    assert app is not None


@pytest.mark.parametrize("manual", [False, True])
def test_restart_callback_does_not_start_after_shutdown(manual):
    module, _ = make_module("aw-watcher-afk", 1)
    manager = make_manager(module)
    now = time.monotonic()
    tray = make_tray(
        module, manager,
        timestamps={module: [now] * TrayIcon.MAX_AUTO_RESTARTS} if manual else None,
    )
    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, module)
        stack.enter_context(patch("aw_qt.trayicon.QMessageBox"))
        button = stack.enter_context(patch("aw_qt.trayicon.QPushButton"))
        TrayIcon._build_rootmenu(tray)
        check_status = scheduled_status_callback(single_shot)
        if manual:
            check_status()
            restart = button.return_value.clicked.connect.call_args.args[0]
            manager.stop_all()
            restart()
        else:
            original = manager.get_unexpected_stops

            def shutdown_after_snapshot():
                modules = original()
                manager.stop_all()  # Signal handler interrupted the callback.
                return modules

            stack.enter_context(patch.object(
                manager, "get_unexpected_stops", side_effect=shutdown_after_snapshot
            ))
            check_status()
        module.start.assert_not_called()
        assert not manager._starting
def test_failed_auto_restart_keeps_status_check_scheduled():
    # A module whose binary can no longer be executed (e.g. its shebang
    # interpreter was removed) raises from start(). That must not stop the
    # 5 s status check, or no module is ever restarted or reported again.
    module, _ = make_module("aw-watcher-afk", 1)
    manager = make_manager(module)
    tray = make_tray(module, manager)

    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, module)
        module.start.side_effect = OSError("Exec format error")
        box = stack.enter_context(patch("aw_qt.trayicon.QMessageBox"))
        stack.enter_context(patch("aw_qt.trayicon.QPushButton"))
        stack.enter_context(patch.object(module, "read_log", return_value="log"))
        TrayIcon._build_rootmenu(tray)
        check_status = scheduled_status_callback(single_shot)
        single_shot.reset_mock()
        check_status()

        single_shot.assert_called_once_with(5000, check_status)
        # stop() cleared `started`, so no later poll sees this module: the
        # failed relaunch must be reported now and still use up an attempt.
        assert module.started is False
        box.return_value.show.assert_called_once_with()
        assert "Exec format error" in box.return_value.setInformativeText.call_args.args[0]
        box.return_value.setAttribute.assert_called_once_with(
            QtCore.Qt.WidgetAttribute.WA_DeleteOnClose
        )
        assert tray._recent_restart_count(module) == 1


@pytest.mark.parametrize("failure", ["restart", "log", "decode", "dialog"])
def test_failed_module_does_not_block_later_crash_recovery(failure):
    first, _ = make_module("aw-watcher-afk", 1)
    second, _ = make_module("aw-watcher-window", 1)
    manager = make_manager(first)
    manager.modules.append(second)
    timestamps = (
        {first: [time.monotonic()] * TrayIcon.MAX_AUTO_RESTARTS}
        if failure != "restart"
        else {}
    )
    tray = make_tray(first, manager, timestamps=timestamps)

    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, first)
        stack.enter_context(patch.object(second, "start"))
        box = stack.enter_context(patch("aw_qt.trayicon.QMessageBox"))
        stack.enter_context(patch("aw_qt.trayicon.QPushButton"))
        if failure == "restart":
            first.start.side_effect = OSError("Exec format error")
            stack.enter_context(patch.object(first, "read_log", return_value="log"))
        elif failure in ("log", "decode"):
            error = (
                PermissionError("log denied")
                if failure == "log"
                else UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
            )
            stack.enter_context(patch.object(first, "read_log", side_effect=error))
        else:
            box.side_effect = RuntimeError("dialog unavailable")
        TrayIcon._build_rootmenu(tray)
        check_status = scheduled_status_callback(single_shot)
        single_shot.reset_mock()
        check_status()

        second.start.assert_called_once_with(True, launch_guard=ANY)
        assert first.started is False
        assert first._process is None
        single_shot.assert_called_once_with(5000, check_status)
        if failure in ("log", "decode"):
            box.return_value.show.assert_called_once_with()
            assert "log" in box.return_value.setDetailedText.call_args.args[0].lower()
        # Neither failure may keep the first crash active and starve later polls.
        check_status()
        assert second.start.call_count == 1


def test_manual_restart_of_unlaunchable_module_reports_instead_of_raising():
    # The failure dialog's Restart button runs in a Qt slot: if the module
    # still cannot launch, the error must be shown, not escape and abort aw-qt.
    module, _ = make_module("aw-watcher-afk", 1)
    manager = make_manager(module)
    tray = make_tray(module, manager)

    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, module)
        module.start.side_effect = OSError("Exec format error")
        failed_dialog, warning = MagicMock(), MagicMock()
        stack.enter_context(
            patch("aw_qt.trayicon.QMessageBox", side_effect=[failed_dialog, warning])
        )
        button = stack.enter_context(patch("aw_qt.trayicon.QPushButton"))
        stack.enter_context(patch.object(module, "read_log", return_value="log"))
        TrayIcon._build_rootmenu(tray)
        scheduled_status_callback(single_shot)()
        failed_dialog.show.assert_called_once_with()

        on_restart = button.return_value.clicked.connect.call_args.args[0]
        on_restart()

        warning.show.assert_called_once_with()
        warning.setAttribute.assert_called_once_with(
            QtCore.Qt.WidgetAttribute.WA_DeleteOnClose
        )
        assert "Exec format error" in warning.setText.call_args.args[0]
        assert module.started is False


@pytest.mark.parametrize("first_exit", [0, 1])
def test_same_name_copies_have_independent_restart_budgets(first_exit):
    first, _ = make_module("aw-watcher-afk", first_exit)
    second, _ = make_module("aw-watcher-afk", 1)
    second.path = Path("/system/aw-watcher-afk")
    second.type = "system"
    manager = make_manager(first)
    manager.modules.append(second)
    tray = make_tray(first, manager)
    # The working copy has one attempt left. A failed restart (or clean exit)
    # of the other copy must neither spend nor reset that budget.
    tray._record_restart(second)
    tray._record_restart(second)
    tray._record_restart(first)
    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, first)
        first.start.side_effect = OSError("missing executable")
        stack.enter_context(patch.object(first, "read_log", return_value="log"))
        stack.enter_context(patch.object(second, "start"))
        stack.enter_context(patch("aw_qt.trayicon.QMessageBox"))
        stack.enter_context(patch("aw_qt.trayicon.QPushButton"))
        TrayIcon._build_rootmenu(tray)
        scheduled_status_callback(single_shot)()
        second.start.assert_called_once_with(True, launch_guard=ANY)
        assert tray._recent_restart_count(second) == 3
        assert tray._recent_restart_count(first) == (0 if first_exit == 0 else 2)


def test_menu_refresh_isolates_status_errors_and_keeps_polling():
    first, _ = make_module("aw-server", 1)
    second, _ = make_module("aw-watcher-afk", 1)
    manager = make_manager(first)
    manager.modules.append(second)
    tray = make_tray(first, manager)

    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, first)
        menu = stack.enter_context(patch("aw_qt.trayicon.QMenu"))
        first_action, second_action = MagicMock(), MagicMock()
        first_action.data.return_value = first
        second_action.data.return_value = second
        menu.return_value.addMenu.return_value.actions.return_value = [
            first_action,
            second_action,
        ]
        stack.enter_context(
            patch.object(first, "is_alive", side_effect=ValueError("bad port"))
        )
        stack.enter_context(patch.object(second, "is_alive", return_value=False))
        TrayIcon._build_rootmenu(tray)
        refresh = next(
            call.args[1] for call in single_shot.call_args_list if call.args[0] == 2000
        )
        single_shot.reset_mock()

        refresh()

        first_action.setChecked.assert_not_called()
        second_action.setChecked.assert_called_once_with(False)
        single_shot.assert_called_once_with(2000, refresh)


@pytest.mark.parametrize("alive", [True, ValueError("bad port")])
def test_stale_restart_dialog_does_not_launch_another_copy(alive):
    module, _ = make_module("aw-watcher-afk", 1)
    manager = make_manager(module)
    tray = make_tray(module, manager)
    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, module)
        module.start.side_effect = OSError("missing executable")
        message_box = stack.enter_context(patch("aw_qt.trayicon.QMessageBox"))
        button = stack.enter_context(patch("aw_qt.trayicon.QPushButton"))
        stack.enter_context(patch.object(module, "read_log", return_value="log"))
        TrayIcon._build_rootmenu(tray)
        scheduled_status_callback(single_shot)()
        on_restart = button.return_value.clicked.connect.call_args.args[0]
        module.start.reset_mock(side_effect=True)
        message_box.reset_mock()
        # The watcher was started from Modules, or its current status cannot
        # be determined. Neither permits replacing its only process handle.
        stack.enter_context(
            patch.object(
                module, "is_alive",
                side_effect=alive if isinstance(alive, Exception) else None,
                return_value=alive,
            )
        )
        on_restart()
        module.start.assert_not_called()
        texts = [c.args[0] for c in message_box.return_value.setText.call_args_list]
        if isinstance(alive, Exception):
            # An unreadable status is reported as such, never as a failed launch.
            assert len(texts) == 1
            assert "Could not check whether" in texts[0]
            assert "Could not start" not in texts[0]
        else:
            assert texts == []


@pytest.mark.parametrize("recover_from_menu", [False, True])
def test_status_polls_log_once_per_failure_episode(recover_from_menu):
    module, _ = make_module("aw-server", 1)
    manager = make_manager(module)
    tray = make_tray(module, manager)
    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, module)
        menu = stack.enter_context(patch("aw_qt.trayicon.QMenu"))
        action = MagicMock()
        action.data.return_value = module
        menu.return_value.addMenu.return_value.actions.return_value = [action]
        alive = stack.enter_context(
            patch.object(module, "is_alive", side_effect=ValueError("bad port"))
        )
        TrayIcon._build_rootmenu(tray)
        refresh = next(
            call.args[1] for call in single_shot.call_args_list if call.args[0] == 2000
        )
        check_status = scheduled_status_callback(single_shot)
        manager_log = stack.enter_context(patch("aw_qt.manager.logger.exception"))
        tray_log = stack.enter_context(patch("aw_qt.trayicon.logger.exception"))
        for _ in range(3):
            refresh()
            check_status()
        assert manager_log.call_count + tray_log.call_count == 1
        action.setChecked.assert_not_called()
        # Either poll observing success ends the shared failure episode.
        alive.side_effect = None
        alive.return_value = True
        (refresh if recover_from_menu else check_status)()
        alive.side_effect = ValueError("bad port again")
        check_status()
        refresh()
        assert manager_log.call_count + tray_log.call_count == 2
