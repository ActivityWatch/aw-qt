"""Supporter reads must not block the tray's Qt event loop."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import QtCore
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication

from aw_qt.trayicon import TrayIcon


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def server():
    state = {"body": True, "status": 200}
    entered = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["path"] = self.path
            entered.set()
            release.wait(5)
            self.send_response(state["status"])
            self.end_headers()
            try:
                self.wfile.write(state.get("raw", json.dumps(state["body"]).encode()))
            except BrokenPipeError:
                pass  # A timeout may have closed the client connection.

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_port, state, entered, release
    release.set()
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def spin_until(app, predicate):
    deadline = QtCore.QDeadlineTimer(3000)
    while not predicate() and not deadline.hasExpired():
        app.processEvents()
        threading.Event().wait(0.001)
    assert predicate()


@pytest.fixture
def tray(app, server):
    with patch.object(TrayIcon, "_build_rootmenu"):
        instance = TrayIcon(MagicMock(), QIcon(), port=server[0])
    instance._support_action = QAction("Support ActivityWatch", instance)
    yield instance
    instance.deleteLater()
    app.processEvents()


@pytest.mark.parametrize(
    "value,visible", [(True, False), (False, True), ("true", True), ({}, True)]
)
def test_support_item_visibility_follows_server(app, server, tray, value, visible):
    server[1]["body"] = value
    server[3].set()
    tray._refresh_support_action()
    spin_until(app, lambda: tray._support_reply is None)
    assert tray._support_action.isVisible() is visible
    assert server[1]["path"] == "/api/0/settings/supporter"


def test_slow_server_does_not_block_or_duplicate_reads(app, server, tray):
    # Timer must run while the server deliberately withholds its response.
    ticked = []
    QtCore.QTimer.singleShot(0, lambda: ticked.append(True))
    started = time.monotonic()
    tray._refresh_support_action()
    assert time.monotonic() - started < 0.25
    reply = tray._support_reply
    tray._refresh_support_action()
    assert tray._support_reply is reply
    spin_until(app, lambda: bool(ticked) and server[2].is_set())
    assert not server[3].is_set()
    assert tray._support_action.isVisible()
    server[3].set()
    spin_until(app, lambda: tray._support_reply is None)
    assert not tray._support_action.isVisible()


def test_failed_read_restores_fail_open_and_next_read_recovers(app, server, tray):
    tray._support_action.setVisible(False)
    server[1]["status"] = 404
    server[3].set()
    tray._refresh_support_action()
    spin_until(app, lambda: tray._support_reply is None)
    assert tray._support_action.isVisible()
    server[1]["status"] = 200
    tray._refresh_support_action()
    spin_until(app, lambda: tray._support_reply is None)
    assert not tray._support_action.isVisible()


@pytest.mark.parametrize("raw", [b"not-json", b"\xff"])
def test_malformed_response_fails_open(app, server, tray, raw):
    tray._support_action.setVisible(False)
    server[1]["raw"] = raw
    server[3].set()
    tray._refresh_support_action()
    spin_until(app, lambda: tray._support_reply is None)
    assert tray._support_action.isVisible()


def test_timeout_fails_open_and_allows_retry(app, server, tray):
    tray._support_action.setVisible(False)
    tray._refresh_support_action()
    spin_until(app, lambda: tray._support_reply is None)
    assert server[2].is_set()
    assert not server[3].is_set()
    assert tray._support_action.isVisible()
    server[3].set()
    tray._refresh_support_action()
    spin_until(app, lambda: tray._support_reply is None)
    assert not tray._support_action.isVisible()


def test_missing_action_does_not_start_a_read(tray):
    tray._support_action = None
    tray._refresh_support_action()
    assert tray._support_reply is None


def test_build_rootmenu_starts_read_and_rechecks_on_open(tray):
    tray.manager.modules = []
    with patch.object(tray, "_refresh_support_action") as refresh, patch.object(
        tray, "_build_modulemenu"
    ), patch("aw_qt.trayicon.autostart.is_supported", return_value=False):
        tray._build_rootmenu()
        refresh.assert_called_once_with()
        tray.contextMenu().aboutToShow.emit()
        assert refresh.call_count == 2
