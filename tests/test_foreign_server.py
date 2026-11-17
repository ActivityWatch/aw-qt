"""aw-qt must not adopt a server that serves another datastore.

On a multiuser machine, user B's aw-qt finds user A's server on the shared
default port. Adopting it sends B's watcher data into A's database
(ActivityWatch/activitywatch#75). The listener's ``/api/0/info`` device_id
identifies its datastore, so a mismatch with B's own datastore is refused.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

import aw_qt.manager as manager_module
from aw_qt.manager import Manager, Module, _local_server_device_ids


class _InfoHandler(BaseHTTPRequestHandler):
    device_id: Optional[str] = None

    def do_GET(self):
        if self.path != "/api/0/info":
            self.send_response(404)
            self.end_headers()
            return
        info = {"hostname": "host", "version": "v0.14.1", "testing": False}
        if self.device_id is not None:
            info["device_id"] = self.device_id
        body = json.dumps(info).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def info_server():
    server = HTTPServer(("127.0.0.1", 0), _InfoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    _InfoHandler.device_id = None


@pytest.fixture(autouse=True)
def _default_profile(monkeypatch):
    monkeypatch.delenv("AW_PROFILE", raising=False)


def _write_device_id(root: Path, appname: str, server: str, device_id: str) -> None:
    path = root / "data" / appname / server / "device_id"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(device_id)


def _start_server_module(port: int) -> tuple[Module, object]:
    mod = Module("aw-server-rust", Path("/nonexistent/aw-server-rust"), "system")
    with (
        patch.object(mod, "_get_server_port", return_value=port),
        patch("subprocess.Popen") as popen,
    ):
        mod.start(testing=False)
    return mod, popen


class TestAdoption:
    def test_refuses_listener_with_another_datastore(
        self, info_server, _isolated_platform_dirs
    ):
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "mine")
        _InfoHandler.device_id = "theirs"

        mod, popen = _start_server_module(info_server.server_port)

        assert mod.started is False
        assert mod._external_server is False
        assert mod.foreign_server_port == info_server.server_port
        # Spawning our own server would only fail with AddrInUse.
        popen.assert_not_called()

    def test_adopts_listener_with_own_datastore(self, info_server, _isolated_platform_dirs):
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "mine")
        _InfoHandler.device_id = "mine\n"

        mod, popen = _start_server_module(info_server.server_port)

        assert mod.started is True
        assert mod._external_server is True
        assert mod.foreign_server_port is None
        popen.assert_not_called()

    def test_adopts_when_user_has_no_local_datastore(self, info_server):
        # First run, or a Docker/systemd server with its own data dir: keep
        # the existing adoption behaviour (aw-qt#123).
        _InfoHandler.device_id = "theirs"

        mod, _popen = _start_server_module(info_server.server_port)

        assert mod.started is True
        assert mod._external_server is True
        assert mod.foreign_server_port is None

    def test_adopts_listener_without_device_id(self, info_server, _isolated_platform_dirs):
        # Old servers do not report device_id: identity is unknown, not foreign.
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "mine")
        _InfoHandler.device_id = None

        mod, _popen = _start_server_module(info_server.server_port)

        assert mod.started is True
        assert mod.foreign_server_port is None

    def test_python_server_datastore_counts_as_own(
        self, info_server, _isolated_platform_dirs
    ):
        # A user who moved from aw-server to aw-server-rust (or runs aw-server
        # externally) owns either datastore.
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "rust")
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server", "python")
        _InfoHandler.device_id = "python"

        mod, _popen = _start_server_module(info_server.server_port)

        assert mod.started is True
        assert mod.foreign_server_port is None


class TestLocalDeviceIds:
    def test_profile_selects_its_own_root(self, _isolated_platform_dirs):
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "default")
        _write_device_id(
            _isolated_platform_dirs, "activitywatch-research", "aw-server-rust", "research"
        )

        assert _local_server_device_ids("default") == {"default"}
        assert _local_server_device_ids("research") == {"research"}

    def test_reading_does_not_create_directories(self, _isolated_platform_dirs):
        assert _local_server_device_ids("default") == set()
        assert not (_isolated_platform_dirs / "data").exists()


class TestManagerWithForeignServer:
    def _manager(self):
        mgr = Manager(testing=False)
        server = Module("aw-server-rust", Path("/nonexistent/aw-server-rust"), "system")
        watcher = Module("aw-watcher-afk", Path("/usr/bin/true"), "system")
        notify = Module(manager_module.NOTIFY_MODULE, Path("/usr/bin/true"), "system")
        mgr.modules = [server, watcher, notify]
        return mgr, server, watcher, notify

    def test_autostart_starts_nothing_else(self, info_server, _isolated_platform_dirs):
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "mine")
        _InfoHandler.device_id = "theirs"
        mgr, server, watcher, _notify = self._manager()

        with (
            patch.object(server, "_get_server_port", return_value=info_server.server_port),
            patch("subprocess.Popen") as popen,
            patch.object(watcher, "start") as watcher_start,
        ):
            mgr.autostart(["aw-server-rust", "aw-watcher-afk"], port=info_server.server_port)

        assert mgr.foreign_server_port == info_server.server_port
        watcher_start.assert_not_called()
        popen.assert_not_called()

    def test_notify_not_started_against_foreign_server(self, info_server):
        mgr, server, _watcher, notify = self._manager()
        server.foreign_server_port = info_server.server_port

        with patch.object(notify, "start") as notify_start, patch.object(
            manager_module, "_read_notify_enabled_or_raise", return_value=True
        ):
            t = mgr.autostart_notify_if_enabled(info_server.server_port)
            t.join(timeout=5)

        notify_start.assert_not_called()

    def test_watchers_start_with_own_server(self, info_server, _isolated_platform_dirs):
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "mine")
        _InfoHandler.device_id = "mine"
        mgr, server, watcher, _notify = self._manager()

        with (
            patch.object(server, "_get_server_port", return_value=info_server.server_port),
            patch.object(watcher, "start") as watcher_start,
        ):
            mgr.autostart(["aw-server-rust", "aw-watcher-afk"], port=info_server.server_port)

        assert mgr.foreign_server_port is None
        watcher_start.assert_called_once_with(False)
