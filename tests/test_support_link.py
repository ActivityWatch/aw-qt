"""The tray's support item opens the attributed /go/ URL."""

from aw_qt import trayicon


def test_open_support_uses_attributed_url(monkeypatch):
    opened = []
    monkeypatch.setattr(trayicon, "open_url", opened.append)
    trayicon.open_support()
    assert opened == ["https://activitywatch.net/go/?src=qt-tray"]
