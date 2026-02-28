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


def _read_server_port(testing: bool) -> int:
    """Read the port from aw-server-rust's config file, falling back to defaults."""
    default_port = 5666 if testing else 5600
    config_dir = dirs.get_config_dir("aw-server-rust")
    config_file = "config-testing.toml" if testing else "config.toml"
    config_path = os.path.join(config_dir, config_file)

    if not os.path.isfile(config_path):
        return default_port

    try:
        with open(config_path) as f:
            config = tomlkit.parse(f.read())
        port = config.get("port", default_port)
        return int(port)
    except Exception as e:
        logger.warning("Failed to read aw-server-rust config: %s", e)
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
