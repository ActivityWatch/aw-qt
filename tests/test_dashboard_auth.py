"""Dashboard token handoff is scoped to the selected Rust instance."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest
from aw_core import dirs
from aw_qt.config import _with_profile_env
from aw_qt.trayicon import TrayIcon


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
            else [SimpleNamespace(name="aw-server-rust", started=True, is_alive=lambda: True)]
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


def test_adopted_external_server_does_not_receive_key():
    write_config("default", 'port = 5601\n[auth]\napi_key = "rust-secret"')
    module = SimpleNamespace(name="aw-server-rust", started=True, _external_server=True)
    assert open_dashboard(modules=[module]) == "http://localhost:5601"


def test_crashed_owned_server_does_not_receive_key():
    write_config("default", 'port = 5601\n[auth]\napi_key = "rust-secret"')
    module = SimpleNamespace(name="aw-server-rust", started=True, is_alive=lambda: False)
    assert open_dashboard(modules=[module]) == "http://localhost:5601"
