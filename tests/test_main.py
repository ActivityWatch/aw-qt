"""Startup must use the effective CLI server selection, not the saved list."""

import importlib
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.mark.parametrize(
    "saved,selected,saved_port,selected_port",
    [
        ("aw-server", "aw-server-rust", 5601, 5602),
        ("aw-server-rust", "aw-server", 5602, 5601),
    ],
)
@pytest.mark.parametrize("profile", ["default", "testing"])
def test_cli_server_override_uses_selected_port(
    saved, selected, saved_port, selected_port, profile, monkeypatch
):
    # Register restoration even when main() writes directly to os.environ.
    monkeypatch.setenv("AW_PROFILE", "default")
    main_module = importlib.import_module("aw_qt.main")
    settings = MagicMock(
        autostart_modules=[saved, "aw-sync"],
        autostart_on_first_run=False,
        port=saved_port,
    )
    with patch.object(
        main_module, "AwQtSettings", return_value=settings
    ), patch.object(main_module, "Manager") as manager, patch.object(
        main_module, "_acquire_single_instance_lock"
    ), patch.object(main_module, "setup_logging"), patch.object(
        main_module.subprocess, "call"
    ), patch.object(main_module.os, "setpgrp", create=True), patch(
        "aw_qt.config._read_server_rust_port", return_value=5602
    ), patch("aw_qt.config._read_aw_server_port", return_value=5601), patch(
        "aw_qt.trayicon.run", return_value=0
    ) as run:
        result = CliRunner().invoke(
            main_module.main,
            ["--profile", profile, "--autostart-modules", f"{selected},aw-sync"],
        )
    assert result.exit_code == 0, result.output
    manager.return_value.autostart.assert_called_once_with(
        [selected, "aw-sync"], port=selected_port
    )
    assert run.call_args.kwargs["port"] == selected_port
    manager.return_value.autostart_notify_if_enabled.assert_not_called()


@pytest.mark.parametrize("selected", ["none", "aw-sync"])
@pytest.mark.parametrize("profile", ["default", "testing"])
@pytest.mark.parametrize("saved", ["aw-server", "aw-server-rust"])
def test_cli_server_free_override_preserves_configured_port(
    selected, profile, saved, monkeypatch
):
    monkeypatch.setenv("AW_PROFILE", "default")
    main_module = importlib.import_module("aw_qt.main")
    settings = MagicMock(
        autostart_modules=[saved, "aw-sync"],
        autostart_on_first_run=False,
        port=5678,
    )
    with patch.object(
        main_module, "AwQtSettings", return_value=settings
    ), patch.object(main_module, "Manager") as manager, patch.object(
        main_module, "_acquire_single_instance_lock"
    ), patch.object(main_module, "setup_logging"), patch.object(
        main_module.subprocess, "call"
    ), patch.object(main_module.os, "setpgrp", create=True), patch(
        "aw_qt.trayicon.run", return_value=0
    ) as run:
        result = CliRunner().invoke(
            main_module.main,
            ["--profile", profile, "--autostart-modules", selected],
        )
    assert result.exit_code == 0, result.output
    manager.return_value.autostart.assert_called_once_with(
        [] if selected == "none" else ["aw-sync"], port=5678
    )
    assert run.call_args.kwargs["port"] == 5678
    manager.return_value.autostart_notify_if_enabled.assert_not_called()
