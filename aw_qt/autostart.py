"""
OS-level autostart ("start at login") support for aw-qt.

This module manages *login items*: getting the operating system to launch aw-qt
when the user logs in.  It is unrelated to the ``autostart_modules`` setting in
:mod:`aw_qt.config`, which decides which ActivityWatch modules aw-qt starts once
it is already running.

Public API:

- :func:`is_enabled` -- whether aw-qt is currently registered to start at login
- :func:`enable` -- register aw-qt as a login item
- :func:`disable` -- unregister aw-qt as a login item

(plus :func:`is_supported`, so callers can hide the option on platforms where
no backend exists)

All operations are idempotent: enabling something already enabled (or disabling
something already disabled) is a no-op and never raises.  Failures that are not
recoverable (read-only home directory, missing registry permissions, ...) raise
:class:`AutostartError` from :func:`enable`/:func:`disable`, so the caller can
show a message instead of crashing.  :func:`is_enabled` never raises; it logs
and reports ``False`` if the state cannot be determined.

Backends (stdlib only, no extra dependencies):

- Linux: ``$XDG_CONFIG_HOME/autostart/aw-qt.desktop`` (defaults to
  ``~/.config/autostart``), same location as ``scripts/config-autostart.sh``.
- macOS: a LaunchAgent plist at
  ``~/Library/LaunchAgents/net.activitywatch.aw-qt.plist`` with ``RunAtLoad``.
- Windows: the ``HKCU\\...\\CurrentVersion\\Run`` registry value, *plus*
  detection of the Startup-folder shortcut created by the installer.
"""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)

APP_NAME = "ActivityWatch"

# Linux
DESKTOP_FILENAME = "aw-qt.desktop"
# Characters that force an Exec argument to be quoted (Desktop Entry Spec)
_DESKTOP_RESERVED_CHARS = " \t\n\"'\\><~|&;$*?#()`"

# macOS
LAUNCH_AGENT_LABEL = "net.activitywatch.aw-qt"
LAUNCH_AGENT_FILENAME = f"{LAUNCH_AGENT_LABEL}.plist"

# Windows
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE_NAME = APP_NAME
# Created by the Inno Setup installer when the user ticks
# "Start ActivityWatch when Windows starts" ({userstartup} in activitywatch-setup.iss)
WINDOWS_STARTUP_SHORTCUT_NAME = f"{APP_NAME}.lnk"

# Fallback used when the packaged resources/aw-qt.desktop cannot be found
_DESKTOP_TEMPLATE = """[Desktop Entry]
Name=ActivityWatch
GenericName=Time-tracking application
Comment=Open source time-tracking application with a focus on extensibility and privacy.
Exec={exec}
Hidden=false
StartupNotify=true
Terminal=false
Type=Application
X-GNOME-Autostart-enabled=true
Version=1.0
Icon=activitywatch
Categories=Utility;
"""


class AutostartError(Exception):
    """Raised when autostart could not be enabled or disabled."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _home() -> Path:
    """Return the user's home directory (indirection makes tests easy to isolate)."""
    return Path(os.path.expanduser("~"))


def _command() -> List[str]:
    """The command the OS should run at login."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: sys.executable is the aw-qt binary itself
        return [sys.executable]

    import shutil

    exe = shutil.which("aw-qt")
    if exe:
        return [exe]

    # Running from a source checkout
    return [sys.executable, "-m", "aw_qt"]


def _write_text_atomic(path: Path, contents: str) -> None:
    """Write `contents` to `path`, replacing any existing file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmppath = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contents)
        os.replace(tmppath, path)
    except BaseException:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise


def _remove_file(path: Path) -> None:
    """Remove `path` if it exists. Missing files are not an error (idempotency)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Linux: XDG autostart .desktop file
# ---------------------------------------------------------------------------


def _linux_autostart_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "autostart"
    return _home() / ".config" / "autostart"


def _linux_desktop_path() -> Path:
    return _linux_autostart_dir() / DESKTOP_FILENAME


def _bundled_desktop_file() -> Optional[Path]:
    """Locate the shipped resources/aw-qt.desktop, if available."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        # aw-qt.spec bundles it at the top level of the PyInstaller dir
        candidates.append(Path(meipass) / DESKTOP_FILENAME)
    candidates.append(
        Path(__file__).resolve().parent.parent / "resources" / DESKTOP_FILENAME
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _desktop_quote_arg(arg: str) -> str:
    """Quote a single Exec argument per the Desktop Entry Specification.

    Desktop entries do *not* use POSIX shell quoting (so `shlex.quote` and its
    single quotes are wrong here): arguments containing reserved characters
    must be enclosed in double quotes, within which `"`, `` ` ``, `$` and `\\`
    are escaped with a backslash.  Those backslashes are then doubled, because
    the value is additionally subject to the desktop entry string escape rules.
    A literal percent sign is written as `%%`, since `%` introduces a field code.

    See: https://specifications.freedesktop.org/desktop-entry-spec/latest/exec-variables.html
    """
    arg = arg.replace("%", "%%")
    if not arg:
        return '""'
    if not any(c in _DESKTOP_RESERVED_CHARS for c in arg):
        return arg
    quoted = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    # String-value escaping: a backslash in a desktop entry value is written as `\\`
    quoted = quoted.replace("\\", "\\\\")
    return f'"{quoted}"'


def _desktop_exec_value() -> str:
    return " ".join(_desktop_quote_arg(arg) for arg in _command())


def _desktop_entry_contents() -> str:
    """Build the .desktop entry, reusing the packaged one when we can find it."""
    exec_value = _desktop_exec_value()
    source = _bundled_desktop_file()
    if source is not None:
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"Could not read {source}, using built-in template: {e}")
        else:
            return _with_exec(text, exec_value)
    return _DESKTOP_TEMPLATE.format(**{"exec": exec_value})


