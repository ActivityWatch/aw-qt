"""Regressions for cancellation, shutdown and profile identity at spawn."""

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aw_qt.config import _with_profile_env
from aw_qt.manager import Manager, Module


def make_manager(name):
    with patch.object(Manager, "discover_modules"):
        manager = Manager()
    module = Module(name, Path("/synthetic") / name, "bundled")
    manager.modules = [module]
    return manager, module


@pytest.mark.parametrize("action,name", [("stop", "aw-sync"), ("stop_notify", "aw-notify"), ("stop_all", "aw-sync")])
def test_stop_cancels_start_waiting_for_environment(action, name):
    manager, module = make_manager(name)
    entered = threading.Event()
    release = threading.Event()

    def environment():
        entered.set()
        assert release.wait(5)
        return {}

    with patch("aw_qt.manager.child_environment", side_effect=environment), patch.object(
        module, "_probe_external_server", return_value=False
    ), patch("aw_qt.manager.subprocess.Popen") as spawned:
        worker = threading.Thread(target=manager.start, args=(name,), daemon=True)
        worker.start()
        try:
            assert entered.wait(2)
            if action == "stop":
                manager.stop(name)
            else:
                getattr(manager, action)()
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive()
        spawned.assert_not_called()
        assert not module.started


def test_shutdown_waits_for_process_publication():
    manager, module = make_manager("aw-sync")
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    process = MagicMock()
    process.returncode = None
    process.wait.side_effect = lambda: setattr(process, "returncode", 0)

    def popen(*args, **kwargs):
        entered.set()  # OS child exists, but Popen has not returned its handle.
        assert release.wait(5)
        return process

    with patch.object(module, "_probe_external_server", return_value=False), patch(
        "aw_qt.manager.subprocess.Popen", side_effect=popen
    ):
        worker = threading.Thread(target=manager.start, args=(module.name,), daemon=True)
        worker.start()
        assert entered.wait(2)
        shutdown = threading.Thread(target=lambda: (manager.stop_all(), stopped.set()), daemon=True)
        shutdown.start()
        try:
            assert not stopped.wait(0.2), "shutdown returned before the child was published"
        finally:
            release.set()
            worker.join(5)
            shutdown.join(5)
        assert stopped.is_set()
        process.terminate.assert_called_once()
        assert not module.is_alive()


def test_rust_launch_identity_waits_for_profile_rewrite(monkeypatch):
    monkeypatch.setenv("AW_PROFILE", "research")
    manager, module = make_manager("aw-server-rust")
    probed = threading.Event()
    release_probe = threading.Event()
    profile_read = threading.Event()
    from aw_qt.profile import profile_from_env

    def probe(*args, **kwargs):
        probed.set()
        assert release_probe.wait(5)
        return False

    def read_profile(*args):
        profile_read.set()
        return profile_from_env(*args)

    with patch.object(module, "_probe_external_server", side_effect=probe), patch.object(
        module, "_get_server_port", return_value=5601
    ), patch("aw_qt.config.dashboard_auth", return_value=("127.0.0.1", "secret")), patch(
        "aw_qt.profile.profile_from_env", side_effect=read_profile
    ), patch("aw_qt.manager.subprocess.Popen") as spawned:
        worker = threading.Thread(target=manager.start, args=(module.name,), daemon=True)
        worker.start()
        try:
            assert probed.wait(2)
            profile_read.clear()
            with _with_profile_env("default"):
                release_probe.set()
                # Old code reads the temporary default before blocking in snapshot.
                profile_read.wait(0.2)
        finally:
            release_probe.set()
            worker.join(5)
        assert not worker.is_alive()
        assert module.dashboard_endpoint == ("research", "127.0.0.1", 5601)
        assert spawned.call_args.kwargs["env"]["AW_PROFILE"] == "research"


def test_cancelled_claim_cannot_consume_replacement_start():
    manager, module = make_manager("aw-sync")
    with manager._notify_lock:
        old = manager._take_start_locked(module.name)
    manager.stop(module.name)
    with manager._notify_lock:
        replacement = manager._take_start_locked(module.name)
    with patch.object(module, "start") as start:
        manager._spawn_claimed(old)
        start.assert_not_called()
        manager._spawn_claimed(replacement)
        start.assert_called_once()


def test_reentrant_shutdown_during_popen_stops_published_child():
    manager, module = make_manager("aw-sync")
    process = MagicMock()
    process.returncode = None
    process.wait.side_effect = lambda: setattr(process, "returncode", 0)

    def popen(*args, **kwargs):
        # A main-thread signal handler reenters the RLock during Popen.
        manager.stop_all()
        return process

    with patch.object(module, "_probe_external_server", return_value=False), patch(
        "aw_qt.manager.subprocess.Popen", side_effect=popen
    ):
        manager.start(module.name)
    process.terminate.assert_called_once()
    assert not module.is_alive()
