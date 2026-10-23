# aw-qt

[![GitHub Actions badge](https://github.com/ActivityWatch/aw-qt/workflows/Build/badge.svg)](https://github.com/ActivityWatch/aw-qt/actions)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Typechecking: Mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)

A service manager and tray icon managing aw-server and watchers, built with Qt.

Use `--profile NAME` to run an isolated named instance. Enabling **Start at login**
from that instance's tray registers the same profile-specific launch command.

When using `aw-server-rust` with opt-in `[auth].api_key`, **Open Dashboard**
and a tray double-click pass the matching profile's key to the web UI's token
bootstrap. The key is read when opening the dashboard, not stored in the tray URL.
The Rust server must be the only started server backend, be a live child owned
by this launcher (not a listener adopted through the public health probe), and
its configured loopback port must match the tray destination. Authenticated URLs
use the configured numeric address (`127.0.0.1` or `[::1]`), not `localhost`, to
avoid sending the key to another address family's listener on the same port.
For authenticated children the launcher pins this address and port with command
line overrides and records the profile/endpoint. Changing the address or port,
or enabling auth after startup, requires restarting the server before handoff;
key-only edits at the same endpoint are still read at click time.
Missing or invalid configuration leaves the dashboard URL unchanged. Hostname
bind addresses are not eligible for key handoff. The API Browser is not
token-bootstrapped.
This does not enable authentication automatically or make separate ports a
security boundary between OS users. Treat a dashboard bootstrap URL as a secret:
it is briefly passed to the OS/browser opener and can be visible in process
arguments before the web UI removes it from the address bar.

For instructions how to build, see `Makefile`.