def _with_exec(desktop_entry: str, exec_value: str) -> str:
    """Return `desktop_entry` with Exec= pointing at our command and Hidden=false."""
    lines = []
    has_exec = False
    for line in desktop_entry.splitlines():
        key = line.split("=", 1)[0].strip().lower()
        if key == "exec":
            lines.append(f"Exec={exec_value}")
            has_exec = True
        elif key == "hidden":
            # Desktop environments write Hidden=true to disable an entry
            lines.append("Hidden=false")
        else:
            lines.append(line)
    if not has_exec:
        lines.append(f"Exec={exec_value}")
    return "\n".join(lines) + "\n"


def _linux_is_enabled() -> bool:
    path = _linux_desktop_path()
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning(f"Could not read {path}: {e}")
        # The file exists, so autostart is (most likely) enabled
        return True
    for line in text.splitlines():
        parts = line.split("=", 1)
        if len(parts) != 2:
            continue
        key, value = parts[0].strip().lower(), parts[1].strip().lower()
        # Both are used by desktop environments to disable an autostart entry
        if key == "hidden" and value == "true":
            return False
        if key == "x-gnome-autostart-enabled" and value == "false":
            return False
    return True


def _linux_enable() -> None:
    _write_text_atomic(_linux_desktop_path(), _desktop_entry_contents())


def _linux_disable() -> None:
    _remove_file(_linux_desktop_path())


# ---------------------------------------------------------------------------
# macOS: LaunchAgent plist
#
# NOTE: we deliberately do *not* call `launchctl load/unload`.
#   - `launchctl load -w` would immediately start a second aw-qt (because of
#     RunAtLoad), which the single-instance lock then kills off again.
#   - `launchctl unload` would terminate the running instance that launchd
#     started at login -- i.e. quitting the app the user just clicked in.
# launchd scans ~/Library/LaunchAgents at every login, so writing/removing the
# plist is enough for the setting to take effect from the next login onwards,
# which is exactly the semantics of a login item.
# ---------------------------------------------------------------------------


def _macos_plist_path() -> Path:
    return _home() / "Library" / "LaunchAgents" / LAUNCH_AGENT_FILENAME


def _macos_plist_contents() -> bytes:
    import plistlib

    return plistlib.dumps(
        {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": _command(),
            "RunAtLoad": True,
            # aw-qt manages its own subprocesses and should not be respawned by
            # launchd when the user quits it from the tray
            "KeepAlive": False,
            "ProcessType": "Interactive",
        }
    )


def _macos_is_enabled() -> bool:
    return _macos_plist_path().is_file()


def _macos_enable() -> None:
    path = _macos_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = _macos_plist_contents()
    fd, tmppath = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contents)
        os.replace(tmppath, path)
    except BaseException:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise


def _macos_disable() -> None:
    _remove_file(_macos_plist_path())


# ---------------------------------------------------------------------------
# Windows: HKCU Run key + installer Startup shortcut
#
# Precedence: the installer's Startup-folder shortcut counts as "enabled".
# If it is present we do *not* also write the Run key (that would start aw-qt
# twice), and disabling removes both, otherwise the toggle would appear to do
# nothing for everyone who ticked the installer checkbox.
# ---------------------------------------------------------------------------


def _windows_startup_dir() -> Optional[Path]:
    """The user's Startup folder, or None if it cannot be determined."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                value, _type = winreg.QueryValueEx(key, "Startup")
            if value:
                return Path(str(value))
        except OSError as e:
            logger.debug(f"Could not read Startup folder from registry: {e}")

        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(
                appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
            )
    return None


def _windows_startup_shortcut() -> Optional[Path]:
    """Path to the installer-created Startup shortcut, if it exists."""
    startup_dir = _windows_startup_dir()
    if startup_dir is None:
        return None
    shortcut = startup_dir / WINDOWS_STARTUP_SHORTCUT_NAME
    try:
        if shortcut.is_file():
            return shortcut
    except OSError as e:
        logger.debug(f"Could not stat {shortcut}: {e}")
    return None


def _windows_run_value() -> Optional[str]:
    """The HKCU Run value for ActivityWatch, or None if it is not set."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                value, _type = winreg.QueryValueEx(key, WINDOWS_RUN_VALUE_NAME)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise AutostartError(f"Could not read the registry: {e}") from e
        return str(value)
    return None


