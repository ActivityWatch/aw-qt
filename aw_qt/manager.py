import os
import sys
import json
import logging
import threading
import subprocess
import platform
import urllib.error
import urllib.request
from pathlib import Path
from glob import glob
from time import monotonic, sleep
from typing import Optional, List, Hashable, Set, Iterable

import aw_core

logger = logging.getLogger(__name__)

# The path of aw_qt
_module_dir = os.path.dirname(os.path.realpath(__file__))

# The path of the aw-qt executable (when using PyInstaller)
_parent_dir = os.path.abspath(os.path.join(_module_dir, os.pardir))


def _log_modules(modules: List["Module"]) -> None:
    for m in modules:
        logger.debug(f" - {m.name} at {m.path}")


# aw-notify is opt-in: it is started only when the shared server-side setting
# `aw-notify` has `enabled: true` (ActivityWatch/activitywatch#1435).
NOTIFY_MODULE = "aw-notify"
NOTIFY_SETTINGS_KEY = "aw-notify"


def _notify_settings_url(port: int) -> str:
    return f"http://localhost:{port}/api/0/settings/{NOTIFY_SETTINGS_KEY}"


def read_notify_settings(port: int, timeout: float = 2.0) -> dict:
    """Return the server's `aw-notify` settings object, or {} if missing/unreachable."""
    try:
        with urllib.request.urlopen(_notify_settings_url(port), timeout=timeout) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_notify_settings_or_raise(port: int, timeout: float) -> dict:
    """Like read_notify_settings, but raises on network errors.

    A 404 (key not yet created) returns {} so write_notify_enabled can create
    the key.  All other HTTP errors and network failures raise so the caller can
    abort instead of POSTing an incomplete settings object and destroying keys
    that simply weren't readable at that moment.
    """
    try:
        with urllib.request.urlopen(_notify_settings_url(port), timeout=timeout) as resp:
            data = json.load(resp)
            return data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise


def read_notify_enabled(port: int, timeout: float = 2.0) -> bool:
    """Return True iff the server's `aw-notify` setting has `enabled: true`.

    A missing key (404), an unreachable server, or any non-boolean-true value
    means disabled: notifications are opt-in.
    """
    return read_notify_settings(port, timeout).get("enabled") is True


