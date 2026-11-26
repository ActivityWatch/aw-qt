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


def test_frozen_bundle_strips_bundled_qt_plugin_paths(monkeypatch, tmp_path):
    bundle_root = tmp_path / "activitywatch"
    bundled_plugins = bundle_root / "PyQt6" / "Qt6" / "plugins"
    bundled_platforms = bundled_plugins / "platforms"
    host_plugins = tmp_path / "host-qt" / "plugins"
    bundled_platforms.mkdir(parents=True)
    host_plugins.mkdir(parents=True)

    for key in ("APPIMAGE", "APPDIR", "LD_LIBRARY_PATH_ORIG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("aw_qt.trayicon.sys.frozen", True, raising=False)
    monkeypatch.setattr("aw_qt.trayicon.sys._MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle_root))
    monkeypatch.setenv(
        "QT_PLUGIN_PATH",
        os.pathsep.join([str(bundled_plugins), str(host_plugins)]),
    )
    monkeypatch.setenv("QT_QPA_PLATFORM_PLUGIN_PATH", str(bundled_platforms))

    child = get_env()

    assert "LD_LIBRARY_PATH" not in child
    assert child["QT_PLUGIN_PATH"] == str(host_plugins)
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in child


def test_frozen_bundle_removes_qt_plugin_path_when_only_bundled(monkeypatch, tmp_path):
    bundle_root = tmp_path / "activitywatch"
    bundled_plugins = bundle_root / "PyQt6" / "Qt6" / "plugins"
    bundled_plugins.mkdir(parents=True)

    for key in ("APPIMAGE", "APPDIR", "LD_LIBRARY_PATH_ORIG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("aw_qt.trayicon.sys.frozen", True, raising=False)
    monkeypatch.setattr("aw_qt.trayicon.sys._MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setenv("QT_PLUGIN_PATH", str(bundled_plugins))
    monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)

    child = get_env()

    assert "QT_PLUGIN_PATH" not in child
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in child


def test_frozen_bundle_preserves_host_only_qt_plugin_paths(monkeypatch, tmp_path):
    bundle_root = tmp_path / "activitywatch"
    host_plugins = tmp_path / "host-qt" / "plugins"
    bundle_root.mkdir(parents=True)
    host_plugins.mkdir(parents=True)

    for key in ("APPIMAGE", "APPDIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("aw_qt.trayicon.sys.frozen", True, raising=False)
    monkeypatch.setattr("aw_qt.trayicon.sys._MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setenv("QT_PLUGIN_PATH", str(host_plugins))
    monkeypatch.setenv("QT_QPA_PLATFORM_PLUGIN_PATH", str(host_plugins / "platforms"))

    child = get_env()

    assert child["QT_PLUGIN_PATH"] == str(host_plugins)
    assert child["QT_QPA_PLATFORM_PLUGIN_PATH"] == str(host_plugins / "platforms")
