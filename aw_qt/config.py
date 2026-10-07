import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Tuple

import tomlkit
from aw_core import dirs
from aw_core.config import load_config_toml

from .profile import DEFAULT_PROFILE, ENV_VAR, TESTING_PROFILE, is_testing

logger = logging.getLogger(__name__)

default_config = """
[aw-qt]
autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window"]
# Enable OS-level start-at-login on first launch (Research Edition builds
# flip this to true; the user can still disable it from the tray afterwards).
autostart_on_first_run = false

[aw-qt-testing]
autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window"]
autostart_on_first_run = false
""".strip()


@contextmanager
def _with_profile_env(profile: str) -> Iterator[None]:
    """Resolve dirs for ``profile`` even if the caller has not exported it.

    aw-core keys isolation off ``AW_PROFILE``. Lookup functions take a
    profile argument, so they must set the env for the duration of the
    path computation — otherwise a test or a probe for a sibling profile
    would read the process's current root.
    """
    old = os.environ.get(ENV_VAR)
    if profile == DEFAULT_PROFILE:
        os.environ.pop(ENV_VAR, None)
    else:
        os.environ[ENV_VAR] = profile
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = old


def _using_legacy_testing_root() -> bool:
    """True when testing data still lives on the shared ``activitywatch/`` root.

    Isolated roots (including new-style ``activitywatch-testing/``) use bare
    filenames; ``config-testing.toml`` / ``[server-testing]`` stay legacy-only.
    """
    try:
        from aw_core.dirs import using_legacy_testing_root

        return using_legacy_testing_root()
    except ImportError:
        # aw-core < 0.5.18: no testing-root helper. Testing still shares the
        # default root and uses suffixed names.
        return os.environ.get(ENV_VAR) == TESTING_PROFILE


def _is_named_profile(profile: str) -> bool:
    """True for sibling profiles that isolate under ``activitywatch-<name>/``."""
    return profile not in (DEFAULT_PROFILE, TESTING_PROFILE)


def _shared_root_config_dir(module: str) -> str:
    """Config dir under the bare ``activitywatch/`` root, ignoring AW_PROFILE."""
    with _with_profile_env(DEFAULT_PROFILE):
        return dirs.get_config_dir(module)


def _read_toml_port(path: str) -> Optional[int]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            config = tomlkit.parse(f.read())
        if "port" in config:
            return int(str(config["port"]))
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
    return None


def _read_section_port(path: str, section: str) -> Optional[int]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            config = tomlkit.parse(f.read())
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None
    section_data = config.get(section, {})
    if "port" in section_data:
        return int(str(section_data["port"]))
    return None