def _windows_set_run_value(command: str) -> None:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(
                    key, WINDOWS_RUN_VALUE_NAME, 0, winreg.REG_SZ, command
                )
        except OSError as e:
            raise AutostartError(f"Could not write to the registry: {e}") from e


def _windows_delete_run_value() -> None:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, WINDOWS_RUN_VALUE_NAME)
        except FileNotFoundError:
            # Key or value is already gone
            pass
        except OSError as e:
            raise AutostartError(f"Could not write to the registry: {e}") from e


def _windows_delete_startup_shortcut() -> None:
    shortcut = _windows_startup_shortcut()
    if shortcut is None:
        return
    try:
        shortcut.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        raise AutostartError(f"Could not remove {shortcut}: {e}") from e


def _windows_is_enabled() -> bool:
    if _windows_startup_shortcut() is not None:
        return True
    return _windows_run_value() is not None


def _windows_enable() -> None:
    if _windows_startup_shortcut() is not None:
        # Already started by the installer's Startup shortcut, adding the Run
        # key as well would launch aw-qt twice
        logger.info("Autostart already enabled via the Startup folder shortcut")
        return
    _windows_set_run_value(subprocess.list2cmdline(_command()))


def _windows_disable() -> None:
    _windows_delete_run_value()
    _windows_delete_startup_shortcut()


# ---------------------------------------------------------------------------
# platform dispatch
# ---------------------------------------------------------------------------


class _Backend(NamedTuple):
    is_enabled: Callable[[], bool]
    enable: Callable[[], None]
    disable: Callable[[], None]


_BACKENDS: Dict[str, _Backend] = {
    "linux": _Backend(_linux_is_enabled, _linux_enable, _linux_disable),
    "darwin": _Backend(_macos_is_enabled, _macos_enable, _macos_disable),
    "windows": _Backend(_windows_is_enabled, _windows_enable, _windows_disable),
}


def _platform_key(platform_name: Optional[str] = None) -> Optional[str]:
    """Map a sys.platform string onto a backend name, or None if unsupported."""
    name = sys.platform if platform_name is None else platform_name
    if name.startswith("linux") or "bsd" in name:
        # The *BSDs use the same XDG autostart directory as Linux
        return "linux"
    if name == "darwin":
        return "darwin"
    if name == "win32":
        return "windows"
    return None


def _get_backend(platform_name: Optional[str] = None) -> Optional[_Backend]:
    key = _platform_key(platform_name)
    return _BACKENDS[key] if key is not None else None


def is_supported(platform_name: Optional[str] = None) -> bool:
    """Whether autostart can be managed on this platform."""
    return _get_backend(platform_name) is not None


def is_enabled() -> bool:
    """Whether aw-qt is currently registered to start at login.

    Never raises: an unsupported platform or an unreadable
    file/registry key is reported as `False`.
    """
    backend = _get_backend()
    if backend is None:
        return False
    try:
        return backend.is_enabled()
    except Exception as e:
        logger.warning(f"Could not determine autostart status: {e}")
        return False


def enable() -> None:
    """Register aw-qt to start at login. No-op if already enabled.

    Raises AutostartError if the change could not be made.
    """
    backend = _get_backend()
    if backend is None:
        raise AutostartError(f"Autostart is not supported on {sys.platform}")
    try:
        backend.enable()
    except AutostartError:
        raise
    except Exception as e:
        raise AutostartError(f"Could not enable autostart: {e}") from e
    logger.info("Enabled autostart")


def disable() -> None:
    """Unregister aw-qt from starting at login. No-op if already disabled.

    Raises AutostartError if the change could not be made.
    """
    backend = _get_backend()
    if backend is None:
        raise AutostartError(f"Autostart is not supported on {sys.platform}")
    try:
        backend.disable()
    except AutostartError:
        raise
    except Exception as e:
        raise AutostartError(f"Could not disable autostart: {e}") from e
    logger.info("Disabled autostart")


FIRST_RUN_MARKER = "autostart-first-run"


def ensure_enabled_on_first_run() -> None:
    """Enable start-at-login once, on the first launch that sees the setting.

    Used by builds where autostart should be on by default (e.g. the Research
    Edition, where participants' machines must survive reboots without setup
    steps). A marker file in the aw-qt data dir records that enabling
    succeeded, so a user who later unchecks "Start at login" is never
    overridden. On failure no marker is written, and the next launch retries.
    """
    if not is_supported():
        logger.debug("Autostart not supported on this platform; skipping first-run enable")
        return
    from aw_core import dirs  # deferred: keep this module importable without aw_core

    marker = Path(dirs.get_data_dir("aw-qt")) / FIRST_RUN_MARKER
    if marker.exists():
        return
    try:
        enable()
    except AutostartError as e:
        logger.warning(
            f"Could not enable autostart on first run (will retry next launch): {e}"
        )
        return
    _write_text_atomic(marker, "start-at-login was enabled on first run\n")
    logger.info("Enabled start-at-login on first run")
