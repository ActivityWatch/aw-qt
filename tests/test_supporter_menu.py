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
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
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
            state["reads"] = state.get("reads", 0) + 1
            body = state.get("raw", json.dumps(state["body"]).encode())
            entered.set()
            release.wait(5)
            self.send_response(state["status"])
            self.end_headers()
            try:
                self.wfile.write(body)
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
    "value,visible",
    [(True, False), (False, True), ("true", True), ({}, True)],
)
def test_support_item_visibility_follows_server(
    app, server, tray, value, visible
):
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


def test_failed_read_restores_fail_open_and_next_read_recovers(
    app, server, tray
):
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
    with patch.object(
        tray, "_refresh_support_action"
    ) as refresh, patch.object(tray, "_build_modulemenu"), patch(
        "aw_qt.trayicon.autostart.is_supported", return_value=False
    ):
        tray._build_rootmenu()
        refresh.assert_called_once_with()
        tray.contextMenu().aboutToShow.emit()
        assert refresh.call_count == 2


@pytest.fixture
def notify_tray(app, server):
    with patch.object(TrayIcon, "_build_rootmenu"):
        instance = TrayIcon(MagicMock(), QIcon(), port=server[0])
    instance._notify_action = QAction("Enable notifications", instance)
    instance._notify_action.setCheckable(True)
    instance._notify_action.triggered.connect(instance._on_notify_toggled)
    yield instance
    instance.deleteLater()
    app.processEvents()


@pytest.mark.parametrize(
    "status,body,before,after",
    [
        (200, {"enabled": True}, False, True),
        (200, {"enabled": False}, True, False),
        (404, {}, True, False),  # key not created yet: off
        (500, {}, True, True),  # unreadable: keep last value (aw-qt#158)
        (200, "not-a-dict", True, False),
    ],
)
def test_notify_item_follows_server_and_keeps_last_on_error(
    app, server, notify_tray, status, body, before, after
):
    server[1].update(status=status, body=body)
    server[3].set()
    notify_tray._notify_action.setChecked(before)
    notify_tray._refresh_notify_action()
    spin_until(app, lambda: notify_tray._notify_reply is None)
    assert notify_tray._notify_action.isChecked() is after
    assert server[1]["path"] == "/api/0/settings/aw-notify"


def test_notify_read_does_not_block_and_timeout_keeps_last(app, server, notify_tray):
    notify_tray._notify_action.setChecked(True)
    started = time.monotonic()
    notify_tray._refresh_notify_action()
    assert time.monotonic() - started < 0.25
    reply = notify_tray._notify_reply
    notify_tray._refresh_notify_action()
    assert notify_tray._notify_reply is reply
    spin_until(app, lambda: notify_tray._notify_reply is None)
    assert not server[3].is_set()
    assert notify_tray._notify_action.isChecked()


@pytest.mark.parametrize("write_succeeded", [True, False])
def test_toggle_during_read_rechecks_saved_state(
    app, server, notify_tray, write_succeeded
):
    server[1]["body"] = {"enabled": False}
    notify_tray._notify_action.setChecked(False)
    notify_tray._refresh_notify_action()
    spin_until(app, lambda: server[2].is_set())

    def write(port, enabled):
        assert port == server[0]
        assert enabled is True
        server[1]["body"] = {"enabled": True}
        # A write may reach the server even if its response times out.
        return write_succeeded

    with patch("aw_qt.trayicon.write_notify_enabled", side_effect=write), patch(
        "aw_qt.trayicon.QMessageBox"
    ):
        notify_tray._notify_action.trigger()
    assert notify_tray._notify_action.isChecked() is write_succeeded
    checked_changes = []
    notify_tray._notify_action.toggled.connect(checked_changes.append)
    server[3].set()
    spin_until(app, lambda: notify_tray._notify_reply is None)
    assert server[1]["reads"] == 2
    assert notify_tray._notify_action.isChecked()
    if write_succeeded:
        assert not checked_changes
    else:
        assert checked_changes == [True]


@pytest.mark.parametrize("raw", [b"not-json", b"\xff"])
def test_notify_malformed_response_keeps_last_value(app, server, notify_tray, raw):
    server[1]["raw"] = raw
    server[3].set()
    notify_tray._notify_action.setChecked(True)
    notify_tray._refresh_notify_action()
    spin_until(app, lambda: notify_tray._notify_reply is None)
    assert notify_tray._notify_action.isChecked()
