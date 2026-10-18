"""Shared Wayland session detection and watcher identities."""

import os

# Both implementations report window and AFK activity. Prefer the standalone
# watcher for automatic selection; aw-awatcher ships in the Linux desktop bundle.
WAYLAND_WATCHER = "aw-watcher-window-wayland"
WAYLAND_WATCHERS = (WAYLAND_WATCHER, "aw-awatcher")
# These X11/xcb-based modules do not work on Wayland and are substituted out.
_WAYLAND_REPLACED_MODULES = ("aw-watcher-window", "aw-watcher-afk")


def _is_wayland() -> bool:
    """Return True when the current session is running under Wayland."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return False
