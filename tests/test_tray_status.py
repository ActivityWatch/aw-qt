"""Exercise the tray's scheduled crash-detection callback without a Qt event loop."""

import subprocess
import time
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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
    stack.enter_context(patch.object(module, "start"))
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
            module.start.assert_called_once_with(True)
            tray.showMessage.assert_called_once()
            assert tray._recent_restart_count(module.name) == 1

        assert module.started is False
        assert module._process is None
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
        timestamps={module.name: [now] * TrayIcon.MAX_AUTO_RESTARTS},
    )
    assert tray._recent_restart_count(module.name) == TrayIcon.MAX_AUTO_RESTARTS

    with ExitStack() as stack:
        single_shot = enter_tray_patches(stack, module)
        TrayIcon._build_rootmenu(tray)
        scheduled_status_callback(single_shot)()

        assert tray._restart_timestamps.get(module.name) is None
        module.start.assert_not_called()
    assert tray._recent_restart_count(module.name) == 0
