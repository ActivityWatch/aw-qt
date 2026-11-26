"""aw-qt must not adopt a server that serves another datastore.

On a multiuser machine, user B's aw-qt finds user A's server on the shared
default port. Adopting it sends B's watcher data into A's database
(ActivityWatch/activitywatch#75). The listener's ``/api/0/info`` device_id
identifies its datastore, so a mismatch with B's own datastore is refused.
"""

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from aw_qt.manager import Manager, Module, NOTIFY_MODULE, _local_server_device_ids


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

    @pytest.mark.parametrize("error", [
        urllib.error.URLError("timeout"),
        OSError("connection reset"),
        ValueError("invalid JSON"),
    ])
    def test_failed_identity_check_refuses_adoption(
        self, error, info_server, _isolated_platform_dirs
    ):
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "mine")
        real_urlopen = urllib.request.urlopen
        requests = 0

        def urlopen(*args, **kwargs):
            nonlocal requests
            requests += 1
            if requests == 2:
                raise error
            return real_urlopen(*args, **kwargs)

        with patch.object(urllib.request, "urlopen", side_effect=urlopen):
            mod, popen = _start_server_module(info_server.server_port)

        assert requests == 2
        assert mod.started is False
        assert mod._external_server is False
        assert mod.foreign_server_port == info_server.server_port
        popen.assert_not_called()

    @pytest.mark.parametrize("body", [b"not JSON", b"[]", b"null", b"\xff"])
    def test_invalid_identity_response_is_refused(self, body, _isolated_platform_dirs):
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "mine")
        from unittest.mock import MagicMock

        response = MagicMock()
        response.__enter__.return_value.read1.side_effect = [body, b""]
        with patch.object(urllib.request, "urlopen", return_value=response):
            mod, popen = _start_server_module(5600)

        assert not mod.started
        assert mod.foreign_server_port == 5600
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


def _manager():
    mgr = Manager(testing=False)
    server = Module("aw-server-rust", Path("/nonexistent/aw-server-rust"), "system")
    watcher = Module("aw-watcher-afk", Path("/usr/bin/true"), "system")
    notify = Module(NOTIFY_MODULE, Path("/usr/bin/true"), "system")
    mgr.modules = [server, watcher, notify]
    return mgr, server, watcher, notify


@pytest.fixture
def own_datastore(_isolated_platform_dirs):
    _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "mine")


@pytest.mark.parametrize("spawned", [False, True], ids=["adoption", "post-spawn"])
def test_missing_device_id_is_refused(spawned, info_server, own_datastore):
    # An answering listener that cannot be identified is refused on both paths.
    _InfoHandler.device_id = None
    server = Module("aw-server-rust", Path("/server"), "system")
    with (
        patch.object(server, "_get_server_port", return_value=info_server.server_port),
        patch.object(
            server, "_probe_external_server", wraps=server._probe_external_server
        ) as probe,
        patch("subprocess.Popen") as popen,
    ):
        if spawned:
            probe.side_effect = lambda *a, **k: False
            popen.return_value.poll.return_value = None
        else:
            server._external_server_port = info_server.server_port
        server.start(False)

    assert not server.started
    assert server.foreign_server_port == info_server.server_port
    assert popen.called is spawned
    if spawned:
        popen.return_value.terminate.assert_called_once_with()


def test_first_run_of_other_server_implementation_is_accepted(
    info_server, _isolated_platform_dirs
):
    # Switching from aw-server to aw-server-rust: the child writes its new
    # device_id after aw-qt snapshots the user's ids, then reports it.
    _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server", "python")
    _InfoHandler.device_id = "rust"
    server = Module("aw-server-rust", Path("/server"), "system")
    read_identity = server._external_server_device_id

    def child_initialises(*args, **kwargs):
        _write_device_id(_isolated_platform_dirs, "activitywatch", "aw-server-rust", "rust")
        return read_identity(*args, **kwargs)

    with (
        patch.object(server, "_get_server_port", return_value=info_server.server_port),
        patch.object(server, "_probe_external_server", return_value=False),
        patch.object(server, "_external_server_device_id", side_effect=child_initialises),
        patch("subprocess.Popen") as popen,
    ):
        popen.return_value.poll.return_value = None
        server.start(False)

    assert server.started
    assert server.foreign_server_port is None
    popen.return_value.terminate.assert_not_called()


