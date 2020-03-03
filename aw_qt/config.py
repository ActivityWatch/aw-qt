from configparser import ConfigParser
from typing import ClassVar, List

from aw_core.config import load_config
import json

default_settings = {
    "possible_modules": json.dumps(["aw-server",
                                    "aw-server-rust",
                                    "aw-watcher-afk",
                                    "aw-watcher-window", ]),
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


class AwQtSettings:
    def __init__(self, testing: bool):
        config_section = qt_config["aw-qt" if not testing else "aw-qt-testing"]

        self.possible_modules: List[str] = json.loads(config_section["possible_modules"])
        self.autostart_modules: List[str] = json.loads(config_section["autostart_modules"])
