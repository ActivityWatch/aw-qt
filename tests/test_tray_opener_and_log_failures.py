"""Opening a URL or reading a crashed module's log must not raise in a Qt slot."""

import subprocess
import sys
from pathlib import Path

import aw_core.log

from aw_qt import trayicon
from aw_qt.manager import Module


def test_open_url_without_xdg_open_falls_back_to_webbrowser(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("xdg-open")

    opened = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen", missing)
    monkeypatch.setattr(trayicon.webbrowser, "open", opened.append)

    started = []

    class InlineThread:
        def __init__(self, target, args, **kwargs):
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
    log.write_bytes(b"started\n\xff\xfe broken\n")
    monkeypatch.setattr(aw_core.log, "get_latest_log_file", lambda *a: str(log))

    text = Module("aw-watcher-afk", Path("/b/aw-watcher-afk"), "bundled").read_log(False)

    assert text == "started\n\ufffd\ufffd broken\n"
