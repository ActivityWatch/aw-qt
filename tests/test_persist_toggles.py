"""Tray start/stop is saved to ``autostart_modules`` (the read-modify-write)."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import tomlkit
from click.testing import CliRunner

from aw_core import dirs
from aw_core.config import _comment_out_toml
from aw_qt.config import (
    AwQtSettings,
    default_config,
    persist_module_autostart,
    set_module_autostart,
)
from aw_qt.manager import Module
from aw_qt.profile import DEFAULT_PROFILE, export_profile


@pytest.fixture(autouse=True)
def _default_profile(monkeypatch):
    monkeypatch.delenv("AW_PROFILE", raising=False)


def _config_path(profile: str) -> Path:
    export_profile(profile)
    try:
        return Path(dirs.get_config_dir("aw-qt")) / "aw-qt.toml"
    finally:
        export_profile(DEFAULT_PROFILE)


USER_CONFIG = """\
# My ActivityWatch setup
[aw-qt]
# watchers, in start order
autostart_modules = [
    "aw-server",  # the server
    "aw-watcher-afk",
    "aw-watcher-window",
]
autostart_on_first_run = false  # keep it off

[aw-qt-testing]
autostart_modules = ["aw-server"]
"""


class TestSetModuleAutostart:
    def test_enable_appends_and_keeps_comments(self):
        out = set_module_autostart(USER_CONFIG, "aw-qt", "aw-watcher-input", True)
        assert out == USER_CONFIG.replace(
            '    "aw-watcher-window",\n',
            '    "aw-watcher-window",\n    "aw-watcher-input",\n',
        )

    def test_disable_removes_and_keeps_order(self):
        out = set_module_autostart(USER_CONFIG, "aw-qt", "aw-watcher-afk", False)
        assert out == USER_CONFIG.replace('    "aw-watcher-afk",\n', "")
        assert "# My ActivityWatch setup" in out
        assert "# keep it off" in out

    def test_already_in_state_is_a_noop(self):
        assert (
            set_module_autostart(USER_CONFIG, "aw-qt", "aw-watcher-afk", True) is None
        )
        assert set_module_autostart(USER_CONFIG, "aw-qt", "aw-notify", False) is None

    def test_no_duplicates_and_all_copies_removed(self):
        source = '[aw-qt]\nautostart_modules = ["a", "b", "a", "c"]\n'
        assert set_module_autostart(source, "aw-qt", "a", True) is None
        out = set_module_autostart(source, "aw-qt", "a", False)
        assert out == '[aw-qt]\nautostart_modules = ["b", "c"]\n'
        out = set_module_autostart(out, "aw-qt", "a", True)
        assert out == '[aw-qt]\nautostart_modules = ["b", "c", "a"]\n'

    def test_only_the_given_section_changes(self):
        out = set_module_autostart(USER_CONFIG, "aw-qt-testing", "aw-watcher-afk", True)
        doc = tomlkit.parse(out)
        assert doc["aw-qt-testing"]["autostart_modules"] == [
            "aw-server",
            "aw-watcher-afk",
        ]
        assert (
            doc["aw-qt"]["autostart_modules"]
            == tomlkit.parse(USER_CONFIG)["aw-qt"]["autostart_modules"]
        )

    def test_commented_out_first_run_file_gets_effective_defaults(self):
        source = _comment_out_toml(default_config)
        out = set_module_autostart(source, "aw-qt", "aw-watcher-afk", False)
        assert out is not None
        # The commented-out defaults are still there for reference.
        for line in source.splitlines():
            assert line in out
        doc = tomlkit.parse(out)
        assert doc["aw-qt"]["autostart_modules"] == ["aw-server", "aw-watcher-window"]
        assert "autostart_modules" not in doc["aw-qt-testing"]

    def test_missing_section_is_created(self):
        out = set_module_autostart("# empty\n", "aw-qt", "aw-watcher-input", True)
        assert out.startswith("# empty\n")
        assert tomlkit.parse(out)["aw-qt"]["autostart_modules"] == [
            "aw-server",
            "aw-watcher-afk",
            "aw-watcher-window",
            "aw-watcher-input",
        ]

    @pytest.mark.parametrize(
        "source",
        [
            '[aw-qt]\nautostart_modules = ["aw-server",\n',
            "[aw-qt]\nautostart_modules = 5\n",
            'aw-qt = "not a table"\n',
        ],
    )
    def test_malformed_raises(self, source):
        with pytest.raises(ValueError):
            set_module_autostart(source, "aw-qt", "aw-watcher-afk", False)


class TestPersistModuleAutostart:
    def test_comments_preserved_on_disk(self):
        path = _config_path(DEFAULT_PROFILE)
        path.write_text(USER_CONFIG)
        assert persist_module_autostart("aw-watcher-afk", False)
        text = path.read_text()
        assert text == USER_CONFIG.replace('    "aw-watcher-afk",\n', "")
        assert AwQtSettings().autostart_modules == ["aw-server", "aw-watcher-window"]

    def test_rereads_file_before_each_write(self):
        path = _config_path(DEFAULT_PROFILE)
        path.write_text('[aw-qt]\nautostart_modules = ["aw-server"]\n')
        assert persist_module_autostart("aw-watcher-afk", True)
        # Edited by hand while aw-qt runs.
        path.write_text(
            '# hand edit\n[aw-qt]\nautostart_modules = ["aw-server", "x"]\n'
        )
        assert persist_module_autostart("aw-watcher-window", True)
        assert path.read_text() == (
            '# hand edit\n[aw-qt]\nautostart_modules = ["aw-server", "x", "aw-watcher-window"]\n'
        )

    def test_first_run_file_round_trips_through_settings(self):
        # AwQtSettings writes the commented-out defaults on first load.
        AwQtSettings()
        assert persist_module_autostart("aw-watcher-afk", False)
        assert AwQtSettings().autostart_modules == ["aw-server", "aw-watcher-window"]
        assert persist_module_autostart("aw-watcher-afk", True)
        assert AwQtSettings().autostart_modules == [
            "aw-server",
            "aw-watcher-window",
            "aw-watcher-afk",
        ]

    @pytest.mark.parametrize("name", ["aw-server", "aw-server-rust"])
    @pytest.mark.parametrize("enabled", [True, False])
    def test_server_modules_are_never_written(self, name, enabled):
        path = _config_path(DEFAULT_PROFILE)
        path.write_text(USER_CONFIG)
        before = path.stat().st_mtime_ns
        assert not persist_module_autostart(name, enabled)
        assert path.read_text() == USER_CONFIG
        assert path.stat().st_mtime_ns == before

    def test_malformed_file_left_untouched(self, caplog):
        path = _config_path(DEFAULT_PROFILE)
        broken = '# mid-edit\n[aw-qt]\nautostart_modules = ["aw-server",\n'
        path.write_text(broken)
        assert not persist_module_autostart("aw-watcher-afk", True)
        assert path.read_text() == broken
        assert "Not saving autostart change" in caplog.text
        # No temp file left behind either.
        assert os.listdir(path.parent) == ["aw-qt.toml"]

    def test_symlinked_config_keeps_the_link(self, tmp_path):
        path = _config_path(DEFAULT_PROFILE)
        real = tmp_path / "dotfiles-aw-qt.toml"
        real.write_text(USER_CONFIG)
        try:
            path.symlink_to(real)
        except (OSError, NotImplementedError) as e:
            # Windows without Developer Mode / symlink privilege.
            pytest.skip(f"cannot create symlinks here: {e}")
        assert persist_module_autostart("aw-watcher-afk", False)
        assert path.is_symlink()
        assert '"aw-watcher-afk"' not in real.read_text()

    def test_utf8_and_line_endings_kept(self):
        path = _config_path(DEFAULT_PROFILE)
        source = '# Björn’s setup\r\n[aw-qt]\r\nautostart_modules = ["aw-server"]\r\n'
        path.write_bytes(source.encode("utf-8"))
        assert persist_module_autostart("aw-watcher-afk", True)
        assert path.read_bytes().decode("utf-8") == source.replace(
            '"aw-server"]', '"aw-server", "aw-watcher-afk"]'
        )

    def test_undecodable_file_left_untouched(self, caplog):
        path = _config_path(DEFAULT_PROFILE)
        broken = b'[aw-qt]\nautostart_modules = ["aw-server"] # \xff\xfe\n'
        path.write_bytes(broken)
        assert not persist_module_autostart("aw-watcher-afk", True)
        assert path.read_bytes() == broken
        assert "not saving autostart change" in caplog.text

    def test_default_profile_does_not_touch_testing_section(self):
        path = _config_path(DEFAULT_PROFILE)
        path.write_text(USER_CONFIG)
        persist_module_autostart("aw-watcher-input", True)
        assert tomlkit.parse(path.read_text())["aw-qt-testing"][
            "autostart_modules"
        ] == ["aw-server"]


class TestProfileLocation:
    """Each profile writes the file and section ``AwQtSettings`` reads."""

    def _toggle_and_reload(self, profile: str):
        AwQtSettings(profile=profile)  # first-run file, as at startup
        assert persist_module_autostart("aw-watcher-afk", False, profile)
        return AwQtSettings(profile=profile).autostart_modules

    def test_isolated_testing_writes_aw_qt_section_in_testing_root(self):
        assert self._toggle_and_reload("testing") == ["aw-server", "aw-watcher-window"]
        path = _config_path("testing")
        assert "activitywatch-testing" in str(path)
        doc = tomlkit.parse(path.read_text())
        assert doc["aw-qt"]["autostart_modules"] == ["aw-server", "aw-watcher-window"]
        # The default profile is unaffected.
        assert AwQtSettings().autostart_modules == [
            "aw-server",
            "aw-watcher-afk",
            "aw-watcher-window",
        ]

    def test_legacy_testing_writes_aw_qt_testing_section(self, tmp_path):
        # A legacy testing artifact in the shared root pins the old layout.
        legacy = tmp_path / "data" / "activitywatch" / "aw-server"
        legacy.mkdir(parents=True)
        (legacy / "peewee-sqlite-testing.v2.db").write_text("")
        path = _config_path("testing")
        assert "activitywatch-testing" not in str(path)
        path.write_text(USER_CONFIG)

        assert persist_module_autostart("aw-watcher-window", True, "testing")
        doc = tomlkit.parse(path.read_text())
        assert doc["aw-qt-testing"]["autostart_modules"] == [
            "aw-server",
            "aw-watcher-window",
        ]
        assert "# My ActivityWatch setup" in path.read_text()
        assert AwQtSettings(profile="testing").autostart_modules == [
            "aw-server",
            "aw-watcher-window",
        ]
        # [aw-qt] (the default profile's list) is unchanged.
        assert AwQtSettings().autostart_modules == [
            "aw-server",
            "aw-watcher-afk",
            "aw-watcher-window",
        ]

    def test_named_profile_writes_its_isolated_file(self):
        assert self._toggle_and_reload("research") == ["aw-server", "aw-watcher-window"]
        assert "activitywatch-research" in str(_config_path("research"))
        assert AwQtSettings().autostart_modules == [
            "aw-server",
            "aw-watcher-afk",
            "aw-watcher-window",
        ]

    def test_named_profile_on_pre_isolation_shared_section(self):
        shared = _config_path(DEFAULT_PROFILE)
        shared.write_text(
            '[aw-qt]\nautostart_modules = ["aw-server"]\n\n'
            "[aw-qt-research]\n"
            "# research setup\n"
            'autostart_modules = ["aw-server-rust", "aw-watcher-afk"]\n'
        )
        assert persist_module_autostart("aw-watcher-window", True, "research")
        text = shared.read_text()
        assert "# research setup" in text
        assert AwQtSettings(profile="research").autostart_modules == [
            "aw-server-rust",
            "aw-watcher-afk",
            "aw-watcher-window",
        ]
        assert AwQtSettings().autostart_modules == ["aw-server"]


class TestTrayClick:
    def _tray(self, persist_toggles: bool, modules=()):
        from aw_qt.manager import Manager
        from aw_qt.trayicon import TrayIcon

        with patch.object(Manager, "discover_modules"):
            manager = Manager()
        manager.modules = list(modules)
        fake = SimpleNamespace(
            manager=manager,
            testing=False,
            profile=DEFAULT_PROFILE,
            persist_toggles=persist_toggles,
            _restart_timestamps={"aw-watcher-afk": [1.0]},
        )
        return fake, lambda module: TrayIcon._on_module_clicked(fake, module)

    def _module(self, type="bundled", **toggle):
        module = Module("aw-watcher-afk", Path(f"/{type}/aw-watcher-afk"), type)
        module.toggle = MagicMock(**toggle)
        return module

    def test_click_persists_new_state(self):
        module = self._module(return_value=False)
        fake, click = self._tray(True, [module])
        with patch("aw_qt.trayicon.persist_module_autostart") as persist:
            click(module)
        persist.assert_called_once_with("aw-watcher-afk", False, DEFAULT_PROFILE)
        assert "aw-watcher-afk" not in fake._restart_timestamps

    def test_cli_override_only_changes_running_state(self):
        module = self._module(return_value=True)
        _, click = self._tray(False, [module])
        with patch("aw_qt.trayicon.persist_module_autostart") as persist:
            click(module)
        module.toggle.assert_called_once_with(False)
        persist.assert_not_called()

    def test_failed_start_is_not_persisted(self):
        module = self._module(side_effect=OSError("exec failed"))
        _, click = self._tray(True, [module])
        with patch("aw_qt.trayicon.persist_module_autostart") as persist:
            with pytest.raises(OSError):
                click(module)
        persist.assert_not_called()

    def test_system_copy_shadowed_by_bundled_is_not_persisted(self):
        bundled = self._module("bundled", return_value=True)
        system = self._module("system", return_value=True)
        _, click = self._tray(True, [system, bundled])
        with patch("aw_qt.trayicon.persist_module_autostart") as persist:
            click(system)
            persist.assert_not_called()
            click(bundled)
        persist.assert_called_once_with("aw-watcher-afk", True, DEFAULT_PROFILE)

    def test_system_module_without_bundled_copy_is_persisted(self):
        system = self._module("system", return_value=True)
        _, click = self._tray(True, [system])
        with patch("aw_qt.trayicon.persist_module_autostart") as persist:
            click(system)
        persist.assert_called_once_with("aw-watcher-afk", True, DEFAULT_PROFILE)

    def test_persist_error_does_not_escape_the_slot(self, caplog):
        module = self._module(return_value=True)
        _, click = self._tray(True, [module])
        with patch(
            "aw_qt.trayicon.persist_module_autostart", side_effect=RuntimeError("boom")
        ):
            click(module)
        assert "Failed to save autostart change" in caplog.text


class TestMainWiring:
    @pytest.mark.parametrize(
        "args, persist",
        [
            ([], True),
            (["--autostart-modules", "aw-server,aw-watcher-afk"], False),
            (["--autostart-modules", "none"], False),
        ],
    )
    def test_autostart_modules_override_disables_persisting(self, args, persist):
        import importlib

        # `aw_qt.main` the attribute is the click command re-exported by aw_qt.
        main_module = importlib.import_module("aw_qt.main")

        settings = MagicMock(
            autostart_modules=["aw-server"], autostart_on_first_run=False, port=5600
        )
        with patch.object(
            main_module, "AwQtSettings", return_value=settings
        ), patch.object(main_module, "Manager"), patch.object(
            main_module, "_acquire_single_instance_lock"
        ), patch.object(
            main_module, "setup_logging"
        ), patch.object(
            main_module.subprocess, "call"
        ), patch.object(
            main_module.os, "setpgrp", create=True
        ), patch(
            "aw_qt.trayicon.run", return_value=0
        ) as run:
            result = CliRunner().invoke(main_module.main, args)
        assert result.exit_code == 0, result.output
        assert run.call_args.kwargs["persist_toggles"] is persist
