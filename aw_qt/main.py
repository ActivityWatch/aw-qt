import sys
import logging

from . import gui
from . import manager

logging.basicConfig()


def main():
    autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window"]

    # Autostart modules
    for module_name in autostart_modules:
        manager.modules[module_name].start()

    error_code = gui.run()

    # TODO: Stop all modules, not just autostarted ones
    for module_name in autostart_modules:
        manager.modules[module_name].stop()

    sys.exit(error_code)
