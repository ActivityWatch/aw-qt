"""Unit tests for OS-level autostart (login item) management.

The OS layer is mocked throughout, so these tests never touch the real home
directory or registry and run on any platform.
"""

import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest

from aw_qt import autostart


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point the autostart module at a throwaway home directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    with patch.object(autostart, "_home", return_value=home):
        yield home


class TestPlatformSelection:
    """Tests for mapping sys.platform onto a backend."""

    @pytest.mark.parametrize(
        "platform_name,expected",
        [
            ("linux", "linux"),
            ("linux2", "linux"),
            ("freebsd13", "linux"),
            ("darwin", "darwin"),
            ("win32", "windows"),
        ],
    )
    def test_supported_platforms(self, platform_name, expected):
        assert autostart._platform_key(platform_name) == expected
        assert autostart.is_supported(platform_name)

    @pytest.mark.parametrize("platform_name", ["aix", "emscripten", "cygwin", ""])
    def test_unsupported_platforms(self, platform_name):
        assert autostart._platform_key(platform_name) is None
        assert not autostart.is_supported(platform_name)

    def test_backend_functions_match_platform(self):
        assert autostart._get_backend("linux").enable is autostart._linux_enable
        assert autostart._get_backend("darwin").enable is autostart._macos_enable
        assert autostart._get_backend("win32").enable is autostart._windows_enable

    def test_default_platform_is_current(self):
        import sys

        assert autostart._platform_key() == autostart._platform_key(sys.platform)

    def test_enable_raises_on_unsupported_platform(self):
        with patch.object(autostart, "_get_backend", return_value=None):
            with pytest.raises(autostart.AutostartError):
                autostart.enable()
            with pytest.raises(autostart.AutostartError):
                autostart.disable()
            # is_enabled must never raise
            assert autostart.is_enabled() is False


class TestPublicApiErrorHandling:
    def test_is_enabled_never_raises(self):
        backend = autostart._Backend(
            is_enabled=lambda: (_ for _ in ()).throw(OSError("boom")),
            enable=lambda: None,
            disable=lambda: None,
        )
        with patch.object(autostart, "_get_backend", return_value=backend):
            assert autostart.is_enabled() is False

    def test_backend_errors_become_autostart_errors(self):
        def _boom():
            raise OSError("read-only file system")

        backend = autostart._Backend(
            is_enabled=lambda: False, enable=_boom, disable=_boom
        )
        with patch.object(autostart, "_get_backend", return_value=backend):
            with pytest.raises(autostart.AutostartError, match="read-only"):
                autostart.enable()
            with pytest.raises(autostart.AutostartError, match="read-only"):
                autostart.disable()


class TestLinuxBackend:
    def test_uses_xdg_config_home(self, fake_home, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert autostart._linux_desktop_path() == (
            tmp_path / "xdg" / "autostart" / "aw-qt.desktop"
        )

    def test_falls_back_to_dot_config(self, fake_home):
        assert autostart._linux_desktop_path() == (
            fake_home / ".config" / "autostart" / "aw-qt.desktop"
        )

    def test_enable_creates_desktop_file(self, fake_home):
        assert not autostart._linux_is_enabled()

        autostart._linux_enable()

        path = autostart._linux_desktop_path()
        assert path.is_file()
        contents = path.read_text()
        assert "[Desktop Entry]" in contents
        assert "Type=Application" in contents
        assert any(line.startswith("Exec=") for line in contents.splitlines())
        assert autostart._linux_is_enabled()

    def test_enable_is_idempotent(self, fake_home):
        autostart._linux_enable()
        first = autostart._linux_desktop_path().read_text()
        autostart._linux_enable()
        assert autostart._linux_desktop_path().read_text() == first
        assert autostart._linux_is_enabled()

    def test_disable_removes_desktop_file(self, fake_home):
        autostart._linux_enable()
        autostart._linux_disable()
        assert not autostart._linux_desktop_path().exists()
        assert not autostart._linux_is_enabled()

    def test_disable_is_idempotent(self, fake_home):
        # Disabling when nothing is enabled must not raise
        autostart._linux_disable()
        autostart._linux_disable()
        assert not autostart._linux_is_enabled()

    def test_enable_reenables_hidden_entry(self, fake_home):
        """A desktop environment may disable the entry with Hidden=true."""
        path = autostart._linux_desktop_path()
        path.parent.mkdir(parents=True)
        path.write_text("[Desktop Entry]\nType=Application\nExec=aw-qt\nHidden=true\n")
        assert not autostart._linux_is_enabled()

        autostart._linux_enable()

        assert autostart._linux_is_enabled()
        assert "Hidden=false" in path.read_text()

    def test_gnome_autostart_disabled_reads_as_disabled(self, fake_home):
        path = autostart._linux_desktop_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            "[Desktop Entry]\nType=Application\nExec=aw-qt\n"
            "X-GNOME-Autostart-enabled=false\n"
        )
        assert not autostart._linux_is_enabled()

    def test_reuses_packaged_desktop_file(self, fake_home, tmp_path):
        packaged = tmp_path / "aw-qt.desktop"
        packaged.write_text(
            "[Desktop Entry]\nName=ActivityWatch\nExec=aw-qt\nIcon=activitywatch\n"
        )
        with patch.object(autostart, "_bundled_desktop_file", return_value=packaged):
            with patch.object(
                autostart, "_command", return_value=["/opt/aw/aw-qt", "--flag"]
            ):
                autostart._linux_enable()

        contents = autostart._linux_desktop_path().read_text()
        assert "Icon=activitywatch" in contents
        assert "Exec=/opt/aw/aw-qt --flag" in contents

    def test_exec_value_is_quoted(self):
        with patch.object(
            autostart, "_command", return_value=["/path with space/aw-qt"]
        ):
            assert autostart._desktop_exec_value() == "'/path with space/aw-qt'"


