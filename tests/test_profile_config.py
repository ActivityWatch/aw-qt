"""Unit tests for profile-aware settings."""

from pathlib import Path

import pytest

from aw_core import dirs
from aw_qt.config import AwQtSettings, _read_server_port
from aw_qt.profile import DEFAULT_PROFILE, export_profile


@pytest.fixture
def xdg_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("AW_PROFILE", raising=False)
    return tmp_path


def _module_config_dir(profile: str, module: str) -> Path:
    export_profile(profile)
    path = Path(dirs.get_config_dir(module))
    export_profile(DEFAULT_PROFILE)
    return path


class TestServerPort:
    def test_defaults_per_profile(self, xdg_tmp):
        assert _read_server_port("default") == 5600
        assert _read_server_port("testing") == 5666

    def test_isolated_profile_reads_bare_rust_config_toml(self, xdg_tmp):
        rust_dir = _module_config_dir("research", "aw-server-rust")
        (rust_dir / "config.toml").write_text("port = 5667\n")
        assert _read_server_port("research") == 5667
        # the default instance is unaffected
        assert _read_server_port("default") == 5600

    def test_isolated_profile_reads_server_section(self, xdg_tmp):
        server_dir = _module_config_dir("research", "aw-server")
        (server_dir / "aw-server.toml").write_text("[server]\nport = 5668\n")
        assert _read_server_port("research") == 5668

    def test_pre_isolation_shared_root_rust_file_is_still_found(self, xdg_tmp):
        """Named profiles stored config-<profile>.toml under activitywatch/."""
        shared = _module_config_dir(DEFAULT_PROFILE, "aw-server-rust")
        (shared / "config-research.toml").write_text("port = 5667\n")
        isolated = _module_config_dir("research", "aw-server-rust")
        assert not (isolated / "config.toml").exists()
        assert _read_server_port("research") == 5667
        assert _read_server_port("default") == 5600

    def test_isolated_bare_config_wins_over_shared_root_suffix(self, xdg_tmp):
        isolated = _module_config_dir("research", "aw-server-rust")
        (isolated / "config.toml").write_text("port = 5667\n")
        shared = _module_config_dir(DEFAULT_PROFILE, "aw-server-rust")
        (shared / "config-research.toml").write_text("port = 5999\n")
        assert _read_server_port("research") == 5667

    def test_pre_isolation_shared_root_python_section(self, xdg_tmp):
        shared = _module_config_dir(DEFAULT_PROFILE, "aw-server")
        (shared / "aw-server.toml").write_text("[server-research]\nport = 5668\n")
        assert _read_server_port("research") == 5668
        assert _read_server_port("default") == 5600

    def test_isolated_testing_uses_bare_config_toml(self, xdg_tmp):
        rust_dir = _module_config_dir("testing", "aw-server-rust")
        (rust_dir / "config.toml").write_text("port = 5669\n")
        assert "activitywatch-testing" in str(rust_dir)
        assert _read_server_port("testing") == 5669

    def test_legacy_testing_reads_config_testing_toml(self, xdg_tmp):
        data = xdg_tmp / "data" / "activitywatch" / "aw-server-rust"
        data.mkdir(parents=True)
        (data / "sqlite-testing.db").write_text("")
        rust_dir = _module_config_dir("testing", "aw-server-rust")
        assert "activitywatch-testing" not in str(rust_dir)
        (rust_dir / "config-testing.toml").write_text("port = 5670\n")
        (rust_dir / "config.toml").write_text("port = 5600\n")
        assert _read_server_port("testing") == 5670
        assert _read_server_port("default") == 5600

    def test_autostart_modules_prevents_rust_port_when_only_python_configured(
        self, xdg_tmp
    ):
        """Tray and manager must target the same endpoint.

        If aw-server-rust config exists with a custom port but only aw-server
        is in autostart_modules, _read_server_port must not return the Rust
        port — that would make the tray open a URL for a server that isn't
        running.
        """
        rust_dir = _module_config_dir("research", "aw-server-rust")
        (rust_dir / "config.toml").write_text("port = 5667\n")
        server_dir = _module_config_dir("research", "aw-server")
        (server_dir / "aw-server.toml").write_text("[server]\nport = 5668\n")
        # Without filtering: Rust port wins (old behaviour, causes divergence)
        assert _read_server_port("research") == 5667
        # With only aw-server in autostart_modules: Python port wins
        assert _read_server_port("research", ["aw-server", "aw-watcher-afk"]) == 5668
        # With aw-server-rust in autostart_modules: Rust port takes priority
        assert (
            _read_server_port("research", ["aw-server-rust", "aw-watcher-afk"]) == 5667
        )


class TestAwQtSettings:
    def test_profile_without_section_inherits_default(self, xdg_tmp):
        settings = AwQtSettings(profile="research")
        assert settings.autostart_modules == AwQtSettings().autostart_modules

    def test_pre_isolation_shared_root_awqt_section(self, xdg_tmp):
        shared = _module_config_dir(DEFAULT_PROFILE, "aw-qt")
        (shared / "aw-qt.toml").write_text(
            '[aw-qt-research]\nautostart_modules = ["aw-server-rust"]\n'
        )
        settings = AwQtSettings(profile="research")
        assert settings.autostart_modules == ["aw-server-rust"]

    def test_isolated_awqt_section_wins_over_shared_root(self, xdg_tmp):
        isolated = _module_config_dir("research", "aw-qt")
        (isolated / "aw-qt.toml").write_text(
            '[aw-qt]\nautostart_modules = ["aw-server"]\n'
        )
        shared = _module_config_dir(DEFAULT_PROFILE, "aw-qt")
        (shared / "aw-qt.toml").write_text(
            '[aw-qt-research]\nautostart_modules = ["aw-server-rust"]\n'
        )
        settings = AwQtSettings(profile="research")
        assert settings.autostart_modules == ["aw-server"]