@pytest.mark.parametrize("spawned", [False, True], ids=["adoption", "post-spawn"])
def test_missing_device_id_without_local_datastore_is_accepted(spawned, info_server):
    # Nothing to compare against (first run): behaviour is unchanged.
    _InfoHandler.device_id = None
    server = Module("aw-server-rust", Path("/server"), "system")
    with (
        patch.object(server, "_get_server_port", return_value=info_server.server_port),
        patch.object(
            server, "_probe_external_server", wraps=server._probe_external_server
        ) as probe,
        patch("subprocess.Popen"),
    ):
        if spawned:
            probe.side_effect = lambda *a, **k: False
        else:
            server._external_server_port = info_server.server_port
        server.start(False)

    assert server.started
    assert server.foreign_server_port is None


class TestManagerWithForeignServer:
    def test_autostart_starts_nothing_else(self, info_server, own_datastore):
        _InfoHandler.device_id = "theirs"
        mgr, server, watcher, _notify = _manager()

        with (
            patch.object(server, "_get_server_port", return_value=info_server.server_port),
            patch("subprocess.Popen") as popen,
            patch.object(watcher, "start") as watcher_start,
        ):
            mgr.autostart(["aw-server-rust", "aw-watcher-afk"], port=None)

        assert mgr.foreign_server_port == info_server.server_port
        watcher_start.assert_not_called()
        popen.assert_not_called()

    def test_notify_not_started_after_refusal(self):
        mgr, server, _watcher, notify = _manager()
        server.foreign_server_port = 5600

        with (
            patch.object(notify, "start") as notify_start,
            patch("aw_qt.manager.wait_for_server", return_value=True),
            patch("aw_qt.manager._read_notify_enabled_or_raise", return_value=True),
        ):
            mgr.autostart_notify_if_enabled(5600).join(timeout=5)

        notify_start.assert_not_called()

    def test_refusal_stops_running_dependents_and_blocks_starts(
        self, info_server, own_datastore
    ):
        _InfoHandler.device_id = "theirs"
        mgr, server, watcher, notify = _manager()
        watcher.started = notify.started = True
        mgr._pending_autostart["aw-sync"] = object()

        with (
            patch.object(server, "_get_server_port", return_value=info_server.server_port),
            patch("subprocess.Popen") as popen,
        ):
            assert mgr.toggle(server) is None
            assert not watcher.started and not notify.started
            assert mgr._pending_autostart == {}
            mgr.start(watcher.name)
            assert mgr.toggle(watcher) is None
            assert mgr.restart(watcher) is False
            popen.assert_not_called()

    def test_refusal_lasts_until_restart(self, info_server, own_datastore):
        # Even once our own server answers on the port, nothing clears it.
        mgr, server, watcher, _notify = _manager()
        with (
            patch.object(server, "_get_server_port", return_value=info_server.server_port),
            patch.object(watcher, "start") as watcher_start,
            patch("subprocess.Popen") as popen,
        ):
            _InfoHandler.device_id = "theirs"
            mgr.start(server.name)
            _InfoHandler.device_id = "mine"
            assert mgr.toggle(server) is None
            assert mgr.restart(server) is False
            mgr.start(watcher.name)

            assert mgr.foreign_server_port == info_server.server_port
            assert not server.started
            watcher_start.assert_not_called()
            popen.assert_not_called()

    def test_another_server_copy_does_not_release_watchers(
        self, info_server, own_datastore
    ):
        _InfoHandler.device_id = "mine"
        mgr, server, watcher, _notify = _manager()
        server.foreign_server_port = 5600
        other = Module("aw-server", Path("/other/aw-server"), "bundled")
        mgr.modules.append(other)
        with (
            patch.object(other, "_get_server_port", return_value=info_server.server_port),
            patch.object(watcher, "start") as watcher_start,
        ):
            assert mgr.toggle(other) is True
            mgr.start(watcher.name)

        assert mgr.foreign_server_port == 5600
        watcher_start.assert_not_called()

    def test_watchers_start_with_own_server(self, info_server, own_datastore):
        _InfoHandler.device_id = "mine"
        mgr, server, watcher, _notify = _manager()

        with (
            patch.object(server, "_get_server_port", return_value=info_server.server_port),
            patch.object(watcher, "start") as watcher_start,
        ):
            mgr.autostart(["aw-server-rust", "aw-watcher-afk"], port=None)

        assert mgr.foreign_server_port is None
        watcher_start.assert_called_once()


