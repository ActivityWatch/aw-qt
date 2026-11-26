"""Tests for the launcher entry point."""

import importlib
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.mark.parametrize(
    "saved,selected,saved_port,selected_port",
    [
        ("aw-server", "aw-server-rust", 5601, 5602),
        ("aw-server-rust", "aw-server", 5602, 5601),
    ],
)
@pytest.mark.parametrize("profile", ["default", "testing"])
def test_cli_server_override_uses_selected_port(
    saved, selected, saved_port, selected_port, profile, monkeypatch
):
    # Register restoration even when main() writes directly to os.environ.
    monkeypatch.setenv("AW_PROFILE", "default")
    main_module = importlib.import_module("aw_qt.main")
    settings = MagicMock(
        autostart_modules=[saved, "aw-sync"],
        autostart_on_first_run=False,
        port=saved_port,
    )
    with patch.object(
        main_module, "AwQtSettings", return_value=settings
    ), patch.object(main_module, "Manager") as manager, patch.object(
        main_module, "_acquire_single_instance_lock"
    ), patch.object(main_module, "setup_logging"), patch.object(
        main_module.subprocess, "call"
    ), patch.object(main_module.os, "setpgrp", create=True), patch(
        "aw_qt.config._read_server_rust_port", return_value=5602
    ), patch("aw_qt.config._read_aw_server_port", return_value=5601), patch(
        "aw_qt.trayicon.run", return_value=0
    ) as run:
        result = CliRunner().invoke(
            main_module.main,
            ["--profile", profile, "--autostart-modules", f"{selected},aw-sync"],
        )
    assert result.exit_code == 0, result.output
    manager.return_value.autostart.assert_called_once_with(
        [selected, "aw-sync"], port=selected_port
    )
    assert run.call_args.kwargs["port"] == selected_port
    manager.return_value.autostart_notify_if_enabled.assert_not_called()


@pytest.mark.parametrize("selected", ["none", "aw-sync"])
@pytest.mark.parametrize("profile", ["default", "testing"])
@pytest.mark.parametrize("saved", ["aw-server", "aw-server-rust"])
def test_cli_server_free_override_preserves_configured_port(
    selected, profile, saved, monkeypatch
):
    monkeypatch.setenv("AW_PROFILE", "default")
    main_module = importlib.import_module("aw_qt.main")
    settings = MagicMock(
        autostart_modules=[saved, "aw-sync"],
        autostart_on_first_run=False,
        port=5678,
    )
    with patch.object(
        main_module, "AwQtSettings", return_value=settings
    ), patch.object(main_module, "Manager") as manager, patch.object(
        main_module, "_acquire_single_instance_lock"
    ), patch.object(main_module, "setup_logging"), patch.object(
        main_module.subprocess, "call"
    ), patch.object(main_module.os, "setpgrp", create=True), patch(
        "aw_qt.trayicon.run", return_value=0
    ) as run:
        result = CliRunner().invoke(
            main_module.main,
            ["--profile", profile, "--autostart-modules", selected],
        )
    assert result.exit_code == 0, result.output
    manager.return_value.autostart.assert_called_once_with(
        [] if selected == "none" else ["aw-sync"], port=5678
    )
    assert run.call_args.kwargs["port"] == 5678
    manager.return_value.autostart_notify_if_enabled.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX file descriptors")
@pytest.mark.parametrize(
    "broken_output, broken_fd",
    [("healthy", 0), ("terminal", 1), ("terminal", 2), ("pipe", 1), ("pipe", 2)],
    ids=["healthy", "terminal-stdout", "terminal-stderr", "pipe-stdout", "pipe-stderr"],
)
def test_startup_preserves_modules_and_gui_actions(tmp_path, broken_output, broken_fd):
    executable = tmp_path / "aw-test-output"
    ready = tmp_path / "ready"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, signal\n"
        "from pathlib import Path\n"
        "os.write(1, b'child stdout\\n')\n"
        "os.write(2, b'child stderr\\n')\n"
        f"Path({str(ready)!r}).touch()\n"
        "signal.pause()\n"
    )
    executable.chmod(0o755)

    launcher = """
import os
import pty
import sys
import time
from pathlib import Path
from unittest.mock import patch
import platformdirs
from aw_qt import main, trayicon

root = Path(sys.argv[1])
broken_fd = int(sys.argv[2])
broken_output = sys.argv[3]
if broken_output == "terminal":
    master, slave = pty.openpty()
    os.close(master)
    os.dup2(slave, broken_fd)
    os.close(slave)
elif broken_output == "pipe":
    reader, writer = os.pipe()
    os.close(reader)
    os.dup2(writer, broken_fd)
    os.close(writer)
# A real terminal makes parent stdout line-buffered, so GUI action prints
# must complete before their browser and shutdown effects can happen.
sys.stdout.reconfigure(line_buffering=True)

# XDG variables do not isolate macOS application directories.
for name, kind in (
    ("user_config_dir", "config"), ("user_data_dir", "data"),
    ("user_cache_dir", "cache"), ("user_log_dir", "logs"),
):
    setattr(platformdirs, name, lambda appname, *args, kind=kind, **kwargs: str(root / kind / appname))
platformdirs.user_cache_path = lambda appname, *args, **kwargs: root / "cache" / appname

def exercise_gui_actions(manager, **kwargs):
    module = next(m for m in manager.modules if m.name == "aw-test-output")
    try:
        deadline = time.monotonic() + 5
        while not (root / "ready").exists():
            assert module.is_alive(), "child failed before completing its writes"
            assert time.monotonic() < deadline, "child did not become ready"
            time.sleep(0.01)
        with patch.object(trayicon, "open_url") as open_url, patch.object(trayicon.QApplication, "quit") as quit_app:
            trayicon.open_webui("http://example.invalid")
            open_url.assert_called_once_with("http://example.invalid")
            trayicon.exit(manager)
            quit_app.assert_called_once_with()
            assert not module.is_alive()
        return 0
    finally:
        manager.stop_all()

trayicon.run = exercise_gui_actions
main(["--profile", "output-test", "--autostart-modules=aw-test-output"])
"""
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(
        [sys.executable, "-c", launcher, str(tmp_path), str(broken_fd), broken_output],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    if broken_fd != 1:
        assert "child stdout\n" in result.stdout
        assert "Opening dashboard\n" in result.stdout
        assert "Shutdown initiated, stopping all services...\n" in result.stdout
    if broken_fd != 2:
        assert "child stderr\n" in result.stderr
