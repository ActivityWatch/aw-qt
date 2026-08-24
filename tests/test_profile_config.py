"""Unit tests for profile-aware settings."""

import os

import pytest

from aw_qt import config as config_module
from aw_qt.config import AwQtSettings, _read_server_port


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config_module.dirs, "get_config_dir", lambda module: str(tmp_path / module)
    )
    for module in ("aw-server", "aw-server-rust", "aw-qt"):
        os.makedirs(tmp_path / module, exist_ok=True)
    return tmp_path


class TestServerPort:
    def test_defaults_per_profile(self, isolated_config):
        assert _read_server_port("default") == 5600
        assert _read_server_port("testing") == 5666

    def test_custom_profile_reads_its_own_rust_config(self, isolated_config):
        (isolated_config / "aw-server-rust" / "config-research.toml").write_text(
            "port = 5667\n"
        )
        assert _read_server_port("research") == 5667
        # the default instance is unaffected
        assert _read_server_port("default") == 5600

    def test_custom_profile_reads_its_own_python_config(self, isolated_config):
        (isolated_config / "aw-server" / "aw-server.toml").write_text(
            "[server-research]\nport = 5668\n"
        )
        assert _read_server_port("research") == 5668


class TestAwQtSettings:
    def test_profile_without_section_inherits_default(self, isolated_config):
        settings = AwQtSettings(profile="research")
        assert settings.autostart_modules == AwQtSettings().autostart_modules
