from configparser import ConfigParser
from typing import List

from aw_core.config import load_config
import json

# NOTE: Updating this won't update the defaults for users, this is an issue with how aw_core.config works
default_settings = {
    "autostart_modules": json.dumps(
        ["aw-server", "aw-watcher-afk", "aw-watcher-window",]
    ),
}

default_config = ConfigParser()
default_config["aw-qt"] = default_settings
# Currently there's no reason to make them differ
default_config["aw-qt-testing"] = default_settings


class AwQtSettings:
    def __init__(self, testing: bool):
        """
        An instance of loaded settings, containing a list of modules to autostart.
        Constructor takes a `testing` boolean as an argument
        """
        qt_config = load_config("aw-qt", default_config)
        config_section = qt_config["aw-qt" if not testing else "aw-qt-testing"]

        self.autostart_modules: List[str] = json.loads(
            config_section["autostart_modules"]
        )
