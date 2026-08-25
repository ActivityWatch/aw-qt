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
from PyQt6.QtCore import QLockFile
from aw_core.log import setup_logging

from .manager import Manager
from .config import AwQtSettings
from .profile import (
    DEFAULT_PROFILE,
    export_profile,
    is_testing,
    profile_suffix,
    resolve_profile,
)

logger = logging.getLogger(__name__)


def _acquire_single_instance_lock(profile: str) -> QLockFile:
    """Ensure only one instance of aw-qt runs at a time.

    Uses QLockFile for cross-platform single-instance enforcement.
    The returned lock must be kept alive for the duration of the process.
    Exits with code 1 if another instance is already running.
    """
    import aw_core.dirs

    data_dir = aw_core.dirs.get_data_dir("aw-qt")
    lock_path = os.path.join(data_dir, f"aw-qt{profile_suffix(profile)}.lock")

    lock = QLockFile(lock_path)
    lock.setStaleLockTime(0)  # Only release when the process explicitly unlocks

    if not lock.tryLock(100):
        if lock.error() == QLockFile.LockError.LockFailedError:
            _ok, pid, _hostname, _appname = lock.getLockInfo()
            msg = f"Another instance of aw-qt is already running (PID {pid}). Exiting."
        else:
            msg = f"Failed to acquire instance lock ({lock.error()}). Exiting."
        logger.warning(msg)
        print(msg)
        sys.exit(1)

    return lock


@click.command("aw-qt", help="A trayicon and service manager for ActivityWatch")
@click.option(
    "--testing", is_flag=True, help="Run the trayicon and services in testing mode"
)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help=(
        "Run an isolated instance under this profile name (data, config, port "
        "and lockfile are separate). --testing is an alias for --profile testing."
    ),
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
    profile_name: Optional[str],
    verbose: bool,
    autostart_modules: Optional[str],
    no_gui: bool,
    interactive_cli: bool,
) -> None:
    # Since the .app can crash when started from Finder for unknown reasons, we send a syslog message here to make debugging easier.
    if platform.system() == "Darwin":
        subprocess.call("syslog -s 'aw-qt started'", shell=True)

    try:
        profile = resolve_profile(profile_name, testing)
    except ValueError as e:
        raise click.UsageError(str(e)) from e
    testing = is_testing(profile)
    # Modules are spawned as subprocesses and inherit the profile from here.
    export_profile(profile)

    setup_logging("aw-qt", testing=testing, verbose=verbose, log_file=True)
    logger.info("Started aw-qt...")
    if profile != DEFAULT_PROFILE:
        logger.info(f"Running with profile: {profile}")

    # Since the .app can crash when started from Finder for unknown reasons, we send a syslog message here to make debugging easier.
    if platform.system() == "Darwin":
        subprocess.call("syslog -s 'aw-qt successfully started logging'", shell=True)

    # Prevent multiple instances from running simultaneously
    _lock = _acquire_single_instance_lock(profile)  # noqa: F841 (must stay alive)

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

    config = AwQtSettings(profile=profile)
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
        error_code = trayicon.run(
            manager, testing=testing, port=config.port, profile=profile
        )
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
