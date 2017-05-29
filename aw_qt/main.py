import sys
import logging
import argparse
from time import sleep

from . import manager
from . import trayicon

logging.basicConfig()


def autostart(testing: bool):
    # Start aw-server and wait for it to boot
    # FIXME: The wait isn't required if clients can handle an unavailable server at startup
    manager.modules["aw-server"].start(testing=testing)
    sleep(1)

    # Autostart modules
    autostart_modules = ["aw-watcher-afk", "aw-watcher-window"]
    for module_name in autostart_modules:
        manager.modules[module_name].start(testing=testing)


def stop():
    # TODO: This might no longer be needed due to signal handling etc. in trayicon.py
    for module in filter(lambda m: m.is_alive(), manager.modules.values()):
        module.stop()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.testing else logging.INFO)

    autostart(testing=args.testing)

    error_code = trayicon.run(testing=args.testing)

    stop()

    sys.exit(error_code)


def parse_args():
    parser = argparse.ArgumentParser(prog="aw-qt", description='A trayicon and service manager for ActivityWatch')
    parser.add_argument('--testing', action='store_true',
                        help='Run the trayicon and services in testing mode')

    return parser.parse_args()
