"""Unit tests for the Module manager."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
