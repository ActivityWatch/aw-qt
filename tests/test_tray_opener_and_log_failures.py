"""Opening a URL or reading a crashed module's log must not raise in a Qt slot."""

import os
import subprocess
import sys
from pathlib import Path

import aw_core.log
import pytest

from aw_qt import trayicon
from aw_qt.manager import Module


def test_open_url_without_xdg_open_falls_back_to_webbrowser(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("xdg-open")

    opened = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen", missing)
    monkeypatch.setattr(trayicon, "_open_with_webbrowser", opened.append)

    started = []

    class InlineThread:
        def __init__(self, target, args, **kwargs):
            assert kwargs.get("daemon") is True
            self._run = lambda: target(*args)

        def start(self):
            started.append(True)
            self._run()

    monkeypatch.setattr(trayicon.threading, "Thread", InlineThread)

    trayicon.open_url("http://localhost:5600")

    # The fallback runs off the Qt thread: webbrowser.open() may block.
    assert started == [True]
    assert opened == ["http://localhost:5600"]


def test_read_log_tolerates_undecodable_bytes(monkeypatch, tmp_path):
    # e.g. a UTF-8 Rust log read with Windows' cp1252 locale encoding, or a
    # log holding a truncated multi-byte sequence. The crash dialog shows it.
    log = tmp_path / "aw-watcher-afk.log"
    log.write_bytes("started: café\n".encode("utf-8") + b"\xff\xfe broken\n")
    monkeypatch.setattr(aw_core.log, "get_latest_log_file", lambda *a: str(log))

    def open_with_cp1252_default(path, *, encoding=None, **kwargs):
        return open(path, encoding=encoding or "cp1252", **kwargs)

    monkeypatch.setattr("aw_qt.manager.open", open_with_cp1252_default, raising=False)
    text = Module("aw-watcher-afk", Path("/b/aw-watcher-afk"), "bundled").read_log(False)

    assert text == "started: café\n\ufffd\ufffd broken\n"


@pytest.mark.parametrize("appimage", [False, True])
@pytest.mark.parametrize("frozen", [False, True])
def test_webbrowser_fallback_uses_clean_child_environment(monkeypatch, appimage, frozen):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/lib")
    if appimage:
        monkeypatch.setenv("APPIMAGE", "/downloads/ActivityWatch.AppImage")
        monkeypatch.setenv("QT_PLUGIN_PATH", "/bundle/plugins")
    parent = dict(os.environ)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert dict(os.environ) == parent
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    url = "http://localhost:5600/?q=a'b"
    trayicon._open_with_webbrowser(url)

    command, kwargs = calls[0]
    assert command[0] == ("python3" if frozen else sys.executable)
    assert command[1] == "-c"
    assert command[-1] == url
    assert kwargs["env"] == trayicon.get_env()
    assert dict(os.environ) == parent


def test_webbrowser_fallback_failure_is_logged(monkeypatch, caplog):
    # The fallback runs on a worker thread: its failure must reach the log
    # rather than vanish as an unhandled thread exception.
    def broken(*args, **kwargs):
        raise FileNotFoundError("python3")

    monkeypatch.setattr(subprocess, "run", broken)
    trayicon._open_with_webbrowser("http://localhost:5600")
    assert "Failed to open http://localhost:5600" in caplog.text

    caplog.clear()
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 3)
    )
    trayicon._open_with_webbrowser("http://localhost:5600")
    assert "No browser found to open http://localhost:5600" in caplog.text


@pytest.mark.parametrize("helper_fails", [False, True])
def test_webbrowser_helper_failure_is_not_a_missing_browser(
    monkeypatch, tmp_path, caplog, helper_fails
):
    # Exercise the child protocol too: a Python exception must not use the
    # exit code reserved for browser discovery returning False.
    (tmp_path / "webbrowser.py").write_text(
        "def open(url):\n"
        + ("    raise RuntimeError('helper failed')\n" if helper_fails else "    return False\n")
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    trayicon._open_with_webbrowser("http://localhost:5600")

    if helper_fails:
        assert "Browser helper exited with status 1" in caplog.text
        assert "No browser found" not in caplog.text
    else:
        assert "No browser found to open http://localhost:5600" in caplog.text


@pytest.mark.parametrize("outcome", ["raises", 3, 1])
def test_webbrowser_fallback_does_not_log_dashboard_token(monkeypatch, caplog, outcome):
    # The authenticated dashboard URL carries the API key in its query string.
    def run(command, **kwargs):
        if outcome == "raises":
            raise FileNotFoundError("python3")
        return subprocess.CompletedProcess(command, outcome)

    monkeypatch.setattr(subprocess, "run", run)
    trayicon._open_with_webbrowser("http://127.0.0.1:5600?token=sekrit-key")

    assert "http://127.0.0.1:5600" in caplog.text
    assert "sekrit-key" not in caplog.text
