"""Tests for the launcher entry point."""

import os
import subprocess
import sys

import pytest


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
