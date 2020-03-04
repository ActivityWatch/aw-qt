import os
import platform
from glob import glob
from time import sleep
import logging
import subprocess
import shutil
from typing import Optional, List, Dict, Set

import aw_core

from .config import AwQtSettings

logger = logging.getLogger(__name__)

_module_dir = os.path.dirname(os.path.realpath(__file__))
_parent_dir = os.path.abspath(os.path.join(_module_dir, os.pardir))
_search_paths = [_module_dir, _parent_dir]


def _locate_bundled_executable(name: str) -> Optional[str]:
    """Returns the path to the module executable if it exists in the bundle, else None."""
    _exec_paths = [os.path.join(path, name) for path in _search_paths]

    # Look for it in the installation path
    for exec_path in _exec_paths:
        if os.path.isfile(exec_path):
            # logger.debug("Found executable for {} in: {}".format(name, exec_path))
            return exec_path
    return None


def _is_system_module(name: str) -> bool:
    """Checks if a module with a particular name exists in PATH"""
    return shutil.which(name) is not None


def _locate_executable(name: str) -> Optional[str]:
    """
    Will return the path to the executable if bundled,
    otherwise returns the name if it is available in PATH.

    Used when calling Popen.
    """
    exec_path = _locate_bundled_executable(name)
    if exec_path is not None:  # Check if it exists in bundle
        return exec_path
    elif _is_system_module(name):  # Check if it's in PATH
        return name
    else:
        logger.warning("Could not find module '{}' in installation directory or PATH".format(name))
        return None


def _discover_modules_bundled() -> List[str]:
    # Look for modules in source dir and parent dir
    modules = []
    for path in _search_paths:
        matches = glob(os.path.join(path, "aw-*"))
        for match in matches:
            if os.path.isfile(match) and os.access(match, os.X_OK):
                name = os.path.basename(match)
                modules.append(name)
            else:
                logger.warning("Found matching file but was not executable: {}".format(match))

    # This prints "Found... set()" if we found 0 bundled modules. Can be a bit misleading.
    logger.info("Found bundled modules: {}".format(set(modules)))
    return modules


def _discover_modules_system() -> List[str]:
    search_paths = os.environ["PATH"].split(":")
    modules = []
    for path in search_paths:
        if os.path.isdir(path):
            files = os.listdir(path)
            for filename in files:
                if "aw-" in filename:
                    modules.append(filename)

    logger.info("Found system modules: {}".format(set(modules)))
    return modules


class Module:
    def __init__(self, name: str, testing: bool = False) -> None:
        self.name = name
        self.started = False  # Should be True if module is supposed to be running, else False
        self.testing = testing
        self.location = "system" if _is_system_module(name) else "bundled"
        self._process: Optional[subprocess.Popen[str]] = None
        self._last_process: Optional[subprocess.Popen[str]] = None

    def start(self) -> None:
        logger.info("Starting module {}".format(self.name))

        # Create a process group, become its leader
        # TODO: This shouldn't go here
        if platform.system() != "Windows":
            os.setpgrp()

        exec_path = _locate_executable(self.name)
        if exec_path is None:
            logger.error("Tried to start nonexistent module {}".format(self.name))
        else:
            exec_cmd = [exec_path]
            if self.testing:
                exec_cmd.append("--testing")
            # logger.debug("Running: {}".format(exec_cmd))

        # Don't display a console window on Windows
        # See: https://github.com/ActivityWatch/activitywatch/issues/212
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO() #type: ignore
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW #type: ignore
        elif platform.system() == "Darwin":
            logger.info("Macos: Disable dock icon")
            import AppKit
            AppKit.NSBundle.mainBundle().infoDictionary()["LSBackgroundOnly"] = "1"

        # There is a very good reason stdout and stderr is not PIPE here
        # See: https://github.com/ActivityWatch/aw-server/issues/27
        self._process = subprocess.Popen(exec_cmd, universal_newlines=True, startupinfo=startupinfo)
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
                logger.error("")
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
        self.settings: AwQtSettings = AwQtSettings(testing)
        self.modules: Dict[str, Module] = {}
        self.autostart_modules: Set[str] = set(self.settings.autostart_modules)
        self.testing = testing

        for name in self.settings.possible_modules:
            if _locate_executable(name):
                self.modules[name] = Module(name, testing=testing)
            else:
                logger.warning("Module '{}' not found but was in possible modules".format(name))
        # Is this actually a good way to do this? merged from dev/autodetect-modules
        self.discover_modules()

    def discover_modules(self) -> None:
        # These should always be bundled with aw-qt
        found_modules = set(_discover_modules_bundled())
        found_modules |= set(_discover_modules_system())
        found_modules ^= {"aw-qt"}  # Exclude self

        for m_name in found_modules:
            if m_name not in self.modules:
                self.modules[m_name] = Module(m_name, testing=self.testing)

    def get_unexpected_stops(self) -> List[Module]:
        return list(filter(lambda x: x.started and not x.is_alive(), self.modules.values()))

    def start(self, module_name: str) -> None:
        if module_name in self.modules.keys():
            self.modules[module_name].start()
        else:
            logger.debug("Manager tried to start nonexistent module {}".format(module_name))

    def autostart(self, autostart_modules: Optional[List[str]]) -> None:
        if autostart_modules is None:
            autostart_modules = []
        if len(autostart_modules) > 0:
            logger.info("Modules to start weren't specified in CLI arguments. Falling back to configuration.")
            autostart_modules = self.settings.autostart_modules
        # We only want to autostart modules that are both in found modules and are asked to autostart.
        modules_to_start = set(autostart_modules).intersection(set(self.modules.keys()))

        # Start aw-server-rust first
        if "aw-server-rust" in modules_to_start:
            self.start("aw-server-rust")
        elif "aw-server" in modules_to_start:
            self.start("aw-server")

        modules_to_start = set(autostart_modules) - {"aw-server", "aw-server-rust"}
        for module_name in modules_to_start:
            self.start(module_name)

    def stop_all(self) -> None:
        for module in filter(lambda m: m.is_alive(), self.modules.values()):
            module.stop()


if __name__ == "__main__":
    manager = Manager()
    for module in manager.modules.values():
        module.start()
        sleep(2)
        assert module.is_alive()
        module.stop()
