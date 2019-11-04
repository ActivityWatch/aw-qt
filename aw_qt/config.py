from configparser import ConfigParser

from aw_core.config import load_config
import json

default_settings = {
    "possible_modules": json.dumps(["aw-server",
                                    "aw-watcher-afk",
                                    "aw-watcher-window", ]),
    "autostart_modules": json.dumps(["aw-server",
                                     "aw-watcher-afk",
                                     "aw-watcher-window", ]),
}
default_testing_settings = {
    "possible_modules": json.dumps(["aw-server",
                                    "aw-watcher-afk",
                                    "aw-watcher-window", ]),
    "autostart_modules": json.dumps(["aw-server",
                                     "aw-watcher-afk",
                                     "aw-watcher-window", ]),
}

default_config = ConfigParser()
default_config['aw-qt'] = default_settings
default_config['aw-qt-testing'] = default_testing_settings
qt_config = load_config("aw-qt", default_config)


class QTSettings:
    def __init__(self, testing: bool):
        config_section = qt_config["aw-qt" if not testing else "aw-qt-testing"]

        # TODO: Resolved available modules automatically.
        # TODO: Filter away all modules not available on system
        self.possible_modules = json.loads(config_section["possible_modules"])
        self.autostart_modules = json.loads(config_section["autostart_modules"])
