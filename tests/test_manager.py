"""Unit tests for the Module manager."""

import os
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


class TestModuleServerProbe:
    def test_aw_server_uses_python_server_port(self):
        mod = Module("aw-server", Path("/usr/bin/aw-server"), "system")

        with (
            patch("aw_qt.config._read_aw_server_port", return_value=5601),
            patch("aw_qt.config._read_server_rust_port", return_value=6601),
        ):
            assert mod._get_server_port(testing=False) == 5601

    def test_aw_server_rust_uses_rust_server_port(self):
        mod = Module("aw-server-rust", Path("/usr/bin/aw-server-rust"), "system")

        with (
            patch("aw_qt.config._read_aw_server_port", return_value=5601),
            patch("aw_qt.config._read_server_rust_port", return_value=6601),
        ):
            assert mod._get_server_port(testing=False) == 6601

    def test_probe_external_server_closes_response(self):
        mod = Module("aw-server", Path("/usr/bin/aw-server"), "system")
        response = MagicMock()

        with (
            patch.object(mod, "_get_server_port", return_value=5600),
            patch("urllib.request.urlopen", return_value=response) as urlopen,
        ):
            assert mod._probe_external_server(testing=False) is True

        urlopen.assert_called_once_with("http://localhost:5600/api/0/info", timeout=0.2)
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once()

    def test_probe_external_server_allows_custom_timeout(self):
        mod = Module("aw-server", Path("/usr/bin/aw-server"), "system")
        response = MagicMock()

        with (
            patch.object(mod, "_get_server_port", return_value=5600),
            patch("urllib.request.urlopen", return_value=response) as urlopen,
        ):
            assert mod._probe_external_server(testing=False, timeout=1.0) is True

        urlopen.assert_called_once_with("http://localhost:5600/api/0/info", timeout=1.0)

    def test_probe_external_server_cached_reuses_recent_result(self):
        mod = Module("aw-server", Path("/usr/bin/aw-server"), "system")

        with (
            patch("aw_qt.manager.monotonic", side_effect=[10.0, 10.4]),
            patch.object(mod, "_probe_external_server", return_value=True) as probe,
        ):
            assert mod._probe_external_server_cached(testing=True, max_age=1.0) is True
            assert mod._probe_external_server_cached(testing=True, max_age=1.0) is True

        assert probe.call_count == 1

    def test_probe_external_server_cached_refreshes_after_ttl(self):
        mod = Module("aw-server", Path("/usr/bin/aw-server"), "system")

        with (
            patch("aw_qt.manager.monotonic", side_effect=[10.0, 10.4, 11.5]),
            patch.object(mod, "_probe_external_server", side_effect=[True, False]) as probe,
        ):
            assert mod._probe_external_server_cached(testing=True, max_age=1.0) is True
            assert mod._probe_external_server_cached(testing=True, max_age=1.0) is True
            assert mod._probe_external_server_cached(testing=True, max_age=1.0) is False

        assert probe.call_count == 2


class TestModuleStart:
    def test_start_uses_longer_timeout_for_external_server_probe(self):
        mod = Module("aw-server", Path("/usr/bin/aw-server"), "system")

        with (
            patch.object(mod, "_probe_external_server", return_value=True) as probe,
            patch.object(mod, "_get_server_port", return_value=5600),
        ):
            mod.start(testing=False)

        probe.assert_called_once_with(False, timeout=1.0)
        assert mod.started is True
        assert mod._external_server is True


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

    def test_is_alive_reprobes_external_server(self):
        mod = Module("aw-server", Path("/usr/bin/aw-server"), "system")
        mod._external_server = True
        mod._external_server_testing = True
        mod.started = True

        with (
            patch("aw_qt.manager.monotonic", side_effect=[10.0, 11.5]),
            patch.object(mod, "_probe_external_server", side_effect=[True, False]) as probe,
        ):
            assert mod.is_alive()
            assert mod.is_alive() is False
            assert mod.started is True
            assert mod._external_server is False
            assert mod._external_server_testing is False

        assert probe.call_args_list[0].args == (True,)
        assert probe.call_args_list[1].args == (True,)

    def test_stop_external_server_resets_state_without_terminating_process(self):
        mod = Module("aw-server", Path("/usr/bin/aw-server"), "system")
        mod.started = True
        mod._external_server = True
        mod._external_server_testing = True
        mock_proc = MagicMock(spec=subprocess.Popen)
        mod._process = mock_proc
        mod._last_process = None
        mod._external_server_probe_cache = True
        mod._external_server_probe_cache_at = 10.0

        mod.stop()

        mock_proc.terminate.assert_not_called()
        mock_proc.wait.assert_not_called()
        assert mod.started is False
        assert mod._external_server is False
        assert mod._external_server_testing is False
        assert mod._external_server_probe_cache is None
        assert mod._external_server_probe_cache_at == 0.0
        assert mod._last_process is None
        assert mod._process is mock_proc


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
        assert os.path.expanduser("~/.local/bin") in searched_paths, (
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
