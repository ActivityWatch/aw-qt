import sys
import logging
import click
from typing import Optional

from aw_core.log import setup_logging

from .manager import Manager
from . import trayicon
from .config import AwQtSettings

logger = logging.getLogger(__name__)


@click.command("aw-qt", help="A trayicon and service manager for ActivityWatch")
@click.option(
    "--testing", is_flag=True, help="Run the trayicon and services in testing mode"
)
@click.option(
    "--autostart-modules",
    help="A comma-separated list of modules to autostart, or just `none` to not autostart anything.",
)
def main(testing: bool, autostart_modules: Optional[str]) -> None:
    config = AwQtSettings(testing=testing)
    _autostart_modules = (
        [m.strip() for m in autostart_modules.split(",") if m and m.lower() != "none"]
        if autostart_modules
        else config.autostart_modules
    )
    setup_logging("aw-qt", testing=testing, verbose=testing, log_file=True)

    _manager = Manager(testing=testing)
    _manager.autostart(_autostart_modules)

    error_code = trayicon.run(_manager, testing=testing)
    _manager.stop_all()

    sys.exit(error_code)
