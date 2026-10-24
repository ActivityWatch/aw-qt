"""Dashboard token handoff is scoped to the selected Rust instance."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest
from aw_core import dirs
from aw_qt.config import _with_profile_env, dashboard_auth
from aw_qt.trayicon import TrayIcon
from aw_qt.manager import DASHBOARD_INFO_MAX_BYTES, Module


def authorize_listener(module):
    module._probe_dashboard_listener = lambda *args, **kwargs: True
    return module


def write_config(profile, text, filename="config.toml"):
    with _with_profile_env(profile):
        root = Path(dirs.get_config_dir("aw-server-rust"))
    path = root / filename
    path.write_text(text, encoding="utf-8")
    return path


def open_dashboard(profile="default", port=5601, modules=None):
    tray = SimpleNamespace(
        root_url=f"http://localhost:{port}",
        port=port,
        profile=profile,
        manager=SimpleNamespace(
            modules=modules
            if modules is not None
            else [SimpleNamespace(
                name="aw-server-rust", started=True, is_alive=lambda: True,
                dashboard_endpoint=(
                    profile,
                    (dashboard_auth(profile, port) or ("127.0.0.1", ""))[0],
                    port,
                ),
            )]
        ),
    )
    with patch("aw_qt.trayicon.open_webui") as opened:
        TrayIcon._open_dashboard(tray)
    return opened.call_args.args[0]


def test_handoff_encodes_key_and_preserves_dashboard_destination():
    key = "secret &+?#/雪"
    write_config("default", f'port = 5601\n[auth]\napi_key = "{key}"\n')
    url = urlsplit(open_dashboard())
    assert url.scheme == "http"
    assert url.netloc == "127.0.0.1:5601"
    assert parse_qs(url.query) == {"token": [key]}


@pytest.mark.parametrize(
    "text",
    [
        'port = 5602\n[auth]\napi_key = "other-port"',
        "port = 5601",
        'port = 5601\n[auth]\napi_key = ""',
        "port = 5601\n[auth]\napi_key = 42",
    ],
)
def test_no_key_for_mismatched_port_or_disabled_or_invalid_auth(text):
    write_config("default", text)
    assert open_dashboard() == "http://localhost:5601"


def test_missing_config_keeps_plain_url():
    assert open_dashboard() == "http://localhost:5601"


def test_python_backend_never_receives_rust_key():
    write_config("default", 'port = 5601\n[auth]\napi_key = "rust-secret"')
    url = open_dashboard(modules=[SimpleNamespace(name="aw-server", started=True)])
    assert url == "http://localhost:5601"


def test_ambiguous_backends_do_not_disclose_key():
    write_config("default", 'port = 5601\n[auth]\napi_key = "rust-secret"')
    modules = [
        SimpleNamespace(name=name, started=True)
        for name in ("aw-server", "aw-server-rust")
    ]
    assert open_dashboard(modules=modules) == "http://localhost:5601"


def test_stopped_rust_server_does_not_authorize_handoff():
    write_config("default", 'port = 5601\n[auth]\napi_key = "rust-secret"')
    url = open_dashboard(modules=[SimpleNamespace(name="aw-server-rust", started=False)])
    assert url == "http://localhost:5601"


def test_key_is_read_at_click_time():
    path = write_config("default", 'port = 5601\n[auth]\napi_key = "first"')
    assert parse_qs(urlsplit(open_dashboard()).query) == {"token": ["first"]}
    path.write_text('port = 5601\n[auth]\napi_key = "rotated"')
    assert parse_qs(urlsplit(open_dashboard()).query) == {"token": ["rotated"]}


def test_named_profile_never_uses_default_key():
    write_config("default", 'port = 5601\n[auth]\napi_key = "default-secret"')
    write_config("research", 'port = 5601\n[auth]\napi_key = "research-secret"')
    assert parse_qs(urlsplit(open_dashboard("research")).query) == {
        "token": ["research-secret"]
    }


def test_malformed_config_does_not_log_secret(caplog):
    write_config(
        "default", 'port = 5601\n[auth]\napi_key = "do-not-log-me" trailing-invalid'
    )
    assert open_dashboard() == "http://localhost:5601"
    assert "do-not-log-me" not in caplog.text


def test_testing_profile_uses_isolated_key_not_default():
    write_config("default", 'port = 5666\n[auth]\napi_key = "default-secret"')
    write_config("testing", 'port = 5666\n[auth]\napi_key = "testing-secret"')
    assert parse_qs(urlsplit(open_dashboard("testing", 5666)).query) == {
        "token": ["testing-secret"]
    }


def test_legacy_testing_uses_suffixed_file(tmp_path):
    data = tmp_path / "data" / "activitywatch" / "aw-server-rust"
    data.mkdir(parents=True)
    (data / "sqlite-testing.db").touch()
    write_config("default", 'port = 5666\n[auth]\napi_key = "default-secret"')
    write_config(
        "testing", 'port = 5666\n[auth]\napi_key = "legacy-test"', "config-testing.toml"
    )
    assert parse_qs(urlsplit(open_dashboard("testing", 5666)).query) == {
        "token": ["legacy-test"]
    }


def test_named_profile_legacy_fallback_is_profile_specific():
    write_config("default", 'port = 5601\n[auth]\napi_key = "default-secret"')
    write_config(
        "default",
        'port = 5601\n[auth]\napi_key = "legacy-research"',
        "config-research.toml",
    )
    assert parse_qs(urlsplit(open_dashboard("research")).query) == {
        "token": ["legacy-research"]
    }


def test_isolated_profile_config_disables_legacy_key_fallback():
    write_config("research", "port = 5601")
    write_config(
        "default",
        'port = 5601\n[auth]\napi_key = "legacy-research"',
        "config-research.toml",
    )
    assert open_dashboard("research") == "http://localhost:5601"


@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
def test_authenticated_destination_matches_configured_address(address):
    write_config(
        "default", f'port = 5601\naddress = "{address}"\n[auth]\napi_key = "secret"'
    )
    url = urlsplit(open_dashboard())
    assert url.hostname == address
    assert url.port == 5601
    assert parse_qs(url.query) == {"token": ["secret"]}


def test_hostname_config_does_not_authorize_key_handoff():
    write_config(
        "default", 'port = 5601\naddress = "localhost"\n[auth]\napi_key = "secret"'
    )
    assert open_dashboard() == "http://localhost:5601"


def test_non_loopback_config_does_not_authorize_key_handoff():
    write_config(
        "default", 'port = 5601\naddress = "example.com"\n[auth]\napi_key = "not-local"'
    )
    assert open_dashboard() == "http://localhost:5601"


def test_double_click_calls_authenticated_dashboard_path():
    from PyQt6.QtWidgets import QSystemTrayIcon

    tray = SimpleNamespace(_open_dashboard=lambda: None)
    with patch.object(tray, "_open_dashboard") as opened:
        TrayIcon.on_activated(tray, QSystemTrayIcon.ActivationReason.DoubleClick)
    opened.assert_called_once_with()


def test_dashboard_menu_uses_same_handoff_path():
    from aw_qt.manager import Module
    from pathlib import Path
    from test_tray_status import make_manager, make_tray, enter_tray_patches
    from contextlib import ExitStack

    module = Module("aw-server-rust", Path("/synthetic/aw-server-rust"), "bundled")
    manager = make_manager(module)
    tray = make_tray(module, manager)
    tray._open_dashboard = lambda: None
    with ExitStack() as stack:
        enter_tray_patches(stack, module)
        menu_class = stack.enter_context(patch("aw_qt.trayicon.QMenu"))
        opened = stack.enter_context(patch.object(tray, "_open_dashboard"))
        TrayIcon._build_rootmenu(tray)
        callbacks = [
            call.args[1] for call in menu_class.return_value.addAction.call_args_list
            if call.args and call.args[0] == "Open Dashboard"
        ]
        assert len(callbacks) == 1
        callbacks[0]()
        opened.assert_called_once_with()


@pytest.mark.parametrize("initial,changed", [("127.0.0.1", "::1"), ("::1", "127.0.0.1")])
def test_running_child_does_not_authorize_changed_address(initial, changed):
    path = write_config(
        "default", f'port = 5601\naddress = "{initial}"\n[auth]\napi_key = "secret"'
    )
    module = Module("aw-server-rust", Path("/synthetic/aw-server-rust"), "bundled")
    with patch.object(module, "_probe_external_server", return_value=False), patch(
        "aw_qt.manager.subprocess.Popen"
    ) as spawned:
        spawned.return_value.returncode = None
        module.start(False)
        authorize_listener(module)
        assert spawned.call_args.args[0] == [
            str(module.path), "--host", initial, "--port", "5601"
        ]
        assert "secret" not in spawned.call_args.args[0]
        assert parse_qs(urlsplit(open_dashboard(modules=[module])).query) == {
            "token": ["secret"]
        }
        path.write_text(
            f'port = 5601\naddress = "{changed}"\n[auth]\napi_key = "secret"'
        )
        assert open_dashboard(modules=[module]) == "http://localhost:5601"
        module.clear_exited_process()  # A live child must retain its endpoint.
        assert open_dashboard(modules=[module]) == "http://localhost:5601"
        spawned.return_value.wait.side_effect = lambda: setattr(
            spawned.return_value, "returncode", 0
        )
        module.stop()
        spawned.return_value.returncode = None
        module.start(False)
        authorize_listener(module)
        assert spawned.call_args.args[0] == [
            str(module.path), "--host", changed, "--port", "5601"
        ]
        assert urlsplit(open_dashboard(modules=[module])).hostname == changed
        assert parse_qs(urlsplit(open_dashboard(modules=[module])).query) == {
            "token": ["secret"]
        }
        path.write_text(
            f'port = 5601\naddress = "{changed}"\n[auth]\napi_key = "rotated"'
        )
        assert parse_qs(urlsplit(open_dashboard(modules=[module])).query) == {
            "token": ["rotated"]
        }


def test_auth_enabled_after_child_start_requires_restart():
    path = write_config("default", "port = 5601")
    module = Module("aw-server-rust", Path("/synthetic/aw-server-rust"), "bundled")
    with patch.object(module, "_probe_external_server", return_value=False), patch(
        "aw_qt.manager.subprocess.Popen"
    ) as spawned:
        spawned.return_value.returncode = None
        module.start(False)
        authorize_listener(module)
        assert spawned.call_args.args[0] == [str(module.path)]
        path.write_text('port = 5601\n[auth]\napi_key = "secret"')
        assert open_dashboard(modules=[module]) == "http://localhost:5601"


def test_changed_tray_port_does_not_retarget_running_child():
    path = write_config("default", 'port = 5601\n[auth]\napi_key = "secret"')
    module = Module("aw-server-rust", Path("/synthetic/aw-server-rust"), "bundled")
    with patch.object(module, "_probe_external_server", return_value=False), patch(
        "aw_qt.manager.subprocess.Popen"
    ) as spawned:
        spawned.return_value.returncode = None
        module.start(False)
        authorize_listener(module)
        path.write_text('port = 5602\n[auth]\napi_key = "secret"')
        assert open_dashboard(port=5602, modules=[module]) == "http://localhost:5602"


def test_adopted_external_server_does_not_receive_key():
    write_config("default", 'port = 5601\n[auth]\napi_key = "rust-secret"')
    module = SimpleNamespace(name="aw-server-rust", started=True, _external_server=True)
    assert open_dashboard(modules=[module]) == "http://localhost:5601"


def test_crashed_owned_server_does_not_receive_key():
    write_config("default", 'port = 5601\n[auth]\napi_key = "rust-secret"')
    module = SimpleNamespace(name="aw-server-rust", started=True, is_alive=lambda: False)
    assert open_dashboard(modules=[module]) == "http://localhost:5601"


def _serve_info(status=200, body=None):
    captured = {"authorization": None, "path": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["path"] = self.path
            captured["authorization"] = self.headers.get("Authorization")
            payload = body
            if payload is None and status == 200:
                payload = json.dumps({"hostname": "bob-test", "version": "0"}).encode()
            elif isinstance(payload, str):
                payload = payload.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, captured


def _started_owned_child():
    module = Module("aw-server-rust", Path("/synthetic/aw-server-rust"), "bundled")
    with patch.object(module, "_probe_external_server", return_value=False), patch(
        "aw_qt.manager.subprocess.Popen"
    ) as spawned:
        spawned.return_value.returncode = None
        module.start(False)
    return module


def test_404_occupant_does_not_receive_key():
    server, captured = _serve_info(status=404)
    try:
        port = server.server_address[1]
        write_config("default", f'port = {port}\n[auth]\napi_key = "secret"')
        module = _started_owned_child()
        assert module.dashboard_endpoint == ("default", "127.0.0.1", port)
        assert module.is_alive()
        assert open_dashboard(port=port, modules=[module]) == f"http://localhost:{port}"
        assert captured["authorization"] is None
        assert captured["path"] == "/api/0/info"
    finally:
        server.shutdown()
        server.server_close()


def test_aw_info_listener_authorizes_key():
    server, captured = _serve_info(status=200)
    try:
        port = server.server_address[1]
        write_config("default", f'port = {port}\n[auth]\napi_key = "secret"')
        module = _started_owned_child()
        url = urlsplit(open_dashboard(port=port, modules=[module]))
        assert url.hostname == "127.0.0.1"
        assert url.port == port
        assert parse_qs(url.query) == {"token": ["secret"]}
        assert captured["authorization"] is None
        assert captured["path"] == "/api/0/info"
    finally:
        server.shutdown()
        server.server_close()


def test_non_aw_200_html_does_not_authorize_key():
    server, _ = _serve_info(status=200, body="<html>not aw</html>")
    try:
        port = server.server_address[1]
        write_config("default", f'port = {port}\n[auth]\napi_key = "secret"')
        module = _started_owned_child()
        assert open_dashboard(port=port, modules=[module]) == f"http://localhost:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_unbound_child_does_not_receive_key():
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    try:
        write_config("default", f'port = {port}\n[auth]\napi_key = "secret"')
        module = _started_owned_child()
        assert module.is_alive()
        assert open_dashboard(port=port, modules=[module]) == f"http://localhost:{port}"
    finally:
        sock.close()


def test_truncated_info_response_does_not_abort_dashboard():
    captured = {"path": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["path"] = self.path
            payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        write_config("default", f'port = {port}\n[auth]\napi_key = "secret"')
        module = _started_owned_child()
        assert module._probe_dashboard_listener("127.0.0.1", port) is False
        assert open_dashboard(port=port, modules=[module]) == f"http://localhost:{port}"
        assert captured["path"] == "/api/0/info"
    finally:
        server.shutdown()
        server.server_close()


def test_ipv6_probe_uses_bracketed_numeric_address():
    module = Module("aw-server-rust", Path("/synthetic/aw-server-rust"), "bundled")
    with patch("aw_qt.manager.http.client.HTTPConnection") as conn_cls:
        conn_cls.side_effect = OSError("refused")
        assert module._probe_dashboard_listener("::1", 5601) is False
    conn_cls.assert_called_once_with("[::1]", 5601, timeout=0.2)


def test_oversized_info_body_does_not_authorize_or_block():
    captured = {"path": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["path"] = self.path
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(DASHBOARD_INFO_MAX_BYTES + 1))
            self.end_headers()
            self.wfile.write(b'{"hostname":"x"}')

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        write_config("default", f'port = {port}\n[auth]\napi_key = "secret"')
        module = _started_owned_child()
        started = monotonic()
        assert module._probe_dashboard_listener("127.0.0.1", port) is False
        assert monotonic() - started < 1.0
        assert open_dashboard(port=port, modules=[module]) == f"http://localhost:{port}"
        assert captured["path"] == "/api/0/info"
    finally:
        server.shutdown()
        server.server_close()


def test_slow_drip_info_body_fails_closed_within_timeout():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "64")
            self.end_headers()
            payload = b'{"hostname":"x"}' + b" " * (64 - 16)
            for byte in payload:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                except BrokenPipeError:
                    return
                sleep(0.05)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        write_config("default", f'port = {port}\n[auth]\napi_key = "secret"')
        module = _started_owned_child()
        started = monotonic()
        assert module._probe_dashboard_listener("127.0.0.1", port) is False
        assert monotonic() - started < 1.0
        assert open_dashboard(port=port, modules=[module]) == f"http://localhost:{port}"
    finally:
        server.shutdown()
        server.server_close()