def _raw_toml_section(path: str, section: str) -> Optional[Any]:
    """Return ``section`` only if the file has uncommented keys in it."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            parsed = tomlkit.parse(f.read())
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None
    data = parsed.get(section)
    if data is None:
        return None
    try:
        if len(data) == 0:
            return None
    except TypeError:
        return None
    return data


def _read_server_rust_port(profile: str) -> Optional[int]:
    """Read port from aw-server-rust config, returns None if not found/set.

    Isolated profile roots (and a fresh ``activitywatch-testing/``) use bare
    ``config.toml`` — matching aw-server-rust#652. ``config-testing.toml`` is
    only read in the legacy shared-root testing layout. A pre-isolation
    ``config-<profile>.toml`` is a fallback for named profiles, looked up in
    the shared ``activitywatch/`` root so an old file is not silently ignored.
    """
    with _with_profile_env(profile):
        config_dir = dirs.get_config_dir("aw-server-rust")
        if _using_legacy_testing_root():
            return _read_toml_port(
                os.path.join(config_dir, f"config-{TESTING_PROFILE}.toml")
            )
        port = _read_toml_port(os.path.join(config_dir, "config.toml"))
        if port is not None:
            return port
    if _is_named_profile(profile):
        return _read_toml_port(
            os.path.join(
                _shared_root_config_dir("aw-server-rust"), f"config-{profile}.toml"
            )
        )
    return None


def _read_aw_server_port(profile: str) -> Optional[int]:
    """Read port from aw-server (Python) config, returns None if not found/set.

    Isolated roots use the ``[server]`` section (the directory already
    isolates). ``[server-testing]`` is legacy-only, matching the same
    3-rule as aw-core#152 / aw-server-rust#652. ``[server-<profile>]``
    remains a fallback for pre-isolation named-profile files in the
    shared ``activitywatch/`` root.
    """
    with _with_profile_env(profile):
        config_path = os.path.join(dirs.get_config_dir("aw-server"), "aw-server.toml")
        if _using_legacy_testing_root():
            return _read_section_port(config_path, f"server-{TESTING_PROFILE}")
        port = _read_section_port(config_path, "server")
        if port is not None:
            return port
    if _is_named_profile(profile):
        shared_path = os.path.join(
            _shared_root_config_dir("aw-server"), "aw-server.toml"
        )
        return _read_section_port(shared_path, f"server-{profile}")
    return None


def _read_server_port(
    profile: str, autostart_modules: Optional[List[str]] = None
) -> int:
    """Read port from server config (aw-server-rust or aw-server), falling back to defaults.

    Only `default` and `testing` have a built-in port; any other profile must
    set `port` in its own config, since two instances cannot share 5600.

    When `autostart_modules` is provided, port lookup is restricted to the
    server type(s) actually configured to run, so the tray and the manager
    always target the same endpoint.
    """
    default_port = 5666 if is_testing(profile) else 5600

    # Determine which server types are in play.  When autostart_modules is
    # None (legacy / test call-site), fall back to the original Rust-first
    # behaviour so existing callers are unaffected.
    check_rust = autostart_modules is None or "aw-server-rust" in autostart_modules
    check_python = autostart_modules is None or "aw-server" in autostart_modules

    if check_rust:
        port = _read_server_rust_port(profile)
        if port is not None:
            return port

    if check_python:
        port = _read_aw_server_port(profile)
        if port is not None:
            return port

    if profile not in (DEFAULT_PROFILE, TESTING_PROFILE):
        logger.warning(
            "Profile %s has no port configured, falling back to %s "
            "(which collides with the default instance)",
            profile,
            default_port,
        )

    return default_port


class AwQtSettings:
    def __init__(self, profile: str = DEFAULT_PROFILE):
        """
        An instance of loaded settings, containing a list of modules to autostart.
        Constructor takes the profile name as an argument.
        """
        with _with_profile_env(profile):
            isolated_path = os.path.join(dirs.get_config_dir("aw-qt"), "aw-qt.toml")
            isolated_user_section = _raw_toml_section(isolated_path, "aw-qt")
            config = load_config_toml("aw-qt", default_config)
            # Isolated roots use [aw-qt]; [aw-qt-testing] is legacy-only.
            if _using_legacy_testing_root():
                section_name = f"aw-qt-{TESTING_PROFILE}"
                if section_name not in config:
                    section_name = "aw-qt"
                config_section: Any = config[section_name]
            else:
                config_section = config["aw-qt"]

        # Pre-isolation named profiles stored [aw-qt-<profile>] in the shared
        # activitywatch/ root. Honor that when the isolated file has no
        # uncommented [aw-qt] keys yet, otherwise the profile silently
        # inherits the default module list.
        if _is_named_profile(profile) and isolated_user_section is None:
            shared_section = _raw_toml_section(
                os.path.join(_shared_root_config_dir("aw-qt"), "aw-qt.toml"),
                f"aw-qt-{profile}",
            )
            if shared_section is not None:
                config_section = shared_section

        self.autostart_modules: List[str] = config_section["autostart_modules"]
        self.autostart_on_first_run: bool = bool(
            config_section.get("autostart_on_first_run", False)
        )
        # Pass autostart_modules so port lookup targets the actual server type,
        # keeping the tray URL and the manager's probe endpoint consistent.
        self.port: int = _read_server_port(profile, self.autostart_modules)


#: Never written by a tray toggle: stopping the server from the tray must not
#: leave ActivityWatch without one on the next start.
SERVER_MODULES = ("aw-server", "aw-server-rust")


def _autostart_location(profile: str) -> Tuple[str, str]:
    """The file and section ``AwQtSettings`` reads ``autostart_modules`` from.

    Mirrors the lookup in ``AwQtSettings.__init__``: ``[aw-qt]`` in the
    profile's ``aw-qt.toml``, ``[aw-qt-testing]`` in the legacy shared-root
    testing layout, and the pre-isolation ``[aw-qt-<profile>]`` section in the
    shared root for a named profile that still uses it.
    """
    with _with_profile_env(profile):
        path = os.path.join(dirs.get_config_dir("aw-qt"), "aw-qt.toml")
        legacy_testing = _using_legacy_testing_root()
    if legacy_testing:
        return path, f"aw-qt-{TESTING_PROFILE}"
    if _is_named_profile(profile) and _raw_toml_section(path, "aw-qt") is None:
        shared_path = os.path.join(_shared_root_config_dir("aw-qt"), "aw-qt.toml")
        shared_section = f"aw-qt-{profile}"
        if _raw_toml_section(shared_path, shared_section) is not None:
            return shared_path, shared_section
    return path, "aw-qt"


def _default_autostart_modules(section: str) -> List[str]:
    defaults = tomlkit.parse(default_config)
    table: Any = defaults.get(section, defaults["aw-qt"])
    return [str(m) for m in table["autostart_modules"]]


def set_module_autostart(
    source: str, section: str, name: str, enabled: bool
) -> Optional[str]:
    """Return ``source`` with ``name`` added to or removed from ``autostart_modules``.

    Returns ``None`` when the list already is in the requested state. Raises
    ``ValueError`` when ``source`` doesn't parse or has an unexpected shape, so
    the caller never writes a file it could not read.

    The document is edited in place: comments, formatting and other keys are
    kept. Enabling appends; disabling removes every occurrence; the order of
    the remaining entries never changes. If the section doesn't set the list
    (e.g. the commented-out file aw-qt writes on first run), the effective
    default list is written out with the change applied.
    """
    try:
        doc = tomlkit.parse(source)
    except Exception as e:
        raise ValueError(f"config file does not parse: {e}") from e

    if section not in doc:
        doc.add(section, tomlkit.table())
    table: Any = doc[section]
    if not isinstance(table, dict):
        raise ValueError(f"[{section}] is not a table")

    current = table.get("autostart_modules")
    if current is None:
        modules = _default_autostart_modules(section)
        if enabled == (name in modules):
            return None
        if enabled:
            modules.append(name)
        else:
            modules = [m for m in modules if m != name]
        table["autostart_modules"] = modules
    else:
        if not isinstance(current, list) or not all(
            isinstance(m, str) for m in current
        ):
            raise ValueError(f"[{section}] autostart_modules is not a list of strings")
        if enabled == (name in current):
            return None
        if enabled:
            current.append(name)
        else:
            for i in reversed(range(len(current))):
                if current[i] == name:
                    del current[i]

    updated = doc.as_string()
    # Belt and braces: never write something we couldn't read back.
    reparsed: Any = tomlkit.parse(updated)
    if (name in reparsed[section]["autostart_modules"]) != enabled:
        raise ValueError("updated config did not round-trip")
    return updated


def persist_module_autostart(
    name: str, enabled: bool, profile: str = DEFAULT_PROFILE
) -> bool:
    """Persist a tray start/stop of ``name`` to ``autostart_modules``.

    Re-reads the config from disk right before writing, so edits made while
    aw-qt runs are kept. Server modules are never written, and a file that
    fails to parse is left untouched. Errors are logged, not raised: the module
    has already been started or stopped either way. Returns whether the file
    was changed.
    """
    if name in SERVER_MODULES:
        logger.info(
            f"Not saving tray toggle of {name}: server modules are never persisted"
        )
        return False

    path, section = _autostart_location(profile)
    try:
        with open(path) as f:
            source = f.read()
    except FileNotFoundError:
        source = ""
    except OSError as e:
        logger.warning(
            f"Could not read {path}, not saving autostart change for {name}: {e}"
        )
        return False

    try:
        updated = set_module_autostart(source, section, name, enabled)
    except ValueError as e:
        logger.warning(f"Not saving autostart change for {name} to {path}: {e}")
        return False
    if updated is None:
        return False

    try:
        _atomic_write(path, updated)
    except OSError as e:
        logger.warning(
            f"Could not write {path}, autostart change for {name} not saved: {e}"
        )
        return False
    logger.info(
        f"{'Added' if enabled else 'Removed'} {name} "
        f"{'to' if enabled else 'from'} [{section}] autostart_modules in {path}"
    )
    return True


def _atomic_write(path: str, content: str) -> None:
    """Write via a temp file and rename, so a crash can't leave a truncated config.

    Follows a symlinked config (e.g. one managed by a dotfiles repo) and
    replaces its target, not the link.
    """
    target = os.path.realpath(path)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(target), prefix=".aw-qt.", suffix=".toml.tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(target):
            os.chmod(tmp, os.stat(target).st_mode & 0o777)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
