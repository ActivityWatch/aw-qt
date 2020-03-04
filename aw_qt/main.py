import sys
import logging
import argparse
from typing import List
from typing_extensions import TypedDict

from aw_core.log import setup_logging

from .manager import Manager
from . import trayicon

logger = logging.getLogger(__name__)


def main() -> None:
    args = parse_args()
    setup_logging("aw-qt", testing=args['testing'], verbose=args['testing'], log_file=True)

    _manager = Manager(testing=args['testing'])
    _manager.autostart(args['autostart_modules'])

    error_code = trayicon.run(_manager, testing=args['testing'])
    _manager.stop_all()

    sys.exit(error_code)


CommandLineArgs = TypedDict('CommandLineArgs', {'testing': bool, 'autostart_modules': List[str]}, total=False)


def parse_args() -> CommandLineArgs:
    parser = argparse.ArgumentParser(prog="aw-qt", description='A trayicon and service manager for ActivityWatch')
    parser.add_argument('--testing', action='store_true',
                        help='Run the trayicon and services in testing mode')
    parser.add_argument('--autostart-modules', dest='autostart_modules',
                        type=lambda s: [m for m in s.split(',') if m and m.lower() != "none"],
                        default=None,
                        help='A comma-separated list of modules to autostart, or just `none` to not autostart anything')
    parsed_args = parser.parse_args()
    dict: CommandLineArgs = {'autostart_modules': parsed_args.autostart_modules, 'testing': parsed_args.testing}
    return dict
