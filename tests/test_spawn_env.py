"""Child processes must never inherit a temporary ``AW_PROFILE`` rewrite.

``_with_profile_env`` rewrites the process environment to resolve another
profile's paths. aw-qt spawns modules from a background thread while the tray
thread can be resolving a profile, so an unserialized spawn used to hand the
child the wrong profile and point ``aw-sync`` at another instance
(ActivityWatch/aw-qt#144).
"""

import os
import threading

from aw_core import dirs

from aw_qt.config import _with_profile_env, child_environment


def test_child_environment_does_not_observe_profile_rewrite(monkeypatch):
    monkeypatch.setenv("AW_PROFILE", "research")

    in_rewrite = threading.Event()
    release = threading.Event()
    real_get_config_dir = dirs.get_config_dir

    def blocking_get_config_dir(*args, **kwargs):
        in_rewrite.set()
        assert release.wait(5), "rewrite was never released"
        return real_get_config_dir(*args, **kwargs)

    monkeypatch.setattr("aw_qt.config.dirs.get_config_dir", blocking_get_config_dir)

    def rewrite():
        with _with_profile_env("default"):
            dirs.get_config_dir("aw-server")

    rewrite_thread = threading.Thread(target=rewrite, daemon=True)
    rewrite_thread.start()
    assert in_rewrite.wait(5), "rewrite never became active"

    # The rewrite has unset AW_PROFILE. A snapshot taken now would lose it.
    assert "AW_PROFILE" not in os.environ

    snapshot_started = threading.Event()
    result = {}

    def snapshot():
        snapshot_started.set()
        result["env"] = child_environment()

    snapshot_thread = threading.Thread(target=snapshot, daemon=True)
    snapshot_thread.start()
    assert snapshot_started.wait(5)
    snapshot_thread.join(0.2)

    assert "env" not in result, (
        "child_environment() observed the temporary AW_PROFILE rewrite; "
        "it must block until the rewrite is restored"
    )

    release.set()
    rewrite_thread.join(5)
    snapshot_thread.join(5)

    assert result["env"]["AW_PROFILE"] == "research"


def test_child_environment_is_a_detached_copy(monkeypatch):
    monkeypatch.setenv("AW_PROFILE", "work")

    env = child_environment()
    monkeypatch.setenv("AW_PROFILE", "other")

    assert env["AW_PROFILE"] == "work"
