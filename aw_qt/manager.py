import os
import platform
from time import sleep
import logging
import subprocess
from typing import Optional, List

import aw_core

from .config import QTSettings

logger = logging.getLogger(__name__)


def _locate_executable(name: str) -> List[str]:
    """
    Will start module from localdir if present there,
    otherwise will try to call what is available in PATH.

    Returns it as a Popen cmd list.
    """
    curr_filepath = os.path.realpath(__file__)
    curr_dir = os.path.dirname(curr_filepath)
    search_paths = [curr_dir, os.path.abspath(os.path.join(curr_dir, os.pardir))]
    exec_paths = [os.path.join(path, name) for path in search_paths]

    for exec_path in exec_paths:
        if os.path.isfile(exec_path):
            # logger.debug("Found executable for {} in: {}".format(name, exec_path))
            return [exec_path]
            break  # this break is redundant, but kept due to for-else semantics
    else:
        # TODO: Actually check if it is in PATH
        # logger.debug("Trying to start {} using PATH (executable not found in: {})"
        #              .format(name, exec_paths))
        return [name]


class Module:
    def __init__(self, name: str, testing: bool = False) -> None:
        self.name = name
        self.started = False
        self.testing = testing
        self._process = None  # type: Optional[subprocess.Popen]
        self._last_process = None  # type: Optional[subprocess.Popen]

    def start(self) -> None:
        logger.info("Starting module {}".format(self.name))

        # Create a process group, become its leader
        if platform.system() != "Windows":
            os.setpgrp()

        exec_cmd = _locate_executable(self.name)
        if self.testing:
            exec_cmd.append("--testing")
        # logger.debug("Running: {}".format(exec_cmd))

        # Don't display a console window on Windows
        # See: https://github.com/ActivityWatch/activitywatch/issues/212
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()  # type: ignore
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore
        elif platform.system() == "Darwin":
            logger.info("Macos: Disable dock icon")
            import AppKit
            AppKit.NSBundle.mainBundle().infoDictionary()["LSBackgroundOnly"] = "1"

        # There is a very good reason stdout and stderr is not PIPE here
        # See: https://github.com/ActivityWatch/aw-server/issues/27
        self._process = subprocess.Popen(exec_cmd, universal_newlines=True, startupinfo=startupinfo)

        # Should be True if module is supposed to be running, else False
        self.started = True

    def stop(self) -> None:
        """
        Stops a module, and waits until it terminates.
        """
        # TODO: What if a module doesn't stop? Add timeout to p.wait() and then do a p.kill() if timeout is hit
        if not self.started:
            logger.warning("Tried to stop module {}, but it hasn't been started".format(self.name))
            return
        elif not self.is_alive():
            logger.warning("Tried to stop module {}, but it wasn't running".format(self.name))
        else:
            if not self._process:
                logger.error("No reference to process object")
            logger.debug("Stopping module {}".format(self.name))
            if self._process:
                self._process.terminate()
            logger.debug("Waiting for module {} to shut down".format(self.name))
            if self._process:
                self._process.wait()
            logger.info("Stopped module {}".format(self.name))

        assert not self.is_alive()
        self._last_process = self._process
        self._process = None
        self.started = False

    def toggle(self) -> None:
        if self.started:
            self.stop()
        else:
            self.start()

    def is_alive(self) -> bool:
        if self._process is None:
            return False

        self._process.poll()
        # If returncode is none after p.poll(), module is still running
        return True if self._process.returncode is None else False

    def read_log(self) -> str:
        """Useful if you want to retrieve the logs of a module"""
        log_path = aw_core.log.get_latest_log_file(self.name, self.testing)
        if log_path:
            with open(log_path) as f:
                return f.read()
        else:
            return "No log file found"


class Manager:
    def __init__(self, testing: bool = False) -> None:
        self.settings = QTSettings(testing)
        self.modules = {name: Module(name, testing=testing) for name in self.settings.possible_modules}

    def get_unexpected_stops(self):
        return list(filter(lambda x: x.started and not x.is_alive(), self.modules.values()))

    def start(self, module_name):
        if module_name in self.modules.keys():
            self.modules[module_name].start()
        else:
            logger.error("Unable to start module '{}': No such module".format(module_name))

    def autostart(self, autostart_modules):

        if autostart_modules is None:
            # Modules to start are not specified. Fallback on configuration.
            autostart_modules = self.settings.autostart_modules

        # Always start aw-server first
        if "aw-server" in autostart_modules:
            self.start("aw-server")

        autostart_modules = set(autostart_modules) - {"aw-server"}
        for module_name in autostart_modules:
            self.start(module_name)

    def stop_all(self):
        for module in filter(lambda m: m.is_alive(), self.modules.values()):
            module.stop()


if __name__ == "__main__":
    manager = Manager()
    for module in manager.modules.values():
        module.start()
        sleep(2)
        assert module.is_alive()
        module.stop()
