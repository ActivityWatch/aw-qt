"""The "Start at login" probe must not block the tray's Qt event loop.

On Linux `autostart.is_enabled()` can fall through to `systemctl --user
is-enabled`, which may take seconds (up to its 5 s timeout) when the user
manager is slow or absent (ActivityWatch/aw-qt#158, item 3).
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import QtCore
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication

from aw_qt.trayicon import TrayIcon


@pytest.fixture(scope="module")
def app():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("QT_QPA_PLATFORM", "offscreen")
        instance = QApplication.instance() or QApplication([])
        yield instance


def spin_until(app, predicate):
    deadline = QtCore.QDeadlineTimer(3000)
    while not predicate() and not deadline.hasExpired():
        app.processEvents()
        threading.Event().wait(0.001)
    assert predicate()


class SlowProbe:
    """Stands in for `autostart.is_enabled`, blocking until released."""

    def __init__(self, value: bool) -> None:
        self.value = value
        self.calls = 0
        self.release = threading.Event()

    def __call__(self) -> bool:
        self.calls += 1
        value = self.value
        self.release.wait(5)
        return value


@pytest.fixture
def tray(app):
    with patch.object(TrayIcon, "_build_rootmenu"):
        instance = TrayIcon(MagicMock(), QIcon(), port=1)
    action = QAction("Start at login", instance)
    action.setCheckable(True)
    instance._autostart_action = action
    yield instance
    instance.deleteLater()
    app.processEvents()


def test_slow_probe_does_not_block_menu_open(app, tray):
    probe = SlowProbe(True)
    with patch("aw_qt.trayicon.autostart.is_enabled", probe):
        started = time.monotonic()
        tray._refresh_autostart_action()
        assert time.monotonic() - started < 0.5
        # The last known value stays in place while the probe runs.
        assert not tray._autostart_action.isChecked()
        probe.release.set()
        spin_until(app, tray._autostart_action.isChecked)


def test_reopening_menu_during_probe_does_not_pile_up_probes(app, tray):
    probe = SlowProbe(True)
    with patch("aw_qt.trayicon.autostart.is_enabled", probe):
        for _ in range(5):
            tray._refresh_autostart_action()
        spin_until(app, lambda: probe.calls == 1)
        app.processEvents()
        assert probe.calls == 1
        probe.release.set()
        spin_until(app, tray._autostart_action.isChecked)


def test_toggle_during_probe_wins_over_stale_result(app, tray):
    # A probe started before the toggle reports the old state (disabled).
    stale = SlowProbe(False)
    with patch("aw_qt.trayicon.autostart.is_enabled", stale):
        tray._refresh_autostart_action()
        spin_until(app, lambda: stale.calls == 1)

    fresh = SlowProbe(True)
    fresh.release.set()
    tray._autostart_action.setChecked(True)  # Qt toggles before triggered
    with patch("aw_qt.trayicon.autostart.is_enabled", fresh), patch(
        "aw_qt.trayicon.autostart.enable"
    ) as enable:
        tray._on_autostart_toggled()
        enable.assert_called_once()
        spin_until(app, lambda: fresh.calls == 1)
        app.processEvents()
        # Let the stale probe finish after the fresh one was applied.
        stale.release.set()
        for _ in range(50):
            app.processEvents()
            threading.Event().wait(0.002)
    assert tray._autostart_action.isChecked()
