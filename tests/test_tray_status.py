"""Exercise the tray's scheduled crash-detection callback without a Qt event loop."""

import subprocess
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aw_qt.manager import Manager, Module
from aw_qt.trayicon import TrayIcon


@pytest.mark.parametrize("returncode", [0, 1, -15])
def test_status_check_only_restarts_crashes(returncode):
    module = Module("aw-notify", Path("/bundled/aw-notify"), "bundled")
    process = MagicMock(spec=subprocess.Popen)
    process.returncode = returncode
    module._process = process
    module.started = True
    with patch.object(Manager, "discover_modules"):
        manager = Manager(testing=True)
    manager.modules = [module]
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
        _restart_timestamps={},
        MAX_AUTO_RESTARTS=TrayIcon.MAX_AUTO_RESTARTS,
        RESTART_WINDOW_SECONDS=TrayIcon.RESTART_WINDOW_SECONDS,
    )
    tray._recent_restart_count = partial(TrayIcon._recent_restart_count, tray)
    tray._record_restart = partial(TrayIcon._record_restart, tray)

    with patch("aw_qt.trayicon.QMenu"), patch("aw_qt.trayicon.QIcon"), patch(
        "aw_qt.trayicon.autostart.is_supported", return_value=False
    ), patch("aw_qt.trayicon.read_notify_enabled", return_value=False), patch(
        "aw_qt.trayicon.QtCore.QTimer.singleShot"
    ) as single_shot, patch.object(module, "start") as start, patch.object(
        module, "stop", wraps=module.stop
    ) as stop:
        TrayIcon._build_rootmenu(tray)
        callbacks = [
            call.args[1]
            for call in single_shot.call_args_list
            if call.args[0] == 5000
        ]
        assert len(callbacks) == 1
        check_status = callbacks[0]
        check_status()

        if returncode == 0:
            start.assert_not_called()
            stop.assert_not_called()
            tray.showMessage.assert_not_called()
            assert tray._restart_timestamps == {}
        else:
            stop.assert_called_once_with()
            start.assert_called_once_with(True)
            tray.showMessage.assert_called_once()
            assert tray._recent_restart_count(module.name) == 1

        assert module.started is False
        assert module._process is None
        assert module._last_process is process
        single_shot.assert_called_with(5000, check_status)
        # The cleared process must not be handled again on the next poll.
        check_status()
        assert start.call_count == (0 if returncode == 0 else 1)
