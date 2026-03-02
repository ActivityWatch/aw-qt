import logging
import os
from typing import Any, List

import tomlkit
from aw_core import dirs
from aw_core.config import load_config_toml

logger = logging.getLogger(__name__)

default_config = """
[aw-qt]
autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window"]

[aw-qt-testing]
autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window"]
""".strip()


def _read_server_rust_port(testing: bool) -> int | None:
    """Read port from aw-server-rust config, returns None if not found/set."""
    config_dir = dirs.get_config_dir("aw-server-rust")
    config_file = "config-testing.toml" if testing else "config.toml"
    config_path = os.path.join(config_dir, config_file)

    if not os.path.isfile(config_path):
        return None

    try:
        with open(config_path) as f:
            config = tomlkit.parse(f.read())
        if "port" in config:
            return int(config["port"])
    except Exception as e:
        logger.warning("Failed to read aw-server-rust config: %s", e)
    return None


def _read_aw_server_port(testing: bool) -> int | None:
    """Read port from aw-server (Python) config, returns None if not found/set."""
    config_dir = dirs.get_config_dir("aw-server")
    config_path = os.path.join(config_dir, "aw-server.toml")
    section = "server-testing" if testing else "server"

    if not os.path.isfile(config_path):
        return None

    try:
        with open(config_path) as f:
            config = tomlkit.parse(f.read())
        section_data = config.get(section, {})
        if "port" in section_data:
            return int(section_data["port"])
    except Exception as e:
        logger.warning("Failed to read aw-server config: %s", e)
    return None


def _read_server_port(testing: bool) -> int:
    """Read port from server config (aw-server-rust or aw-server), falling back to defaults."""
    default_port = 5666 if testing else 5600

    port = _read_server_rust_port(testing)
    if port is not None:
        return port

    port = _read_aw_server_port(testing)
    if port is not None:
        return port

    return default_port


class AwQtSettings:
    def __init__(self, testing: bool):
        """
        An instance of loaded settings, containing a list of modules to autostart.
        Constructor takes a `testing` boolean as an argument
        """
        config = load_config_toml("aw-qt", default_config)
        config_section: Any = config["aw-qt" if not testing else "aw-qt-testing"]

        self.autostart_modules: List[str] = config_section["autostart_modules"]
        self.port: int = _read_server_port(testing)
