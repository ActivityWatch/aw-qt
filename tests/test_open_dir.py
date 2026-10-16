"""Exercise folder-opening failures with real external processes."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PyQt6 import QtCore
from PyQt6.QtWidgets import QApplication, QMessageBox

from aw_qt import trayicon


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux opener")


@pytest.fixture(scope="module")
def app():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
        application = QApplication.instance() or QApplication([])
        application.setQuitOnLastWindowClosed(False)
        yield application


@pytest.fixture
def errors(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        trayicon,
        "_show_open_dir_error",
        lambda path, details: recorded.append((path, details)),
        raising=False,
    )
    return recorded


@pytest.fixture
def opener(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    result = tmp_path / "opener-result"
    monkeypatch.setenv("OPENER_RESULT", str(result))

    def install(body):
        executable = bin_dir / "xdg-open"
        executable.write_text("#!/bin/sh\n" + body)
        executable.chmod(0o755)
        return result

    return install


def wait_until(app, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition(), "Timed out waiting for opener result"


def process_for(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


@pytest.mark.parametrize("path_type", [str, Path])
def test_failed_opener_reports_path_and_stderr_once(app, opener, errors, path_type):
    opener("printf 'No file manager is installed\\n' >&2\nexit 4\n")
    folder = "/tmp/config folder with spaces"

    trayicon.open_dir(path_type(folder))

    wait_until(app, lambda: bool(errors))
    process_for(app, 0.25)
    assert len(errors) == 1
    assert errors[0][0] == folder
    assert "No file manager is installed" in errors[0][1]


def test_missing_opener_reports_error(app, opener, errors):
    # The fixture supplies an empty PATH directory, without installing an opener.
    folder = "/tmp/logs"

    trayicon.open_dir(folder)

    wait_until(app, lambda: bool(errors))
    assert len(errors) == 1
    assert errors[0][0] == folder
    assert "xdg-open" in errors[0][1]


def test_success_is_silent_and_preserves_argument(app, opener, errors):
    result = opener("printf '%s\\n%s' \"$#\" \"$1\" > \"$OPENER_RESULT\"\n")
    folder = "/tmp/config folder with spaces and 'quotes'"

    trayicon.open_dir(folder)

    wait_until(app, result.exists)
    process_for(app, 0.25)
    assert result.read_text() == "1\n" + folder
    assert errors == []


def test_slow_opener_does_not_block_event_loop(app, opener, errors):
    result = opener("/bin/sleep 0.3\nprintf done > \"$OPENER_RESULT\"\n")
    heartbeats = []
    timer = QtCore.QTimer()
    timer.timeout.connect(lambda: heartbeats.append(result.exists()))
    timer.start(10)
    try:
        trayicon.open_dir("/tmp/logs")
        wait_until(app, result.exists)
        process_for(app, 0.25)
    finally:
        timer.stop()

    assert False in heartbeats, "Qt did not process a heartbeat while opener ran"
    assert errors == []


@pytest.mark.parametrize("original", [None, "/host/custom-libraries"])
def test_child_library_path_is_restored_without_changing_parent(
    app, opener, errors, monkeypatch, original
):
    result = opener('printf "%s" "${LD_LIBRARY_PATH-unset}" > "$OPENER_RESULT"\n')
    bundled = "/bundled/activitywatch/libraries"
    monkeypatch.setenv("LD_LIBRARY_PATH", bundled)
    if original is None:
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original)

    trayicon.open_dir("/tmp/logs")

    wait_until(app, result.exists)
    process_for(app, 0.25)
    assert result.read_text() == (original if original is not None else "unset")
    assert os.environ["LD_LIBRARY_PATH"] == bundled
    assert errors == []


def test_external_opener_survives_application_exit(opener, monkeypatch):
    result = opener("/bin/sleep 0.3\nprintf survived > \"$OPENER_RESULT\"\n")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    repo = Path(__file__).resolve().parents[1]
    script = """
from PyQt6.QtWidgets import QApplication
from aw_qt.trayicon import open_dir
app = QApplication([])
open_dir('/tmp/logs')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    deadline = time.monotonic() + 3
    while not result.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert result.exists(), "Exiting ActivityWatch killed the external opener"
    assert result.read_text() == "survived"


def test_error_dialog_displays_diagnostics_and_copies_literal_path(app):
    folder = "/tmp/config folder/<literal-name>"
    diagnostics = "xdg-open exited with status 4.\nNo file manager is installed."
    observed = {}

    def inspect_and_close():
        box = app.activeModalWidget()
        try:
            if not isinstance(box, QMessageBox):
                observed["error"] = "Expected a modal QMessageBox"
                return
            observed["format"] = box.textFormat()
            observed["path"] = box.informativeText()
            observed["details"] = box.detailedText()
            copy_button = next(b for b in box.buttons() if b.text() == "Copy path")
            copy_button.click()
            observed["clipboard"] = app.clipboard().text()
        except Exception as exc:
            # Exceptions escaping Qt callbacks can abort the test process.
            observed["error"] = repr(exc)
        finally:
            if isinstance(box, QMessageBox):
                box.close()
            else:
                app.closeAllWindows()

    QtCore.QTimer.singleShot(0, inspect_and_close)
    trayicon._show_open_dir_error(folder, diagnostics)

    assert "error" not in observed, observed.get("error")
    assert observed["format"] == QtCore.Qt.TextFormat.PlainText
    assert observed["path"] == folder
    assert observed["details"] == diagnostics
    assert observed["clipboard"] == folder
