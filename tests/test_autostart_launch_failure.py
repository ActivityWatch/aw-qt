"""A module that fails to launch must not keep the other modules from starting."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aw_qt.manager import Manager, Module


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing interpreter"),
        # e.g. `import AppKit` failing on macOS, or a malformed command list
        ImportError("No module named 'AppKit'"),
        ValueError("embedded null byte"),
    ],
)
def test_autostart_continues_past_module_that_fails_to_launch(error):
    afk = Module("aw-watcher-afk", Path("/bundled/aw-watcher-afk"), "bundled")
    window = Module("aw-watcher-window", Path("/bundled/aw-watcher-window"), "bundled")
    with patch.object(Manager, "discover_modules"):
        manager = Manager(testing=True)
    manager.modules = [afk, window]

    # autostart iterates a set: fail the first launch regardless of its name.
    with patch.object(Module, "_probe_external_server", return_value=False), patch(
        "aw_qt.manager.subprocess.Popen",
        side_effect=[error, MagicMock(returncode=None)],
    ) as popen:
        manager.autostart(["aw-watcher-afk", "aw-watcher-window"])

    assert popen.call_count == 2
    failed, started = [call.args[0][0] for call in popen.call_args_list]
    assert failed != started
    for module in manager.modules:
        assert module.started is (str(module.path) == started)
