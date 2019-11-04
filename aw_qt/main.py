import sys
import logging
import argparse

from aw_core.log import setup_logging

from .manager import Manager

from . import trayicon

logger = logging.getLogger(__name__)


def main():
    args = parse_args()
    setup_logging("aw-qt", testing=args.testing, verbose=args.testing, log_file=True)

    _manager = Manager(testing=args.testing)
    _manager.autostart(args.autostart_modules)

    error_code = trayicon.run(_manager, testing=args.testing)
    _manager.stop_all()

    sys.exit(error_code)


def parse_args():
    parser = argparse.ArgumentParser(prog="aw-qt", description='A trayicon and service manager for ActivityWatch')
    parser.add_argument('--testing', action='store_true',
                        help='Run the trayicon and services in testing mode')
    parser.add_argument('--autostart-modules', dest='autostart_modules',
                        type=lambda s: [m for m in s.split(',') if m and m.lower() != "none"],
                        default=None,
                        help='A comma-separated list of modules to autostart, or just `none` to not autostart anything')

    return parser.parse_args()