class TestMacosBackend:
    def test_plist_path(self, fake_home):
        assert autostart._macos_plist_path() == (
            fake_home / "Library" / "LaunchAgents" / "net.activitywatch.aw-qt.plist"
        )

    def test_enable_writes_launch_agent(self, fake_home):
        assert not autostart._macos_is_enabled()

        with patch.object(autostart, "_command", return_value=["/opt/aw/aw-qt"]):
            autostart._macos_enable()

        path = autostart._macos_plist_path()
        assert path.is_file()
        with path.open("rb") as f:
            plist = plistlib.load(f)
        assert plist["Label"] == "net.activitywatch.aw-qt"
        assert plist["RunAtLoad"] is True
        assert plist["ProgramArguments"] == ["/opt/aw/aw-qt"]
        assert autostart._macos_is_enabled()

    def test_enable_is_idempotent(self, fake_home):
        with patch.object(autostart, "_command", return_value=["/opt/aw/aw-qt"]):
            autostart._macos_enable()
            first = autostart._macos_plist_path().read_bytes()
            autostart._macos_enable()
        assert autostart._macos_plist_path().read_bytes() == first
        assert autostart._macos_is_enabled()

    def test_disable_removes_launch_agent(self, fake_home):
        autostart._macos_enable()
        autostart._macos_disable()
        assert not autostart._macos_plist_path().exists()
        assert not autostart._macos_is_enabled()

    def test_disable_is_idempotent(self, fake_home):
        autostart._macos_disable()
        autostart._macos_disable()
        assert not autostart._macos_is_enabled()

    def test_no_launchctl_invocation(self, fake_home):
        """Loading/unloading would duplicate or kill the running instance."""
        with patch("subprocess.run") as run, patch("subprocess.call") as call, patch(
            "subprocess.Popen"
        ) as popen:
            autostart._macos_enable()
            autostart._macos_disable()
        run.assert_not_called()
        call.assert_not_called()
        popen.assert_not_called()


class TestWindowsBackend:
    """The registry/filesystem leaves are mocked; this tests the precedence logic."""

    def test_enabled_via_run_key(self):
        with patch.object(autostart, "_windows_startup_shortcut", return_value=None):
            with patch.object(
                autostart, "_windows_run_value", return_value='"C:\\aw\\aw-qt.exe"'
            ):
                assert autostart._windows_is_enabled()

    def test_enabled_via_installer_shortcut(self):
        """Users who ticked the installer checkbox must read as enabled."""
        shortcut = Path("C:/Startup/ActivityWatch.lnk")
        with patch.object(
            autostart, "_windows_startup_shortcut", return_value=shortcut
        ):
            with patch.object(
                autostart, "_windows_run_value", return_value=None
            ) as run_value:
                assert autostart._windows_is_enabled()
            run_value.assert_not_called()

    def test_disabled_when_neither_present(self):
        with patch.object(autostart, "_windows_startup_shortcut", return_value=None):
            with patch.object(autostart, "_windows_run_value", return_value=None):
                assert not autostart._windows_is_enabled()

    def test_enable_writes_run_key(self):
        with patch.object(autostart, "_windows_startup_shortcut", return_value=None):
            with patch.object(
                autostart, "_command", return_value=["C:\\aw\\aw-qt.exe"]
            ):
                with patch.object(autostart, "_windows_set_run_value") as set_value:
                    autostart._windows_enable()
        set_value.assert_called_once()
        assert "aw-qt.exe" in set_value.call_args[0][0]

    def test_enable_skips_run_key_when_shortcut_exists(self):
        """Writing both would start aw-qt twice at login."""
        shortcut = Path("C:/Startup/ActivityWatch.lnk")
        with patch.object(
            autostart, "_windows_startup_shortcut", return_value=shortcut
        ):
            with patch.object(autostart, "_windows_set_run_value") as set_value:
                autostart._windows_enable()
        set_value.assert_not_called()

    def test_disable_removes_both_locations(self):
        with patch.object(autostart, "_windows_delete_run_value") as del_value:
            with patch.object(
                autostart, "_windows_delete_startup_shortcut"
            ) as del_shortcut:
                autostart._windows_disable()
        del_value.assert_called_once()
        del_shortcut.assert_called_once()

    def test_disable_is_idempotent(self):
        """Neither location present: no errors, nothing to remove."""
        with patch.object(autostart, "_windows_startup_shortcut", return_value=None):
            with patch.object(autostart, "_windows_run_value", return_value=None):
                # The winreg-backed helpers are no-ops off Windows
                autostart._windows_disable()
                autostart._windows_disable()
                assert not autostart._windows_is_enabled()


class TestCommand:
    def test_frozen_bundle_uses_executable(self):
        with patch.object(autostart.sys, "frozen", True, create=True):
            with patch.object(autostart.sys, "executable", "/Applications/aw-qt"):
                assert autostart._command() == ["/Applications/aw-qt"]

    def test_prefers_aw_qt_on_path(self):
        with patch("shutil.which", return_value="/usr/local/bin/aw-qt"):
            assert autostart._command() == ["/usr/local/bin/aw-qt"]

    def test_falls_back_to_module_invocation(self):
        with patch("shutil.which", return_value=None):
            assert autostart._command() == [autostart.sys.executable, "-m", "aw_qt"]
