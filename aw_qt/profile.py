"""Profile resolution for aw-qt.

A *profile* names an isolated ActivityWatch instance (data, config, port,
lockfile). `default` is the ordinary install, `testing` is what `--testing`
has always meant, and any other name (for example `research`) is a sibling
instance that can run at the same time as the others.

aw-qt only launches things, so its job is small: resolve the profile, export
it as ``AW_PROFILE`` for the modules it spawns, and use the profile suffix for
the things aw-qt itself owns (lockfile, config section, port lookup).
"""

import os
import re
from typing import Optional

DEFAULT_PROFILE = "default"
TESTING_PROFILE = "testing"

#: Same rule as aw-server-rust's `validate_profile`: lowercase alphanumeric
#: plus `-`/`_`, at most 32 chars, so a profile is always a safe path segment.
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

ENV_VAR = "AW_PROFILE"


def validate_profile(profile: str) -> str:
    """Return the profile unchanged, or raise ValueError if it is not usable."""
    if not PROFILE_RE.match(profile):
        raise ValueError(
            f"Invalid profile name {profile!r}: expected lowercase alphanumeric "
            "with '-' or '_', at most 32 characters"
        )
    return profile


def resolve_profile(profile: Optional[str], testing: bool) -> str:
    """Resolve the effective profile from the CLI flags.

    ``--testing`` is an alias for ``--profile testing``; passing both is only
    an error if they disagree.
    """
    if profile is None:
        return TESTING_PROFILE if testing else DEFAULT_PROFILE

    profile = validate_profile(profile)
    if testing and profile != TESTING_PROFILE:
        raise ValueError(
            f"--testing conflicts with --profile {profile}: --testing is an "
            f"alias for --profile {TESTING_PROFILE}"
        )
    return profile


def is_testing(profile: str) -> bool:
    return profile == TESTING_PROFILE


def profile_suffix(profile: str) -> str:
    """Filename suffix for a profile (``""``, ``"-testing"``, ``"-research"``)."""
    return "" if profile == DEFAULT_PROFILE else f"-{profile}"


def profile_from_env(testing: bool = False) -> str:
    """Read the profile the process was started with.

    Falls back to the `--testing` bool for callers that only track that, so
    behaviour is unchanged when no profile was set.
    """
    profile = os.environ.get(ENV_VAR)
    if not profile:
        return TESTING_PROFILE if testing else DEFAULT_PROFILE
    try:
        return validate_profile(profile)
    except ValueError:
        return TESTING_PROFILE if testing else DEFAULT_PROFILE


def export_profile(profile: str) -> None:
    """Publish the profile to child processes.

    Modules are spawned as subprocesses, so they inherit the profile without
    aw-qt threading a flag through every module's CLI.
    """
    os.environ[ENV_VAR] = profile
