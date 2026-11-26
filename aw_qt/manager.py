import os
import sys
import json
import logging
import threading
import subprocess
import platform
import socket
import http.client
import io
import urllib.error
import urllib.request
from pathlib import Path
from glob import glob
from time import monotonic, sleep
from typing import Optional, List, Hashable, Set, Iterable, Dict, Tuple, Callable, ContextManager
from contextlib import contextmanager, nullcontext

import aw_core
import aw_core.dirs
import platformdirs

from .config import child_environment
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

# Honor-system flag set from the web UI Settings page ("I already support
# ActivityWatch"). Stored in the server's generic settings store, so every
# client reading /api/0/settings sees the same answer. Unset means "show the
# support prompts", which is also what an unreachable server must mean.
SUPPORTER_SETTINGS_KEY = "supporter"

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


def read_notify_enabled_or_none(port: int, timeout: float = 2.0) -> Optional[bool]:
    """Like read_notify_enabled, but None when the server could not be read.

    Lets the tray keep the last successfully read value instead of showing an
    unreachable server as "notifications off".
    """
    try:
        return _read_notify_enabled_or_raise(port, timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return None


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


def read_supporter(port: int, timeout: float = 2.0) -> bool:
    """Return True iff the server's `supporter` setting is boolean true.

    The web UI stores this as a plain boolean (`/api/0/settings/supporter`), and
    skips writing it while false, so a missing key (404), an unreachable server
    or any non-boolean-true value all mean "not a supporter" — keep showing the
    support prompts, which is today's behaviour.
    """
    try:
        with urllib.request.urlopen(
            f"http://localhost:{port}/api/0/settings/{SUPPORTER_SETTINGS_KEY}",
            timeout=timeout,
        ) as resp:
            value = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return value is True


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


def _read_info_body(response, deadline: float) -> bytes:
    """Read at most DASHBOARD_INFO_MAX_BYTES of an info response before *deadline*.

    urlopen's timeout applies per socket read, so a listener that trickles
    bytes could otherwise hold the caller (the Qt thread) indefinitely. The
    check runs between reads, so the total is bounded by deadline + timeout.
    """
    body = b""
    while True:
        if monotonic() > deadline:
            raise TimeoutError("server info read timed out")
        chunk = response.read1(DASHBOARD_INFO_MAX_BYTES + 1 - len(body))
        if not chunk:
            return body
        body += chunk
        if len(body) > DASHBOARD_INFO_MAX_BYTES:
            raise ValueError("server info response is too large")


SERVER_MODULES = ("aw-server", "aw-server-rust")


def _server_data_roots(appname: str) -> Set[Path]:
    """Every per-user data root an aw-server could use for ``appname``.

    aw-core (Python aw-server) uses platformdirs with the default app author;
    aw-server-rust uses ``%LOCALAPPDATA%\\<appname>`` on Windows, and v0.14.0
    misplaced it under roaming ``%APPDATA%``. On Linux/macOS these coincide.
    """
    return {
        Path(platformdirs.user_data_dir(appname)),
        Path(platformdirs.user_data_dir(appname, appauthor=False)),
        Path(platformdirs.user_data_dir(appname, appauthor=False, roaming=True)),
    }


def _local_server_device_ids(profile: str) -> Set[str]:
    """``device_id``s of this user's own server datastores for ``profile``.

    Read-only: unlike aw_core.dirs.get_data_dir() this never creates
    directories, so probing does not leave empty datastores behind.
    """
    from .config import _with_profile_env

    with _with_profile_env(profile):
        appname = aw_core.dirs._get_appname()
    ids: Set[str] = set()
    for root in _server_data_roots(appname):
        for server in ("aw-server-rust", "aw-server"):
            try:
                device_id = (root / server / "device_id").read_text().strip()
            except (OSError, UnicodeDecodeError):
                continue
            if device_id:
                ids.add(device_id)
    return ids


def _own_device_ids(testing: bool) -> Set[str]:
    from .config import _spawn_env_lock
    from .profile import profile_from_env

    with _spawn_env_lock:
        return _local_server_device_ids(profile_from_env(testing))


def _port_accepts_connections(port: int, timeout: float = 0.2) -> bool:
    """Whether something listens on ``port``, independent of its HTTP health."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _identity_mismatch(listener_id: Optional[str], local_ids: Set[str]) -> Optional[str]:
    """Why an answering listener is not this user's server, or None if it is.

    Shared by the adoption and post-spawn paths so both treat a missing
    ``device_id`` the same way: it proves nothing, so it is refused.
    """
    if listener_id is None:
        return "the server reports no device_id, so its datastore cannot be verified"
    if listener_id not in local_ids:
        return (
            f"it serves another datastore (device_id {listener_id}, this user's "
            f"is {', '.join(sorted(local_ids))}), possibly another user's"
        )
    return None


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
        self._external_server_port: Optional[int] = None
        self._external_server_probe_cache: Optional[bool] = None
        self._external_server_probe_cache_at: float = 0.0
        # Port refused as foreign or unverifiable. Never cleared: the refusal
        # lasts until aw-qt restarts.
        self.foreign_server_port: Optional[int] = None

    def __hash__(self) -> int:
        return hash((self.name, self.path))

    def __eq__(self, other: Hashable) -> bool:
        return hash(self) == hash(other)

    def __repr__(self) -> str:
        return f"<Module {self.name} at {self.path}>"

    def _get_server_port(self, testing: bool) -> Optional[int]:
        if self.name not in ("aw-server", "aw-server-rust"):
            return None

        from .config import _read_aw_server_port, _read_server_rust_port, _spawn_env_lock
        from .profile import profile_from_env

        with _spawn_env_lock:
            profile = profile_from_env(testing)
            default_port = 5666 if testing else 5600
            if self.name == "aw-server":
                return _read_aw_server_port(profile) or default_port
            return _read_server_rust_port(profile) or default_port

    def _probe_external_server(self, testing: bool, timeout: float = 0.2) -> bool:
        # Reprobe an adopted endpoint without config/environment lookup: callers
        # such as Manager.start may hold _notify_lock during is_alive().
        port = self._external_server_port if self._external_server else self._get_server_port(testing)
        if port is None:
            return False

        try:
            with urllib.request.urlopen(
                f"http://localhost:{port}/api/0/info", timeout=timeout
            ):
                self._external_server_port = port
                return True
        except (urllib.error.URLError, OSError):
            return False

    def _external_server_device_id(self, port: int, timeout: float) -> Optional[str]:
        """The listener's ``device_id``, or None if its info response has none.

        Raises if the info response cannot be fetched or parsed.
        """
        deadline = monotonic() + timeout
        with urllib.request.urlopen(
            f"http://localhost:{port}/api/0/info", timeout=timeout
        ) as response:
            info = json.loads(_read_info_body(response, deadline).decode("utf-8"))
        if not isinstance(info, dict):
            raise ValueError("Server info is not an object")
        device_id = info.get("device_id")
        if isinstance(device_id, str) and device_id.strip():
            return device_id.strip()
        return None

    def _process_exited(self) -> bool:
        # Popen.poll() returns None while the child runs. Tests patch Popen
        # with a mock whose poll() is not an int, so require an int.
        if self._process is None:
            return False
        return isinstance(self._process.poll(), int)

    def _refuse_port(self, port: int, reason: str) -> None:
        logger.error(
            f"{self.name}: not using port {port}: {reason}. Dependent modules stay "
            "stopped until aw-qt restarts. If another server owns the port, "
            "configure a distinct server port for this user."
        )
        self.foreign_server_port = port
        # Do not leave a spawned child hidden behind started=False: stop()
        # would then refuse to manage it.
        process = self._process
        if process is not None:
            if not self._process_exited():
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self._last_process = process
            self._process = None
        self.dashboard_endpoint = None
        self.started = False

    def _verify_spawned_listener(
        self, port: Optional[int], testing: bool, wait: float = 1.0
    ) -> None:
        """Refuse the port unless its listener proves to be this user's server.

        The pre-spawn probe can miss a listener that answers slowly. The child
        then exits with AddrInUse while that listener keeps the port.
        """
        process = self._process
        if port is None or process is None:
            return
        local_ids = _own_device_ids(testing)
        if not local_ids:
            # First run or an externally managed datastore: nothing to compare.
            return
        deadline = monotonic() + wait
        while monotonic() < deadline and self._process is process:
            exited = self._process_exited()
            try:
                listener_id = self._external_server_device_id(port, timeout=0.2)
            except (urllib.error.URLError, OSError, ValueError):
                if exited:
                    break
                sleep(0.05)
                continue
            reason = _identity_mismatch(listener_id, local_ids)
            if reason:
                self._refuse_port(port, reason)
            return
        # Nothing identified itself. A free port is a slow or broken child; a
        # held one may belong to someone else.
        if self._process is process and _port_accepts_connections(port):
            self._refuse_port(port, "its listener could not be identified")

    def _refuse_unverified_listener(self, port: Optional[int], testing: bool) -> None:
        """Refuse a port someone else holds when our server failed to launch."""
        if port is None:
            return
        local_ids = _own_device_ids(testing)
        if not local_ids or not _port_accepts_connections(port):
            return
        try:
            listener_id = self._external_server_device_id(port, timeout=0.5)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason: Optional[str] = f"its identity cannot be read ({exc})"
        else:
            reason = _identity_mismatch(listener_id, local_ids)
        if reason:
            self._refuse_port(port, reason)

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
        # Pass socket-form IPv6: Python 3.9 retains brackets with an explicit port.
        host = address
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

    def start(
        self, testing: bool,
        launch_guard: Optional[Callable[[], ContextManager[bool]]] = None,
    ) -> None:
        if self.foreign_server_port is not None:
            logger.error(
                f"Not starting {self.name}: port {self.foreign_server_port} was "
                "refused. Restart aw-qt after fixing the server."
            )
            return
        logger.info(f"Starting module {self.name}")

        # For server modules, check if a server is already running before attempting
        # to start one. This avoids port conflicts and the confusing "Restart" requirement
        # when aw-server is managed externally (e.g. via systemd or Docker).
        if self._probe_external_server(testing, timeout=1.0):
            port = self._external_server_port
            local_ids = _own_device_ids(testing) if port is not None else set()
            if port is not None and local_ids:
                # Adopting another datastore's server would send this user's
                # watcher data into it (ActivityWatch/activitywatch#75).
                try:
                    listener_id = self._external_server_device_id(port, timeout=1.0)
                except (urllib.error.URLError, OSError, ValueError) as exc:
                    self._refuse_port(port, f"its identity cannot be read ({exc})")
                    return
                reason = _identity_mismatch(listener_id, local_ids)
                if reason:
                    self._refuse_port(port, reason)
                    return
            logger.info(
                f"{self.name}: server already running on port {port}, "
                "using existing instance instead of starting a new one"
            )
            with launch_guard() if launch_guard else nullcontext(True) as allowed:
                if not allowed:
                    return
                self.dashboard_endpoint = None
                self._external_server = True
                self._external_server_testing = testing
                self._external_server_probe_cache = True
                self._external_server_probe_cache_at = monotonic()
                self.started = True
            return

        exec_cmd = [str(self.path)]
        if testing:
            exec_cmd.append("--testing")
        from .config import _spawn_env_lock, dashboard_auth
        from .profile import profile_from_env

        endpoint = None
        # Resolve identity and environment in one critical section. Never take
        # the manager launch guard until this environment lock is released.
        with _spawn_env_lock:
            env = child_environment()
            listen_port = self._get_server_port(testing)
            if self.name == "aw-server-rust":
                profile = profile_from_env(testing)
                port = listen_port
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
        # Stop/shutdown may cancel preparation. Once admitted, publish the
        # handle before releasing the guard so shutdown cannot miss the child.
        try:
            with launch_guard() if launch_guard else nullcontext(True) as allowed:
                if not allowed:
                    return
                self._process = subprocess.Popen(
                    exec_cmd,
                    universal_newlines=True,
                    startupinfo=startupinfo,
                    env=env,
                )
                self.dashboard_endpoint = endpoint
                self.started = True
        except Exception:
            # The probe can miss a slow listener, and autostart carries on past
            # a launch failure.
            self._refuse_unverified_listener(listen_port, testing)
            raise
        # Popen only confirms that a child was created. Verify outside the launch
        # guard: it waits on the network.
        self._verify_spawned_listener(listen_port, testing)

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
        log_path = aw_core.log.get_latest_log_file(
            self.name, testing
        ) or _latest_rust_log_file(self.name, testing)
        if log_path:
            # Logs are UTF-8 (aw-core and the Rust modules). The locale encoding
            # (e.g. cp1252 on Windows) can fail on them, and this runs inside
            # the crash dialog, so never let decoding raise.
            with open(log_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        else:
            return "No log file found"


def _latest_rust_log_file(name: str, testing: bool) -> Optional[str]:
    """Newest log of a Rust module (aw-server-rust, aw-sync) on Windows.

    aw-core's platformdirs log dir on Windows is
    %LOCALAPPDATA%\\activitywatch\\activitywatch\\Logs (appauthor defaults to the
    appname), but the Rust modules log to %LOCALAPPDATA%\\activitywatch\\Logs.
    Linux and macOS layouts already agree.
    """
    if sys.platform != "win32":
        return None
    log_dir = os.path.join(
        platformdirs.user_log_dir(aw_core.dirs._get_appname(), appauthor=False), name
    )
    try:
        files = os.listdir(log_dir)
    except OSError:
        return None
    files = [f for f in files if name in f and ("testing" in f) == testing]
    if not files:
        return None
    return os.path.join(log_dir, max(files))


class Manager:
    def __init__(self, testing: bool = False) -> None:
        self.modules: List[Module] = []
        self.testing = testing
        # Serialises background autostart against manual start/stop/toggle so
        # that a pending server wait cannot override a user's module choice.
        # Reentrant: a SIGINT/SIGTERM handler runs on the main thread and calls
        # stop_all(), which takes this lock again while a toggle callback on the
        # same thread may still hold it. A plain Lock would deadlock shutdown.
        # Never hold this lock while taking ``_spawn_env_lock`` (child env
        # snapshot / dashboard_auth): that inversion deadlocks signal-driven
        # stop_all() (ActivityWatch/aw-qt#144).
        self._notify_lock = threading.RLock()
        # Set once shutdown begins so a late autostart thread cannot start
        # aw-notify after stop_all() has already run.
        self._shutting_down = False
        self._pending_autostart: Dict[str, object] = {}
        # Names claimed for spawn so two callers cannot both Popen after
        # releasing _notify_lock.
        self._starting: Dict[str, Tuple[Module, object]] = {}

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

    @property
    def foreign_server_port(self) -> Optional[int]:
        """A refused server port, if any. Set until aw-qt restarts."""
        return next(
            (
                m.foreign_server_port
                for m in self.modules
                if m.foreign_server_port is not None
            ),
            None,
        )

    def _start_blocked(self, module: Module) -> bool:
        # Watchers post to their own aw-client port, which aw-qt does not know,
        # so any refusal blocks every dependent and the refused server itself.
        if self.foreign_server_port is None:
            return False
        return (
            module.name not in SERVER_MODULES
            or module.foreign_server_port is not None
        )

    def _stop_refused_dependents(self) -> None:
        # Caller holds _notify_lock.
        if self.foreign_server_port is None:
            return
        self._pending_autostart.clear()
        for module in self.modules:
            if module.name not in SERVER_MODULES and module.started:
                module.stop()

    def _take_start_locked(self, module_name: str) -> Optional[Tuple[Module, object]]:
        """Claim ``module_name`` for spawn. Caller holds ``_notify_lock``.

        Returns the module and request identity to start after releasing the lock.
        Spawn takes
        ``_spawn_env_lock``; holding ``_notify_lock`` across that inverts with
        a signal-driven ``stop_all()``.
        """
        self._pending_autostart.pop(module_name, None)
        if self._shutting_down:
            return None
        module = self.resolve(module_name)
        if module is None:
            logger.error(
                "Manager tried to start nonexistent module %s", module_name
            )
            return None
        if module.is_alive() or module.name in self._starting:
            return None
        request = (module, object())
        self._starting[module.name] = request
        return module, request

    def _spawn_claimed(self, claim: Optional[Tuple[Module, object]]) -> None:
        """Prepare outside ``_notify_lock``; serialize spawn and publication."""
        if claim is None:
            return
        module, request = claim
        with self._notify_lock:
            if self._starting.get(module.name) is not request or self._shutting_down:
                return
            if self._start_blocked(module):
                logger.error(
                    f"Not starting {module.name}: server port "
                    f"{self.foreign_server_port} was refused"
                )
                self._starting.pop(module.name, None)
                return

        @contextmanager
        def launch_guard():
            # Only process creation/publication holds this lock, never profile
            # lookup or environment snapshot. Shutdown can cancel preparation
            # without waiting for a lock held by its interrupted main thread.
            with self._notify_lock:
                allowed = (
                    not self._shutting_down
                    and self._starting.get(module.name) is request
                    and not self._start_blocked(module)
                )
                try:
                    yield allowed
                finally:
                    # A main-thread signal handler may reenter the RLock while
                    # Popen is running. Retire a handle published after that stop.
                    if allowed and (
                        self._shutting_down
                        or self._starting.get(module.name) is not request
                    ) and module.started:
                        module.stop()

        try:
            module.start(self.testing, launch_guard=launch_guard)
        finally:
            with self._notify_lock:
                if self._starting.get(module.name) is request:
                    self._starting.pop(module.name, None)
                # A failed launch can also record a refusal.
                self._stop_refused_dependents()

    def start(self, module_name: str) -> None:
        with self._notify_lock:
            module = self._take_start_locked(module_name)
        self._spawn_claimed(module)

    def _autostart_one(self, module_name: str) -> None:
        """Start one autostart module; a launch failure must not stop the rest.

        A module can be discovered yet fail to launch (e.g. a watcher on PATH
        whose shebang interpreter was removed, or `import AppKit` failing on
        macOS). Letting that escape would skip every later module and abort
        aw-qt before the tray appears.
        """
        try:
            self.start(module_name)
        except Exception:
            logger.exception(f"Failed to launch module {module_name}")

    def _autostart_claimed(self, claim: Tuple[Module, object]) -> None:
        """``_autostart_one`` for an already-claimed deferred start.

        Every claim must reach ``_spawn_claimed``, which releases it, so one
        failed launch must not leave the later modules claimed forever.
        """
        try:
            self._spawn_claimed(claim)
        except Exception:
            logger.exception(f"Failed to launch module {claim[0].name}")

    def restart(self, module: Module) -> bool:
        """Restart the selected copy through the same shutdown-safe launch guard."""
        with self._notify_lock:
            if self._shutting_down:
                return False
            self._pending_autostart.pop(module.name, None)
            self._starting.pop(module.name, None)
            module.stop()
            request = (module, object())
            self._starting[module.name] = request
        self._spawn_claimed((module, request))
        return module.started

    def toggle(self, module: Module) -> Optional[bool]:
        """Apply a tray choice to the selected copy, cancelling deferred startup.

        Returns the new started state, or None when a start was refused because
        of a datastore refusal: the user did not choose to disable the module.
        """
        with self._notify_lock:
            self._pending_autostart.pop(module.name, None)
            if self._shutting_down:
                return module.started
            if module.is_alive():
                # Commit to Stop: re-running toggle could observe an expired
                # external-listener cache and enter Start under this lock.
                module.stop()
                return False
            if self._start_blocked(module):
                if module.started:
                    module.stop()  # Unticking a crashed module is a choice.
                    return False
                return None
            # Tray starts run synchronously, so a preparing claim belongs to
            # background startup, not a previous click. Replace it with this
            # explicit Start choice; the stale worker's guard will reject it.
            self._starting.pop(module.name, None)
            if module.started:
                module.stop()  # Clean up an exited process before restarting.
            request = (module, object())
            self._starting[module.name] = request
        self._spawn_claimed((module, request))
        if not module.started and self._start_blocked(module):
            return None
        return module.started

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
            self._autostart_one("aw-server-rust")
        elif "aw-server" in autostart_modules:
            self._autostart_one("aw-server")

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
                self._autostart_one(name)

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
            claimed: List[Tuple[Module, object]] = []
            with self._notify_lock:
                if self._shutting_down:
                    return
                for name in deferred:
                    if self._pending_autostart.get(name) is request:
                        module = self._take_start_locked(name)
                        if module is not None:
                            claimed.append(module)
            for claim in claimed:
                self._autostart_claimed(claim)

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
                    module = self._start_notify_locked()
                else:
                    logger.info(f"{NOTIFY_MODULE} not enabled in settings, not starting")
                    module = None
            self._spawn_claimed(module)

        t = threading.Thread(target=_run, name="aw-notify-autostart", daemon=True)
        t.start()
        return t

    def stop(self, module_name: str) -> None:
        with self._notify_lock:
            self._pending_autostart.pop(module_name, None)
            self._starting.pop(module_name, None)
            for m in self.modules:
                if m.name == module_name:
                    m.stop()
                    break
            else:
                logger.error(f"Manager tried to stop nonexistent module {module_name}")

    def start_notify(self) -> None:
        """Start aw-notify unless it is already running or shutdown has begun.

        Shared by the autostart thread and the tray toggle. The start
        decision holds _notify_lock so the two cannot both claim a second
        instance. Spawn runs after the lock is released.
        """
        with self._notify_lock:
            module = self._start_notify_locked()
        self._spawn_claimed(module)

    def _start_notify_locked(self) -> Optional[Tuple[Module, object]]:
        # Caller must hold _notify_lock. Returns the module to spawn after
        # releasing it; spawn takes _spawn_env_lock.
        if self._shutting_down:
            return None
        if any(
            m.name == NOTIFY_MODULE and (m.started or m.name in self._starting)
            for m in self.modules
        ):
            return None
        return self._take_start_locked(NOTIFY_MODULE)

    def stop_notify(self) -> None:
        """Stop every started aw-notify copy.

        Both a bundled and a system copy can be discovered. Manager.stop() only
        stops the first match, so a running second copy would survive a disable.
        """
        with self._notify_lock:
            self._starting.pop(NOTIFY_MODULE, None)
            self._pending_autostart.pop(NOTIFY_MODULE, None)
            for m in self.modules:
                if m.name == NOTIFY_MODULE and m.started:
                    m.stop()

    def stop_all(self) -> None:
        with self._notify_lock:
            self._shutting_down = True
            self._starting.clear()
            self._pending_autostart.clear()
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
