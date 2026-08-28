import logging
import os
from typing import Any, List, Optional

import tomlkit
from aw_core import dirs
from aw_core.config import load_config_toml

from .profile import DEFAULT_PROFILE, TESTING_PROFILE, is_testing

logger = logging.getLogger(__name__)

default_config = """
[aw-qt]
autostart_modules = ["aw-server-rust", "aw-watcher-afk", "aw-watcher-window"]

[aw-qt-testing]
autostart_modules = ["aw-server-rust", "aw-watcher-afk", "aw-watcher-window"]
""".strip()


def _read_server_rust_port(profile: str) -> Optional[int]:
    """Read port from aw-server-rust config, returns None if not found/set."""
    config_dir = dirs.get_config_dir("aw-server-rust")
    # Mirrors aw-server-rust's own config path: bare for the default profile,
    # `config-<profile>.toml` otherwise.
    config_file = (
        "config.toml" if profile == DEFAULT_PROFILE else f"config-{profile}.toml"
    )
    config_path = os.path.join(config_dir, config_file)

    if not os.path.isfile(config_path):
        return None

    try:
        with open(config_path) as f:
            config = tomlkit.parse(f.read())
        if "port" in config:
            return int(str(config["port"]))
    except Exception as e:
        logger.warning("Failed to read aw-server-rust config: %s", e)
    return None


def _read_aw_server_port(profile: str) -> Optional[int]:
    """Read port from aw-server (Python) config, returns None if not found/set."""
    config_dir = dirs.get_config_dir("aw-server")
    config_path = os.path.join(config_dir, "aw-server.toml")
    section = "server" if profile == DEFAULT_PROFILE else f"server-{profile}"

    if not os.path.isfile(config_path):
        return None

    try:
        with open(config_path) as f:
            config = tomlkit.parse(f.read())
        section_data = config.get(section, {})
        if "port" in section_data:
            return int(str(section_data["port"]))
    except Exception as e:
        logger.warning("Failed to read aw-server config: %s", e)
    return None


def _read_server_port(
    profile: str, autostart_modules: Optional[List[str]] = None
) -> int:
    """Read port from server config (aw-server-rust or aw-server), falling back to defaults.

    Only `default` and `testing` have a built-in port; any other profile must
    set `port` in its own config, since two instances cannot share 5600.

    When `autostart_modules` is provided, port lookup is restricted to the
    server type(s) actually configured to run, so the tray and the manager
    always target the same endpoint.
    """
    default_port = 5666 if is_testing(profile) else 5600

    # Determine which server types are in play.  When autostart_modules is
    # None (legacy / test call-site), fall back to the original Rust-first
    # behaviour so existing callers are unaffected.
    check_rust = autostart_modules is None or "aw-server-rust" in autostart_modules
    check_python = autostart_modules is None or "aw-server" in autostart_modules

    if check_rust:
        port = _read_server_rust_port(profile)
        if port is not None:
            return port

    if check_python:
        port = _read_aw_server_port(profile)
        if port is not None:
            return port

    if profile not in (DEFAULT_PROFILE, TESTING_PROFILE):
        logger.warning(
            "Profile %s has no port configured, falling back to %s "
            "(which collides with the default instance)",
            profile,
            default_port,
        )

    return default_port


class AwQtSettings:
    def __init__(self, profile: str = DEFAULT_PROFILE):
        """
        An instance of loaded settings, containing a list of modules to autostart.
        Constructor takes the profile name as an argument.
        """
        config = load_config_toml("aw-qt", default_config)
        section_name = "aw-qt" if profile == DEFAULT_PROFILE else f"aw-qt-{profile}"
        if section_name not in config:
            # A profile without its own section inherits the default one.
            section_name = "aw-qt"
        config_section: Any = config[section_name]

        self.autostart_modules: List[str] = config_section["autostart_modules"]
        # Pass autostart_modules so port lookup targets the actual server type,
        # keeping the tray URL and the manager's probe endpoint consistent.
        self.port: int = _read_server_port(profile, self.autostart_modules)
