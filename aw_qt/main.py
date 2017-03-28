import sys
import logging
import argparse
from time import sleep

from . import gui
from . import manager

logging.basicConfig()


def main():
    args = parse_args()

    # Start aw-server and wait for it to boot
    # FIXME: The wait isn't required if clients can handle an unavailable server at startup
    manager.modules["aw-server"].start(testing=args.testing)
    sleep(1)

    # Autostart modules
    autostart_modules = ["aw-watcher-afk", "aw-watcher-window"]
    for module_name in autostart_modules:
        manager.modules[module_name].start(testing=args.testing)

    error_code = gui.run(testing=args.testing)

    # TODO: This might no longer be needed due to signal handling etc. in trayicon.py
    for module in filter(lambda m: m.is_alive(), manager.modules.values()):
        module.stop()

    sys.exit(error_code)


def parse_args():
    parser = argparse.ArgumentParser(prog="aw-qt", description='A trayicon and service manager for ActivityWatch')
    parser.add_argument('--testing', action='store_true',
                        help='Run the trayicon and services in testing mode')

    return parser.parse_args()
