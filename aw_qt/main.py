import sys
import logging
import argparse
from time import sleep
from typing import List

from aw_core.log import setup_logging

from . import manager
from . import trayicon

logger = logging.getLogger(__name__)


def autostart(modules: List[str], testing: bool):
    # Start aw-server and wait for it to boot
    # FIXME: The wait isn't required if clients can handle an unavailable server at startup
    if "aw-server" in modules:
        manager.modules["aw-server"].start(testing=testing)
        modules.remove("aw-server")
        sleep(1)

    # Autostart modules
    autostart_modules = modules
    for module_name in autostart_modules:
        if module_name in manager.modules:
            manager.modules[module_name].start(testing=testing)
        else:
            logger.error("Module {} not available".format(module_name))


def stop():
    # TODO: This might no longer be needed due to signal handling etc. in trayicon.py
    for module in filter(lambda m: m.is_alive(), manager.modules.values()):
        module.stop()


def main():
    args = parse_args()
    setup_logging("aw-qt", testing=args.testing, verbose=args.testing, log_file=True)

    autostart(args.autostart_modules, testing=args.testing)

    error_code = trayicon.run(testing=args.testing)

    stop()

    sys.exit(error_code)


def parse_args():
    parser = argparse.ArgumentParser(prog="aw-qt", description='A trayicon and service manager for ActivityWatch')
    parser.add_argument('--testing', action='store_true',
                        help='Run the trayicon and services in testing mode')
    parser.add_argument('--autostart-modules', dest='autostart_modules',
                        type=lambda s: [m for m in s.split(',') if m and m.lower() != "none"],
                        default=["aw-server", "aw-watcher-afk", "aw-watcher-window"],
                        help='A comma-separated list of modules to autostart, or just `none` to not autostart anything')

    return parser.parse_args()
