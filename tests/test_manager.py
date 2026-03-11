"""Unit tests for the Module manager."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import aw_qt.manager as manager_module
from aw_qt.manager import Module


@pytest.fixture
def module():
    """Create a test module with a mock path."""
    return Module("aw-test-module", Path("/usr/bin/true"), "system")


class TestModuleToggle:
    """Tests for Module.toggle() behavior, especially with crashed processes."""

    def test_toggle_starts_stopped_module(self, module):
        """Toggle should start a module that was never started."""
        assert not module.started
        assert not module.is_alive()

        with patch.object(module, "start") as mock_start:
            module.toggle(testing=True)
            mock_start.assert_called_once_with(True)

    def test_toggle_stops_running_module(self, module):
        """Toggle should stop a module that is running."""
        # Simulate a running process
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.returncode = None  # None means still running

        # After terminate+wait, process should report as dead
        def fake_wait():
            mock_proc.returncode = -15  # SIGTERM

        mock_proc.wait.side_effect = fake_wait
        module._process = mock_proc
        module.started = True

        module.toggle(testing=True)
        assert not module.started
        assert module._process is None

    def test_toggle_restarts_crashed_module(self, module):
        """Toggle should restart a module that crashed (started=True, process dead).

        This is the key fix: previously, toggle checked `self.started` instead of
        `self.is_alive()`, so the first click would only call stop() (cleaning up
        state) without starting, requiring a second click to actually start.
        """
        # Simulate a crashed process: started=True but process exited
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.returncode = 1  # Non-zero = exited
        module._process = mock_proc
        module.started = True

        assert not module.is_alive()  # Process is dead
        assert module.started  # But flag says started

        with patch.object(module, "start") as mock_start:
            module.toggle(testing=True)
            # Should have cleaned up and started in one toggle
            mock_start.assert_called_once_with(True)
            assert not module.started  # stop() was called to clean up


class TestModuleIsAlive:
    """Tests for Module.is_alive() behavior."""

    def test_is_alive_no_process(self, module):
        assert not module.is_alive()

    def test_is_alive_running(self, module):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.returncode = None
        module._process = mock_proc
        assert module.is_alive()

    def test_is_alive_exited(self, module):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.returncode = 0
        module._process = mock_proc
        assert not module.is_alive()


class TestGetUnexpectedStops:
    """Tests for Manager.get_unexpected_stops()."""

    def test_detects_crashed_module(self):
        from aw_qt.manager import Manager

        with patch.object(Manager, "discover_modules"):
            mgr = Manager(testing=True)

        mod = Module("aw-test", Path("/usr/bin/true"), "system")
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.returncode = 1
        mod._process = mock_proc
        mod.started = True
        mgr.modules = [mod]

        unexpected = mgr.get_unexpected_stops()
        assert len(unexpected) == 1
        assert unexpected[0].name == "aw-test"

    def test_ignores_intentionally_stopped(self):
        from aw_qt.manager import Manager

        with patch.object(Manager, "discover_modules"):
            mgr = Manager(testing=True)

        mod = Module("aw-test", Path("/usr/bin/true"), "system")
        mod.started = False  # Intentionally stopped
        mgr.modules = [mod]

        unexpected = mgr.get_unexpected_stops()
        assert len(unexpected) == 0


class TestMacOSSystemPathDiscovery:
    """Tests for macOS-specific path augmentation in _discover_modules_system().

    When aw-qt is launched from Finder on macOS, PATH is minimal (/usr/bin:/bin:...)
    and doesn't include directories where AW modules are typically installed.
    The fix adds common macOS binary paths so modules can be found regardless
    of how aw-qt was launched.
    """

    def test_macos_adds_extra_paths_when_not_in_path(self):
        """On macOS, common install dirs are searched even if absent from PATH."""
        from aw_qt.manager import _discover_modules_system

        searched_paths: list[str] = []

        def fake_listdir(path: str) -> list[str]:
            searched_paths.append(path)
            return []  # no modules — we just want to see what paths were searched

        # Simulate minimal macOS Finder PATH
        minimal_path = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]

        with (
            patch.object(manager_module.platform, "system", return_value="Darwin"),
            patch("os.get_exec_path", return_value=list(minimal_path)),
            patch("os.path.isdir", return_value=True),
            patch("os.listdir", fake_listdir),
        ):
            _discover_modules_system()

        assert "/opt/homebrew/bin" in searched_paths, (
            "Homebrew (Apple Silicon) path should be searched on macOS"
        )
        assert "/usr/local/bin" in searched_paths, (
            "Homebrew (Intel) / pip global path should be searched on macOS"
        )
        # ~/.local/bin (expanduser result) should also be searched
        assert any("local/bin" in p for p in searched_paths), (
            "~/.local/bin (pip --user) should be searched on macOS"
        )

    def test_macos_does_not_duplicate_existing_path_entries(self):
        """Extra macOS paths should not be added if they're already in PATH."""
        from aw_qt.manager import _discover_modules_system

        searched_paths: list[str] = []

        def fake_listdir(path: str) -> list[str]:
            searched_paths.append(path)
            return []

        # PATH already includes the homebrew paths
        full_path = ["/usr/bin", "/bin", "/opt/homebrew/bin", "/usr/local/bin"]

        with (
            patch.object(manager_module.platform, "system", return_value="Darwin"),
            patch("os.get_exec_path", return_value=list(full_path)),
            patch("os.path.isdir", return_value=True),
            patch("os.listdir", fake_listdir),
        ):
            _discover_modules_system()

        # Each path should appear exactly once (no duplicates)
        homebrew_count = searched_paths.count("/opt/homebrew/bin")
        assert homebrew_count == 1, (
            f"/opt/homebrew/bin should appear exactly once, got {homebrew_count}"
        )

    def test_non_macos_does_not_add_extra_paths(self):
        """On non-macOS platforms, no extra paths should be added."""
        from aw_qt.manager import _discover_modules_system

        searched_paths: list[str] = []

        def fake_listdir(path: str) -> list[str]:
            searched_paths.append(path)
            return []

        minimal_path = ["/usr/bin", "/bin"]

        with (
            patch.object(manager_module.platform, "system", return_value="Linux"),
            patch("os.get_exec_path", return_value=list(minimal_path)),
            patch("os.path.isdir", return_value=True),
            patch("os.listdir", fake_listdir),
        ):
            _discover_modules_system()

        assert "/opt/homebrew/bin" not in searched_paths, (
            "Homebrew path should NOT be added on Linux"
        )
