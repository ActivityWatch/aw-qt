"""A module that fails to launch must not keep the other modules from starting."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from aw_qt.manager import Manager, Module


def test_autostart_continues_past_module_that_fails_to_launch():
    afk = Module("aw-watcher-afk", Path("/bundled/aw-watcher-afk"), "bundled")
    window = Module("aw-watcher-window", Path("/bundled/aw-watcher-window"), "bundled")
    with patch.object(Manager, "discover_modules"):
        manager = Manager(testing=True)
    manager.modules = [afk, window]

    def popen(cmd, **kwargs):
        if "afk" in cmd[0]:
            raise FileNotFoundError(cmd[0])
        return MagicMock(returncode=None)

    with patch.object(Module, "_probe_external_server", return_value=False), patch(
        "aw_qt.manager.subprocess.Popen", side_effect=popen
    ):
        manager.autostart(["aw-watcher-afk", "aw-watcher-window"])

    assert window.started is True
    assert afk.started is False
