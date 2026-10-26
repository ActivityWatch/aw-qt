"""System openers must not inherit AppImage's bundled Qt environment."""

import os

import pytest

from aw_qt.trayicon import get_env


@pytest.mark.parametrize("appimage", [False, True])
def test_system_opener_environment(monkeypatch, appimage):
    for key in ("APPIMAGE", "APPDIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/pyinstaller/libraries")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/libraries")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/host/qt/plugins")
    monkeypatch.setenv("QT_QPA_PLATFORM_PLUGIN_PATH", "/host/qt/platforms")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("DISPLAY", ":0")
    if appimage:
        monkeypatch.setenv("APPIMAGE", "/downloads/ActivityWatch.AppImage")
        monkeypatch.setenv("APPDIR", "/tmp/.mount_ActivityWatch")
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/tmp/.mount_ActivityWatch")
        monkeypatch.setenv("QT_PLUGIN_PATH", "/tmp/.mount_ActivityWatch/qt/plugins")
        monkeypatch.setenv(
            "QT_QPA_PLATFORM_PLUGIN_PATH", "/tmp/.mount_ActivityWatch/qt/platforms"
        )
    parent = dict(os.environ)

    child = get_env()

    if appimage:
        for key in (
            "LD_LIBRARY_PATH",
            "LD_LIBRARY_PATH_ORIG",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
        ):
            assert key not in child, key
    else:
        assert child["LD_LIBRARY_PATH"] == "/host/libraries"
        assert child["QT_PLUGIN_PATH"] == "/host/qt/plugins"
        assert child["QT_QPA_PLATFORM_PLUGIN_PATH"] == "/host/qt/platforms"
    assert child["DBUS_SESSION_BUS_ADDRESS"] == parent["DBUS_SESSION_BUS_ADDRESS"]
    assert child["DISPLAY"] == parent["DISPLAY"]
    assert dict(os.environ) == parent
