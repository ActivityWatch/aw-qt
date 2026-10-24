import os
import sys
import json
import logging
import threading
import subprocess
import platform
import http.client
import io
import urllib.error
import urllib.request
from pathlib import Path
from glob import glob
from time import monotonic, sleep
from typing import Optional, List, Hashable, Set, Iterable, Dict, Tuple

import aw_core

from .wayland import WAYLAND_WATCHER  # noqa: F401 - retained for existing imports
from .wayland import (
    WAYLAND_WATCHERS,
    _WAYLAND_REPLACED_MODULES,
    _is_wayland,
)

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

# Modules that talk to the server on startup. aw-sync's first sync cycle fails
# if it starts before aw-server listens, so at login it is started only once
# the server answers (or after the wait times out).
SERVER_DEPENDENT_MODULES = {"aw-sync"}


def _select_wayland_watcher(available_names: Set[str]) -> Optional[str]:
    """Return the first available Wayland watcher implementation, if any."""
    for name in WAYLAND_WATCHERS:
        if name in available_names:
            return name
    return None


def _apply_wayland_substitutions(
    modules: List[str], available_names: Set[str]
) -> List[str]:
    """On Wayland, replace X11-only watchers with an available Wayland watcher.

    aw-watcher-window-wayland (and aw-awatcher, which the Linux desktop bundle
    ships) report to both aw-watcher-window and aw-watcher-afk buckets, so the
    two X11 modules are dropped and the Wayland watcher is inserted in the
    position of the first replaced module. When no Wayland watcher is
    installed, the original list is returned unchanged (the X11 modules will
    fail to capture anything on Wayland, but the rest of ActivityWatch still
    works).

    Only modules the user actually selected are replaced. An empty or
    server-only list is returned unchanged: explicitly disabling tracking
    (``--autostart-modules none`` or an empty config) must not start a watcher.

    An explicit Wayland selection that is not installed is never *replaced by a
    different Wayland watcher*: if another Wayland watcher happens to be
    installed, it is not selected on the user's behalf. Two cases follow from
    that:

    - No X11 watcher is also selected: there is nothing to substitute, so the
      original list is returned unchanged and the unavailable selection is
      still there (it may start working once installed).
    - X11 watchers are also selected: those are replaced by an installed
      Wayland watcher, and the unavailable explicit selection is dropped from
      the returned list because it cannot run. Only the return value is
      affected -- the persisted ``autostart_modules`` config is untouched, so
      the user's choice is not lost.
    """
    if not _is_wayland():
        return modules
    # The first installed explicit selection takes precedence over the automatic
    # preference. Only that implementation may run, even if both are selected.
    explicit = [name for name in modules if name in WAYLAND_WATCHERS]
    if explicit and not any(name in available_names for name in explicit):
        logger.warning("Selected Wayland watcher is not installed: %s", explicit)
        if not any(name in _WAYLAND_REPLACED_MODULES for name in modules):
            return modules
    watcher = next(
        (name for name in explicit if name in available_names),
        _select_wayland_watcher(available_names),
    )
    if watcher is None:
        logger.warning(
            "Running on Wayland but no Wayland watcher is installed; "
            "window and AFK tracking will not work. "
            "Install aw-watcher-window-wayland or aw-awatcher for Wayland support.",
        )
        return modules

    result: List[str] = []
    inserted = watcher in modules
    for name in modules:
        if name in WAYLAND_WATCHERS and name != watcher:
            continue
        if name in _WAYLAND_REPLACED_MODULES:
            if not inserted:
                result.append(watcher)
                inserted = True
            # Drop the X11-only module
        else:
            result.append(name)
    if inserted:
        logger.info(
            "Wayland session detected: substituted %s with %s",
            list(_WAYLAND_REPLACED_MODULES),
            watcher,
        )
    return result


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


# /api/0/info is a small JSON object. Cap body size and wall-clock duration so a
# slow local listener cannot freeze the tray Qt callback.
DASHBOARD_INFO_MAX_BYTES = 4096


class _DashboardInfoReader(io.RawIOBase):
    """Apply the total deadline to every socket read, including HTTP framing."""

    def __init__(self, sock, deadline: float) -> None:
        super().__init__()
        self.sock = sock
        self.deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("dashboard info probe timed out")
        self.sock.settimeout(remaining)
        return self.sock.recv_into(buffer)


