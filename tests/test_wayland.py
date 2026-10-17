"""Tests for Wayland session detection and module substitution."""

import os
from unittest.mock import patch

from aw_qt.manager import (
    WAYLAND_WATCHER,
    _apply_wayland_substitutions,
    _is_wayland,
)


def _clean_env(**overrides):
    """Return a copy of os.environ with Wayland vars removed, then apply overrides."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE")
    }
    env.update(overrides)
    return env


class TestIsWayland:
    def test_wayland_display_set(self):
        with patch.dict(
            os.environ, _clean_env(WAYLAND_DISPLAY="wayland-0"), clear=True
        ):
            assert _is_wayland() is True

    def test_xdg_session_type_wayland(self):
        with patch.dict(os.environ, _clean_env(XDG_SESSION_TYPE="wayland"), clear=True):
            assert _is_wayland() is True

    def test_xdg_session_type_wayland_uppercase(self):
        # Some compositors set mixed-case values
        with patch.dict(os.environ, _clean_env(XDG_SESSION_TYPE="Wayland"), clear=True):
            assert _is_wayland() is True

    def test_x11_session(self):
        with patch.dict(os.environ, _clean_env(XDG_SESSION_TYPE="x11"), clear=True):
            assert _is_wayland() is False

    def test_no_session_vars(self):
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _is_wayland() is False


class TestApplyWaylandSubstitutions:
    _DEFAULT_MODULES = ["aw-server", "aw-watcher-afk", "aw-watcher-window"]
    _AVAILABLE = {*_DEFAULT_MODULES, WAYLAND_WATCHER}

    def test_no_substitution_on_x11(self):
        with patch("aw_qt.manager._is_wayland", return_value=False):
            result = _apply_wayland_substitutions(self._DEFAULT_MODULES, self._AVAILABLE)
        assert result == self._DEFAULT_MODULES

    def test_substitution_on_wayland(self):
        with patch("aw_qt.manager._is_wayland", return_value=True):
            result = _apply_wayland_substitutions(self._DEFAULT_MODULES, self._AVAILABLE)
        assert WAYLAND_WATCHER in result
        assert "aw-watcher-window" not in result
        assert "aw-watcher-afk" not in result
        assert "aw-server" in result

    def test_wayland_watcher_not_installed_keeps_original(self):
        """When the Wayland watcher is absent, original list is returned unchanged."""
        available = set(self._DEFAULT_MODULES)  # no WAYLAND_WATCHER
        with patch("aw_qt.manager._is_wayland", return_value=True):
            result = _apply_wayland_substitutions(self._DEFAULT_MODULES, available)
        assert result == self._DEFAULT_MODULES

    def test_no_duplicate_wayland_watcher(self):
        """aw-watcher-window-wayland appears exactly once even when both X11 modules are listed."""
        with patch("aw_qt.manager._is_wayland", return_value=True):
            result = _apply_wayland_substitutions(self._DEFAULT_MODULES, self._AVAILABLE)
        assert result.count(WAYLAND_WATCHER) == 1

    def test_wayland_watcher_inserted_at_first_replaced_position(self):
        """Wayland watcher replaces aw-watcher-afk (first X11 module in the list)."""
        modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window"]
        with patch("aw_qt.manager._is_wayland", return_value=True):
            result = _apply_wayland_substitutions(modules, self._AVAILABLE)
        assert result.index(WAYLAND_WATCHER) == 1  # replaces aw-watcher-afk's position

    def test_server_only_list_gets_no_watcher(self):
        """A server-only selection must not have a watcher appended."""
        modules = ["aw-server-rust"]
        available = {"aw-server-rust", WAYLAND_WATCHER}
        with patch("aw_qt.manager._is_wayland", return_value=True):
            result = _apply_wayland_substitutions(modules, available)
        assert result == modules

    def test_empty_list_stays_empty(self):
        """`--autostart-modules none` (empty list) must start nothing on Wayland."""
        with patch("aw_qt.manager._is_wayland", return_value=True):
            result = _apply_wayland_substitutions([], self._AVAILABLE)
        assert result == []

    def test_custom_list_without_x11_watchers_unchanged(self):
        """A custom list with no X11 watchers is not augmented."""
        modules = ["aw-server", "aw-watcher-input"]
        available = {*modules, WAYLAND_WATCHER}
        with patch("aw_qt.manager._is_wayland", return_value=True):
            result = _apply_wayland_substitutions(modules, available)
        assert result == modules

    def test_wayland_watcher_already_in_list_not_duplicated(self):
        """Explicit inclusion of the Wayland watcher is not doubled."""
        modules = ["aw-server", WAYLAND_WATCHER]
        with patch("aw_qt.manager._is_wayland", return_value=True):
            result = _apply_wayland_substitutions(modules, self._AVAILABLE)
        assert result.count(WAYLAND_WATCHER) == 1