class TestSpawnedListenerVerification:
    """A spawn proves a child was created, not that it owns the port."""

    @staticmethod
    def _spawn(server, *, exit_code, listening, identity):
        with (
            patch.object(server, "_get_server_port", return_value=5600),
            patch.object(server, "_probe_external_server", return_value=False),
            patch.object(server, "_external_server_device_id", **identity),
            patch("subprocess.Popen") as popen,
            patch("aw_qt.manager._port_accepts_connections", return_value=listening),
        ):
            popen.return_value.poll.return_value = exit_code
            server.start(False)
        return popen

    @pytest.mark.parametrize(
        "exit_code,listening,refused",
        [(1, True, True), (None, True, True), (1, False, False)],
        ids=["held-by-another", "unanswering-listener", "broken-binary"],
    )
    def test_unreadable_listener(self, exit_code, listening, refused, own_datastore):
        server = Module("aw-server-rust", Path("/server"), "system")
        self._spawn(
            server,
            exit_code=exit_code,
            listening=listening,
            identity={"side_effect": urllib.error.URLError("timeout")},
        )

        assert server.foreign_server_port == (5600 if refused else None)
        assert server.started is not refused

    @pytest.mark.parametrize("identity", ["mine", "theirs"])
    def test_readable_listener(self, identity, own_datastore):
        server = Module("aw-server-rust", Path("/server"), "system")
        self._spawn(
            server, exit_code=None, listening=True, identity={"return_value": identity}
        )

        assert server.started is (identity == "mine")

    @pytest.mark.parametrize("listening", [True, False])
    def test_autostart_after_a_launch_failure_respects_the_listener(
        self, listening, own_datastore
    ):
        mgr, server, watcher, _notify = _manager()
        watcher_calls = []
        with (
            patch.object(server, "_get_server_port", return_value=5600),
            patch.object(server, "_probe_external_server", return_value=False),
            patch.object(
                server,
                "_external_server_device_id",
                side_effect=urllib.error.URLError("timeout"),
            ),
            patch("subprocess.Popen", side_effect=OSError("missing interpreter")),
            patch("aw_qt.manager._port_accepts_connections", return_value=listening),
            patch.object(
                watcher, "start", side_effect=lambda *a, **k: watcher_calls.append(a)
            ),
        ):
            mgr.autostart([server.name, watcher.name])

        assert mgr.foreign_server_port == (5600 if listening else None)
        assert len(watcher_calls) == (0 if listening else 1)

    def test_refusal_terminates_spawned_child(self, own_datastore):
        server = Module("aw-server-rust", Path("/server"), "system")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            with (
                patch.object(server, "_get_server_port", return_value=5600),
                patch.object(server, "_probe_external_server", return_value=False),
                patch.object(server, "_external_server_device_id", return_value="theirs"),
                patch("subprocess.Popen", return_value=child),
            ):
                server.start(False)

            assert child.poll() is not None
            assert server._process is None
            assert server._last_process is child
            assert server.dashboard_endpoint is None
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def test_refusal_kills_child_that_ignores_termination(self):
        server = Module("aw-server-rust", Path("/server"), "system")
        with patch("subprocess.Popen") as popen:
            child = popen.return_value
            child.poll.return_value = None
            child.wait.side_effect = [subprocess.TimeoutExpired("server", 3.0), 0]
            server._process = child

            server._refuse_port(5600, "test")

        child.kill.assert_called_once_with()
        assert server._process is None


class _TrickleHandler(BaseHTTPRequestHandler):
    """Answers /api/0/info one byte at a time, never finishing quickly."""

    oversize = False

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            if self.oversize:
                self.wfile.write(b" " * 10_000 + b"{}")
                return
            for _ in range(100):
                self.wfile.write(b" ")
                self.wfile.flush()
                time.sleep(0.05)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("oversize", [False, True])
def test_identity_read_is_bounded(oversize):
    # The probed listener may be another user's: a slow or huge response must
    # not hold the Qt thread past the timeout.
    _TrickleHandler.oversize = oversize
    server = HTTPServer(("127.0.0.1", 0), _TrickleHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        mod = Module("aw-server-rust", Path("/nonexistent/aw-server-rust"), "system")
        started = time.monotonic()
        with pytest.raises((TimeoutError, ValueError)):
            mod._external_server_device_id(server.server_port, timeout=0.3)
        assert time.monotonic() - started < 2
    finally:
        server.shutdown()