class _DashboardInfoSocket:
    def __init__(self, sock, deadline: float) -> None:
        self.sock = sock
        self.deadline = deadline

    def makefile(self, mode):
        # HTTPResponse uses this stream for status, headers, chunks and body.
        return io.BufferedReader(_DashboardInfoReader(self.sock, self.deadline))


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
        self.dashboard_endpoint: Optional[Tuple[str, str, int]] = None
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

    def _probe_dashboard_listener(
        self, address: str, port: int, timeout: float = 0.2
    ) -> bool:
        """Return True only if the pinned numeric endpoint answers as ActivityWatch.

        GET /api/0/info is public even with auth enabled, so this must not send
        the API key. HTTP errors (including 404 occupants) fail closed. Body size
        and wall-clock duration are bounded so a slow listener cannot freeze Qt.
        """
        host = f"[{address}]" if ":" in address else address
        deadline = monotonic() + timeout
        conn = None
        try:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.connect()
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            conn.sock.settimeout(remaining)
            conn.request("GET", "/api/0/info")
            with http.client.HTTPResponse(
                # HTTPResponse only needs makefile(); keep the real socket owned by conn.
                _DashboardInfoSocket(conn.sock, deadline)  # type: ignore[arg-type]
            ) as resp:
                resp.begin()
                if resp.status != 200:
                    return False
                length_header = resp.getheader("Content-Length")
                if length_header is not None:
                    try:
                        length = int(length_header)
                    except ValueError:
                        return False
                    if length < 0 or length > DASHBOARD_INFO_MAX_BYTES:
                        return False
                payload = resp.read(DASHBOARD_INFO_MAX_BYTES + 1)
                if len(payload) > DASHBOARD_INFO_MAX_BYTES or resp.length not in (None, 0):
                    return False
                body = json.loads(payload.decode("utf-8"))
            return isinstance(body, dict) and "hostname" in body
        except (
            urllib.error.URLError,
            OSError,
            ValueError,
            UnicodeDecodeError,
            RecursionError,
            http.client.HTTPException,
        ):
            return False
        finally:
            if conn is not None:
                conn.close()

    def dashboard_listener_owned(self) -> bool:
        """Whether a live owned child currently answers on the pinned endpoint.

        ``dashboard_endpoint`` is launch identity (profile/address/port), not
        listener ownership. A 404 occupant or a still-initializing child must
        not receive the credential.
        """
        if self._external_server or not self.is_alive():
            return False
        endpoint = self.dashboard_endpoint
        if endpoint is None:
            return False
        _, address, port = endpoint
        return self._probe_dashboard_listener(address, port)

    def start(self, testing: bool) -> None:
        logger.info(f"Starting module {self.name}")
        self.dashboard_endpoint = None

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
        endpoint = None
        if self.name == "aw-server-rust":
            from .config import dashboard_auth
            from .profile import profile_from_env

            profile = profile_from_env(testing)
            port = self._get_server_port(testing)
            auth = dashboard_auth(profile, port) if port is not None else None
            if auth is not None and port is not None:
                address, _ = auth
                # Pin the child to the endpoint we authorize, even if the config
                # changes between this read and the child's startup read.
                exec_cmd.extend(["--host", address, "--port", str(port)])
                endpoint = (profile, address, port)
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
        self.dashboard_endpoint = endpoint
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

    def exited_cleanly(self) -> bool:
        """Whether the module's process exited with code 0 (an intentional exit).

        A clean exit is not a crash. e.g. ``aw-notify`` exits 0 when
        notifications are disabled by its opt-in gate; treating that as a crash
        makes aw-qt auto-restart it on every poll interval, forever.
        """
        if self._process is None:
            return False
        self._process.poll()
        return self._process.returncode == 0

    def clear_exited_process(self) -> None:
        """Drop state for a process that exited on its own (no-op if alive)."""
        if self.is_alive():
            return
        self._last_process = self._process
        self._process = None
        self.started = False

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
        # Serialises background autostart against manual start/stop/toggle so
        # that a pending server wait cannot override a user's module choice.
        # Reentrant: a SIGINT/SIGTERM handler runs on the main thread and calls
        # stop_all(), which takes this lock again while a toggle callback on the
        # same thread may still hold it. A plain Lock would deadlock shutdown.
        self._notify_lock = threading.RLock()
        # Set once shutdown begins so a late autostart thread cannot start
        # aw-notify after stop_all() has already run.
        self._shutting_down = False
        self._pending_autostart: Dict[str, object] = {}

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
        with self._notify_lock:
            self._pending_autostart.pop(module_name, None)
            if self._shutting_down:
                return
            module = self.resolve(module_name)
            if module is not None:
                if not module.is_alive():
                    module.start(self.testing)
            else:
                logger.error(f"Manager tried to start nonexistent module {module_name}")

    def toggle(self, module: Module) -> bool:
        """Apply a tray choice to the selected copy, cancelling deferred startup."""
        with self._notify_lock:
            self._pending_autostart.pop(module.name, None)
            if self._shutting_down:
                return module.started
            return module.toggle(self.testing)

    def autostart(
        self, autostart_modules: List[str], port: Optional[int] = None
    ) -> Optional[threading.Thread]:
        """Start the server, then the other autostart modules.

        With `port`, server-dependent modules start from a background thread
        once the server answers; that thread is returned (for tests).
        """
        # NOTE: Currently impossible to autostart a system module if a bundled module with the same name exists

        # On Wayland, transparently swap aw-watcher-window / aw-watcher-afk for
        # aw-watcher-window-wayland before validating the final list.
        available_names = {m.name for m in self.modules}
        autostart_modules = _apply_wayland_substitutions(autostart_modules, available_names)

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
        deferred = (
            sorted(SERVER_DEPENDENT_MODULES.intersection(autostart_modules))
            if port is not None
            else []
        )
        for name in autostart_modules:
            if name not in deferred:
                self.start(name)

        if port is None or not deferred:
            return None
        server_port = port
        request = object()
        with self._notify_lock:
            # A cancelled worker must not consume a later autostart request.
            self._pending_autostart.update(dict.fromkeys(deferred, request))

        def _run() -> None:
            if not wait_for_server(server_port):
                logger.warning(
                    f"Server not reachable on port {server_port}, starting {', '.join(deferred)} anyway"
                )
            with self._notify_lock:
                if self._shutting_down:
                    return
                for name in deferred:
                    if self._pending_autostart.get(name) is request:
                        self.start(name)

        t = threading.Thread(target=_run, name="server-dependent-autostart", daemon=True)
        t.start()
        return t

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
        with self._notify_lock:
            self._pending_autostart.pop(module_name, None)
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

    def stop_notify(self) -> None:
        """Stop every started aw-notify copy.

        Both a bundled and a system copy can be discovered. Manager.stop() only
        stops the first match, so a running second copy would survive a disable.
        """
        with self._notify_lock:
            for m in self.modules:
                if m.name == NOTIFY_MODULE and m.started:
                    m.stop()

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
