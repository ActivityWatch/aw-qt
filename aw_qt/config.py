from configparser import ConfigParser
from typing import List

from aw_core.config import load_config
import json

default_settings = {
    "autostart_modules": json.dumps(["aw-server-rust",
                                     "aw-server",
                                     "aw-watcher-afk",
                                     "aw-watcher-window", ]),
}

default_config = ConfigParser()
default_config['aw-qt'] = default_settings
# Currently there's no reason to make them differ
default_config['aw-qt-testing'] = default_settings
qt_config = load_config("aw-qt", default_config)

"""
An instance of loaded settings, containing a list of modules to autostart.
Constructor takes a `testing` boolean as an argument
"""
class AwQtSettings:
    def __init__(self, testing: bool):
        config_section = qt_config["aw-qt" if not testing else "aw-qt-testing"]

        self.autostart_modules: List[str] = json.loads(config_section["autostart_modules"])
