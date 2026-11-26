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
    # Pin a non-macOS platform: on darwin, start() imports AppKit before Popen,
    # so a host without it would fail both launches before the mock is reached.
    with patch("aw_qt.manager.sys.platform", "linux"), patch.object(
        Module, "_probe_external_server", return_value=False
    ), patch(
        "aw_qt.manager.subprocess.Popen",
        side_effect=[error, MagicMock(returncode=None)],
    ) as popen:
        manager.autostart(["aw-watcher-afk", "aw-watcher-window"])

    assert popen.call_count == 2
    failed, started = [call.args[0][0] for call in popen.call_args_list]
    assert failed != started
    for module in manager.modules:
        assert module.started is (str(module.path) == started)


def test_deferred_launch_failure_releases_claims_and_starts_the_rest():
    names = ["aw-watcher-afk", "aw-watcher-window"]
    modules = [Module(name, Path(f"/bundled/{name}"), "bundled") for name in names]
    with patch.object(Manager, "discover_modules"):
        manager = Manager(testing=True)
    manager.modules = modules

    with patch("aw_qt.manager.sys.platform", "linux"), patch(
        "aw_qt.manager.SERVER_DEPENDENT_MODULES", set(names)
    ), patch("aw_qt.manager.wait_for_server", return_value=True), patch(
        "aw_qt.manager.subprocess.Popen",
        side_effect=[FileNotFoundError("missing"), MagicMock(returncode=None)],
    ) as popen:
        worker = manager.autostart(names, port=5600)
        assert worker is not None
        worker.join(timeout=5)

    assert popen.call_count == 2
    # A leaked claim would make every later start of that module a no-op.
    assert not manager._starting
    assert [module.started for module in modules] == [False, True]
