"""The tray hides its support item when the server says the user already supports us."""

from contextlib import ExitStack
from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aw_qt.trayicon import TrayIcon


def make_tray():
    """Build a TrayIcon stand-in carrying the attributes the support action uses."""
    tray = SimpleNamespace(
        manager=MagicMock(),
        _parent=None,
        profile="default",
        port=5600,
        root_url="http://localhost:5600",
        testing=True,
        _build_modulemenu=MagicMock(),
        _on_notify_toggled=MagicMock(),
        _autostart_action=None,
        _notify_action=None,
        _support_action=MagicMock(),
        setContextMenu=MagicMock(),
        _restart_timestamps={},
        MAX_AUTO_RESTARTS=TrayIcon.MAX_AUTO_RESTARTS,
        RESTART_WINDOW_SECONDS=TrayIcon.RESTART_WINDOW_SECONDS,
    )
    tray._refresh_notify_action = partial(TrayIcon._refresh_notify_action, tray)
    tray._refresh_support_action = partial(TrayIcon._refresh_support_action, tray)
    return tray


@pytest.mark.parametrize(
    "supporter,expected_visible",
    [
        (True, False),  # already supporting => item hidden
        (False, True),  # not a supporter => item shown
    ],
)
def test_support_item_visibility_follows_the_server_flag(supporter, expected_visible):
    tray = make_tray()
    with patch("aw_qt.trayicon.read_supporter", return_value=supporter):
        tray._refresh_support_action()
    tray._support_action.setVisible.assert_called_once_with(expected_visible)


def test_support_item_defaults_to_visible_without_the_action():
    """A tray built without the action (older menu) must not raise."""
    tray = make_tray()
    tray._support_action = None
    tray._refresh_support_action()  # no exception


def test_build_rootmenu_hides_the_item_and_rechecks_it_on_open():
    """The item is read once at build time and again every time the menu opens."""
    tray = make_tray()
    tray.manager.modules = []
    menu = MagicMock()
    with ExitStack() as stack:
        stack.enter_context(patch("aw_qt.trayicon.QMenu", return_value=menu))
        stack.enter_context(patch("aw_qt.trayicon.QIcon"))
        stack.enter_context(
            patch("aw_qt.trayicon.autostart.is_supported", return_value=False)
        )
        stack.enter_context(
            patch("aw_qt.trayicon.read_notify_enabled", return_value=False)
        )
        supporter = stack.enter_context(
            patch("aw_qt.trayicon.read_supporter", return_value=True)
        )
        TrayIcon._build_rootmenu(tray)

        assert tray._support_action is not None
        supporter.assert_called_once()
        tray._support_action.setVisible.assert_called_once_with(False)

        # Re-read on every open, since the flag is changed from the web UI
        tray._support_action.setVisible.reset_mock()
        refresh = [
            call.args[0]
            for call in menu.aboutToShow.connect.call_args_list
            if call.args[0] == tray._refresh_support_action
        ]
        assert refresh == [tray._refresh_support_action]
        refresh[0]()
        tray._support_action.setVisible.assert_called_once_with(False)
