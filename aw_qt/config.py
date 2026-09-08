import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional

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
