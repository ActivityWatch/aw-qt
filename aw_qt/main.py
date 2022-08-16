import os
import sys
import logging
import subprocess
import platform
import signal
import threading
from typing import Optional
from time import sleep

import click
from aw_core.log import setup_logging

from .manager import Manager
from .config import AwQtSettings

logger = logging.getLogger(__name__)


@click.command("aw-qt", help="A trayicon and service manager for ActivityWatch")
@click.option(
    "--testing", is_flag=True, help="Run the trayicon and services in testing mode"
)
@click.option("-v", "--verbose", is_flag=True, help="Run with debug logging")
@click.option(
    "--autostart-modules",
    help="A comma-separated list of modules to autostart, or just `none` to not autostart anything.",
)
@click.option(
    "--no-gui",
    is_flag=True,
    help="Start aw-qt without a graphical user interface (terminal output only)",
)
@click.option(
    "-i",
    "--interactive",
    "interactive_cli",
    is_flag=True,
    help="Start aw-qt in interactive cli mode (forces --no-gui)",
)
def main(
    testing: bool,
    verbose: bool,
    autostart_modules: Optional[str],
    no_gui: bool,
    interactive_cli: bool,
) -> None:
    # Since the .app can crash when started from Finder for unknown reasons, we send a syslog message here to make debugging easier.
    if platform.system() == "Darwin":
        subprocess.call("syslog -s 'aw-qt started'", shell=True)

    setup_logging("aw-qt", testing=testing, verbose=verbose, log_file=True)
    logger.info("Started aw-qt...")

    # Since the .app can crash when started from Finder for unknown reasons, we send a syslog message here to make debugging easier.
    if platform.system() == "Darwin":
        subprocess.call("syslog -s 'aw-qt successfully started logging'", shell=True)

    # Create a process group, become its leader
    # TODO: This shouldn't go here
    if sys.platform != "win32":
        # Running setpgrp when the python process is a session leader fails,
        # such as in a systemd service. See:
        # https://stackoverflow.com/a/51005084/1014208
        try:
            os.setpgrp()
        except PermissionError:
            pass

    config = AwQtSettings(testing=testing)
    _autostart_modules = (
        [m.strip() for m in autostart_modules.split(",") if m and m.lower() != "none"]
        if autostart_modules
        else config.autostart_modules
    )

    manager = Manager(testing=testing)
    manager.autostart(_autostart_modules)

    if not no_gui and not interactive_cli:
        from . import trayicon  # pylint: disable=import-outside-toplevel

        # run the trayicon, wait for signal to quit
        error_code = trayicon.run(manager, testing=testing)
    elif interactive_cli:
        # just an experiment, don't really see the use right now
        _interactive_cli(manager)
        error_code = 0
    else:
        # wait for signal to quit
        if sys.platform == "win32":
            # Windows doesn't support signals, so we just sleep until interrupted
            try:
                sleep(threading.TIMEOUT_MAX)
            except KeyboardInterrupt:
                pass
        else:
            signal.pause()

        error_code = 0

    manager.stop_all()
    sys.exit(error_code)


def _interactive_cli(manager: Manager) -> None:
    while True:
        answer = input("> ")
        if answer == "q":
            break

        tokens = answer.split(" ")
        t = tokens[0]
        if t == "start":
            if len(tokens) == 2:
                manager.start(tokens[1])
            else:
                print("Usage: start <module>")
        elif t == "stop":
            if len(tokens) == 2:
                manager.stop(tokens[1])
            else:
                print("Usage: stop <module>")
        elif t in ["s", "status"]:
            if len(tokens) == 1:
                manager.print_status()
            elif len(tokens) == 2:
                manager.print_status(tokens[1])
        elif not t.strip():
            # if t was empty string, or just whitespace, pretend like we didn't see that
            continue
        else:
            print(f"Unknown command: {t}")
