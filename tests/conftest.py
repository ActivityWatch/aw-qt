"""Shared fixtures.

Every test runs with the platform dirs that aw-core resolves (config, data,
cache, log) pointed into a temporary directory. The XDG_* variables some tests
set only isolate Linux; on macOS and Windows platformdirs ignores them, so
without this a test that writes a config file would overwrite the real one.
"""

import os
from pathlib import Path

import platformdirs
import pytest


@pytest.fixture(autouse=True)
def _isolated_platform_dirs(tmp_path, monkeypatch):
    # Same layout as the XDG_*_HOME dirs set by the ``xdg_tmp`` fixture.
    root = tmp_path

    def _dir(kind: str):
        def get(appname=None, *args, **kwargs) -> str:
            return os.path.join(str(root / kind), appname or "")

        return get

    for kind in ("data", "config", "cache", "log"):
        monkeypatch.setattr(platformdirs, f"user_{kind}_dir", _dir(kind))
    monkeypatch.setattr(
        platformdirs,
        "user_cache_path",
        lambda appname=None, *a, **k: Path(_dir("cache")(appname)),
    )
    yield root