def write_notify_enabled(port: int, enabled: bool, timeout: float = 2.0) -> bool:
    """Persist `enabled` in the shared `aw-notify` setting, keeping all other keys.

    Returns True on success. The settings endpoint replaces the whole value, so
    the current object is read first and only `enabled` is changed.

    If the read fails due to a network error (not a missing key), the write is
    aborted and False is returned so no keys are silently overwritten.
    """
    try:
        settings = _read_notify_settings_or_raise(port, timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    settings["enabled"] = enabled
    req = urllib.request.Request(
        _notify_settings_url(port),
        data=json.dumps(settings).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _read_notify_enabled_or_raise(port: int, timeout: float = 2.0) -> bool:
    """Like read_notify_enabled, but raises on network errors instead of hiding them.

    Lets the autostart path retry a transient failure rather than treating an
    unreadable setting as "disabled".
    """
    return _read_notify_settings_or_raise(port, timeout).get("enabled") is True


def wait_for_server(port: int, timeout: float = 60.0, interval: float = 0.5) -> bool:
    """Block until the server answers on /api/0/info, or `timeout` seconds pass."""
    deadline = monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(
                f"http://localhost:{port}/api/0/info", timeout=1.0
            ):
                return True
        except (urllib.error.URLError, OSError):
            pass
        if monotonic() >= deadline:
            return False
        sleep(interval)


ignored_filenames = ["aw-cli", "aw-client", "aw-qt", "aw-qt.desktop", "aw-qt.spec"]


def filter_modules(modules: Iterable["Module"]) -> Set["Module"]:
    # Remove things matching the pattern which is not a module
    # Like aw-qt itself, or aw-cli
    return {m for m in modules if m.name not in ignored_filenames}


def is_executable(path: str, filename: str) -> bool:
    if not os.path.isfile(path):
        return False
    # On Windows, .exe/.bat/.cmd files are executables
    if platform.system() == "Windows":
        return (
            filename.endswith(".exe")
            or filename.endswith(".bat")
            or filename.endswith(".cmd")
        )
    # On Unix platforms all files having executable permissions are executables
    # We do not however want to include .desktop files
    else:  # Assumes Unix
        if not os.access(path, os.X_OK):
            return False
        if filename.endswith(".desktop"):
            return False
        return True


def _discover_modules_in_directory(path: str) -> List["Module"]:
    """Look for modules in given directory path and recursively in subdirs matching aw-*"""
    modules = []
    matches = glob(os.path.join(path, "aw-*"))
    for path in matches:
        basename = os.path.basename(path)
        name = _filename_to_name(basename)
        if name in ignored_filenames:
            continue
        if is_executable(path, basename) and basename.startswith("aw-"):
            modules.append(Module(name, Path(path), "bundled"))
        elif os.path.isdir(path) and os.access(path, os.X_OK):
            modules.extend(_discover_modules_in_directory(path))
        else:
            logger.warning(f"Found matching file but was not executable: {path}")
    return modules


def _filename_to_name(filename: str) -> str:
    if platform.system() == "Windows":
        for ext in (".exe", ".bat", ".cmd"):
            if filename.endswith(ext):
                return filename[: -len(ext)]
    return filename


def _discover_modules_bundled() -> List["Module"]:
    """Use ``_discover_modules_in_directory`` to find all bundled modules"""
    search_paths = [_module_dir, _parent_dir]
    if platform.system() == "Darwin":
        macos_dir = os.path.abspath(os.path.join(_parent_dir, os.pardir, "MacOS"))
        search_paths.append(macos_dir)
    # logger.debug(f"Searching for bundled modules in: {search_paths}")

    modules: List[Module] = []
    for path in search_paths:
        modules += _discover_modules_in_directory(path)

    modules = list(filter_modules(modules))
    logger.info(f"Found {len(modules)} bundled modules")
    _log_modules(modules)
    return modules


def _discover_modules_system() -> List["Module"]:
    """Find all aw- modules in PATH"""
    search_paths = os.get_exec_path()

    # Needed because PyInstaller adds the executable dir to the PATH
    if _parent_dir in search_paths:
        search_paths.remove(_parent_dir)

    # On macOS, when launched from Finder the PATH is minimal (/usr/bin:/bin:/usr/sbin:/sbin)
    # and doesn't include directories where AW modules are typically installed.
    # Add common macOS binary paths explicitly so system modules can be found regardless
    # of how aw-qt was launched.  Fixes: https://github.com/ActivityWatch/aw-qt/issues/96
    if platform.system() == "Darwin":
        macos_extra_paths = [
            "/opt/homebrew/bin",  # Homebrew on Apple Silicon
            "/usr/local/bin",  # Homebrew on Intel / pip global installs
            os.path.expanduser("~/.local/bin"),  # pip --user installs
        ]
        for extra_path in macos_extra_paths:
            if extra_path not in search_paths and os.path.isdir(extra_path):
                search_paths.append(extra_path)

    # logger.debug(f"Searching for system modules in PATH: {search_paths}")
    modules: List["Module"] = []
    paths = [p for p in search_paths if os.path.isdir(p)]
    for path in paths:
        try:
            ls = os.listdir(path)
        except PermissionError:
            logger.warning(f"PermissionError while listing {path}, skipping")
            continue

        for basename in ls:
            if not basename.startswith("aw-"):
                continue
            if not is_executable(os.path.join(path, basename), basename):
                continue
            name = _filename_to_name(basename)
            # Only pick the first match (to respect PATH priority)
            if name not in [m.name for m in modules]:
                modules.append(Module(name, Path(path) / basename, "system"))

    modules = list(filter_modules(modules))
    logger.info(f"Found {len(modules)} system modules")
    _log_modules(modules)
    return modules


class Module:
    def __init__(self, name: str, path: Path, type: str) -> None:
        self.name = name
        self.path = path
        assert type in ["system", "bundled"]
        self.type = type
        self.started = (
            False  # Should be True if module is supposed to be running, else False
        )
        # assert location in ["system", "bundled"]
        # self.location = "system" if _is_system_module(name) else "bundled"
        self._process: Optional[subprocess.Popen[str]] = None
        self._last_process: Optional[subprocess.Popen[str]] = None
        self._external_server: bool = False  # True if we detected an already-running server
        self._external_server_testing: bool = False
        self._external_server_probe_cache: Optional[bool] = None
        self._external_server_probe_cache_at: float = 0.0

    def __hash__(self) -> int:
        return hash((self.name, self.path))

    def __eq__(self, other: Hashable) -> bool:
        return hash(self) == hash(other)

    def __repr__(self) -> str:
        return f"<Module {self.name} at {self.path}>"

    def _get_server_port(self, testing: bool) -> Optional[int]:
        if self.name not in ("aw-server", "aw-server-rust"):
            return None

        from .config import _read_aw_server_port, _read_server_rust_port
        from .profile import profile_from_env

        profile = profile_from_env(testing)
        default_port = 5666 if testing else 5600
        if self.name == "aw-server":
            return _read_aw_server_port(profile) or default_port
        return _read_server_rust_port(profile) or default_port

    def _probe_external_server(self, testing: bool, timeout: float = 0.2) -> bool:
        port = self._get_server_port(testing)
        if port is None:
            return False

        try:
            with urllib.request.urlopen(
                f"http://localhost:{port}/api/0/info", timeout=timeout
            ):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def _probe_external_server_cached(self, testing: bool, max_age: float = 1.0) -> bool:
        now = monotonic()
        if (
            self._external_server_probe_cache is not None
            and now - self._external_server_probe_cache_at < max_age
        ):
            return self._external_server_probe_cache

        alive = self._probe_external_server(testing)
        self._external_server_probe_cache = alive
        self._external_server_probe_cache_at = now
        return alive

    def start(self, testing: bool) -> None:
        logger.info(f"Starting module {self.name}")

        # For server modules, check if a server is already running before attempting
        # to start one. This avoids port conflicts and the confusing "Restart" requirement
        # when aw-server is managed externally (e.g. via systemd or Docker).
        if self._probe_external_server(testing, timeout=1.0):
            port = self._get_server_port(testing)
            logger.info(
                f"{self.name}: server already running on port {port}, "
                "using existing instance instead of starting a new one"
            )
            self._external_server = True
            self._external_server_testing = testing
            self._external_server_probe_cache = True
            self._external_server_probe_cache_at = monotonic()
            self.started = True
            return

        exec_cmd = [str(self.path)]
        if testing:
            exec_cmd.append("--testing")
        # logger.debug("Running: {}".format(exec_cmd))

        # Don't display a console window on Windows
        # See: https://github.com/ActivityWatch/activitywatch/issues/212
        startupinfo = None
        if sys.platform == "win32" or sys.platform == "cygwin":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        elif sys.platform == "darwin":
            logger.info("macOS: Disable dock icon")
            import AppKit

            AppKit.NSBundle.mainBundle().infoDictionary()["LSBackgroundOnly"] = "1"

        # There is a very good reason stdout and stderr is not PIPE here
        # See: https://github.com/ActivityWatch/aw-server/issues/27
        self._process = subprocess.Popen(
            exec_cmd, universal_newlines=True, startupinfo=startupinfo
        )
        self.started = True

    def stop(self) -> None:
        """
        Stops a module, and waits until it terminates.
        """
        # TODO: What if a module doesn't stop? Add timeout to p.wait() and then do a p.kill() if timeout is hit
        if not self.started:
            logger.warning(
                f"Tried to stop module {self.name}, but it hasn't been started"
            )
            return
        elif self._external_server:
            # We didn't start this server, so don't stop it — it's managed externally
            logger.info(
                f"Module {self.name} is using an external server instance, not stopping it"
            )
            self._external_server = False
            self._external_server_testing = False
            self._external_server_probe_cache = None
            self._external_server_probe_cache_at = 0.0
            self.started = False
            return
        elif not self.is_alive():
            logger.warning(f"Tried to stop module {self.name}, but it wasn't running")
        else:
            if not self._process:
                logger.error("No reference to process object")
            logger.debug(f"Stopping module {self.name}")
            if self._process:
                self._process.terminate()
            logger.debug(f"Waiting for module {self.name} to shut down")
            if self._process:
                self._process.wait()
            logger.info(f"Stopped module {self.name}")

        assert not self.is_alive()
        self._last_process = self._process
        self._process = None
        self.started = False

    def toggle(self, testing: bool) -> bool:
        """Start the module if it isn't running, else stop it.

        Returns whether the module is now meant to be running.
        """
        if self.is_alive():
            self.stop()
        else:
            if self.started:
                # Process died unexpectedly, clean up state
                self.stop()
            self.start(testing)
        return self.started

    def is_alive(self) -> bool:
        if self._external_server:
            # We don't own this process, so re-probe the server instead.
            # Cache short-lived probe results to avoid blocking repeated UI poll
            # cycles on the main thread when multiple checks happen close together.
            alive = self._probe_external_server_cached(self._external_server_testing)
            if not alive:
                self._external_server = False
                self._external_server_testing = False
                self._external_server_probe_cache = None
                self._external_server_probe_cache_at = 0.0
            return alive
        if self._process is None:
            return False

        self._process.poll()
        # If returncode is none after p.poll(), module is still running
        return True if self._process.returncode is None else False

    def read_log(self, testing: bool) -> str:
        """Useful if you want to retrieve the logs of a module"""
        log_path = aw_core.log.get_latest_log_file(self.name, testing)
        if log_path:
            with open(log_path) as f:
                return f.read()
        else:
            return "No log file found"


class Manager:
    def __init__(self, testing: bool = False) -> None:
        self.modules: List[Module] = []
        self.testing = testing
        # Serialises the autostart background thread against the tray toggle so
        # that only one path can check-and-start/stop aw-notify at a time.
        # Reentrant: a SIGINT/SIGTERM handler runs on the main thread and calls
        # stop_all(), which takes this lock again while a toggle callback on the
        # same thread may still hold it. A plain Lock would deadlock shutdown.
        self._notify_lock = threading.RLock()
        # Set once shutdown begins so a late autostart thread cannot start
        # aw-notify after stop_all() has already run.
        self._shutting_down = False

        self.discover_modules()

    @property
    def modules_system(self) -> List[Module]:
        return [m for m in self.modules if m.type == "system"]

    @property
    def modules_bundled(self) -> List[Module]:
        return [m for m in self.modules if m.type == "bundled"]

    def discover_modules(self) -> None:
        # These should always be bundled with aw-qt
        modules = set(_discover_modules_bundled())
        modules |= set(_discover_modules_system())
        modules = filter_modules(modules)

        # update one by one
        for m in modules:
            if m not in self.modules:
                self.modules.append(m)

    def get_unexpected_stops(self) -> List[Module]:
        return list(filter(lambda x: x.started and not x.is_alive(), self.modules))

    def resolve(self, module_name: str) -> Optional[Module]:
        """The module that ``start``/``autostart`` runs for ``module_name``.

        Always prefers a bundled version, if available. The aw-qt menu lists
        both copies and calls the chosen module's start() directly.
        """
        bundled = [m for m in self.modules_bundled if m.name == module_name]
        system = [m for m in self.modules_system if m.name == module_name]
        if bundled:
            return bundled[0]
        if system:
            return system[0]
        return None

    def start(self, module_name: str) -> None:
        module = self.resolve(module_name)
        if module is not None:
            module.start(self.testing)
        else:
            logger.error(f"Manager tried to start nonexistent module {module_name}")

    def autostart(self, autostart_modules: List[str]) -> None:
        # NOTE: Currently impossible to autostart a system module if a bundled module with the same name exists

        # We only want to autostart modules that are both in found modules and are asked to autostart.
        for name in autostart_modules:
            if name not in [m.name for m in self.modules]:
                logger.error(f"Module {name} not found")
        autostart_modules = list(set(autostart_modules))

        # Start aw-server-rust first
        if "aw-server-rust" in autostart_modules:
            self.start("aw-server-rust")
        elif "aw-server" in autostart_modules:
            self.start("aw-server")

        autostart_modules = list(
            set(autostart_modules) - {"aw-server", "aw-server-rust"}
        )
        for name in autostart_modules:
            self.start(name)

    def autostart_notify_if_enabled(self, port: int) -> threading.Thread:
        """Start aw-notify once the server is up, iff the shared `enabled` flag is true.

        Runs in a background thread so a slow server start does not block the
        tray. Does nothing when no aw-notify module is installed, or when it was
        already started explicitly through `autostart_modules`.

        Returns the background thread so callers (e.g. tests) can join it.
        """
        if NOTIFY_MODULE not in [m.name for m in self.modules]:
            t = threading.Thread(target=lambda: None, name="aw-notify-autostart", daemon=True)
            t.start()
            return t

        def _run() -> None:
            if not wait_for_server(port):
                logger.warning(
                    f"Server not reachable on port {port}, not starting {NOTIFY_MODULE}"
                )
                return
            with self._notify_lock:
                if self._shutting_down:
                    return
                enabled = None
                for _ in range(3):
                    try:
                        enabled = _read_notify_enabled_or_raise(port)
                        break
                    except (urllib.error.URLError, OSError, ValueError) as e:
                        logger.warning(f"Could not read {NOTIFY_MODULE} setting: {e}")
                        sleep(1.0)
                if enabled is None:
                    logger.warning(
                        f"Could not read {NOTIFY_MODULE} setting, not starting"
                    )
                    return
                if enabled:
                    logger.info(f"{NOTIFY_MODULE} enabled in settings, starting")
                    self._start_notify_locked()
                else:
                    logger.info(f"{NOTIFY_MODULE} not enabled in settings, not starting")

        t = threading.Thread(target=_run, name="aw-notify-autostart", daemon=True)
        t.start()
        return t

    def stop(self, module_name: str) -> None:
        for m in self.modules:
            if m.name == module_name:
                m.stop()
                break
        else:
            logger.error(f"Manager tried to stop nonexistent module {module_name}")

    def start_notify(self) -> None:
        """Start aw-notify unless it is already running or shutdown has begun.

        Shared by the autostart thread and the tray toggle; holds _notify_lock
        so the two cannot both start a second instance.
        """
        with self._notify_lock:
            self._start_notify_locked()

    def _start_notify_locked(self) -> None:
        # Caller must hold _notify_lock.
        if self._shutting_down:
            return
        if any(m.name == NOTIFY_MODULE and m.started for m in self.modules):
            return
        self.start(NOTIFY_MODULE)

    def stop_all(self) -> None:
        with self._notify_lock:
            self._shutting_down = True
        for module in filter(lambda m: m.is_alive(), self.modules):
            module.stop()

    def print_status(self, module_name: Optional[str] = None) -> None:
        header = "name                status      type"
        if module_name:
            # find module
            module = next((m for m in self.modules if m.name == module_name), None)
            if module:
                logger.info(header)
                self._print_status_module(module)
            else:
                logger.error(f"Module {module_name} not found")
        else:
            logger.info(header)
            for module in self.modules:
                self._print_status_module(module)

    def _print_status_module(self, module: Module) -> None:
        logger.info(
            f"{module.name:18}  {'running' if module.is_alive() else 'stopped' :10}  {module.type}"
        )


def main_test():
    manager = Manager()
    for module in manager.modules:
        module.start(testing=True)
        sleep(2)
        assert module.is_alive()
        module.stop()


if __name__ == "__main__":
    main_test()
