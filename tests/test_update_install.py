"""The Windows updater: it must not wait forever or fail silently (2026.11.23: 'it downloads, I restart, nothing
happens, and the old version starts again')."""
import os

import tagger_core as core


def test_windows_updater_stops_what_is_left_and_reports(tmp_path):
    root = str(tmp_path / "IPTC Tagger")
    os.makedirs(os.path.join(root, "_internal"))
    s = core.updater_script(r"C:\Temp\upd\new\IPTC Tagger", root, r"C:\Temp\upd", 4321, True, windows=True,
                            version="2026.11.23.3")
    assert 'find " 4321 "' in s                                 # the PID exactly, not inside another number
    assert "if %tries% GEQ 30 goto closed" in s                 # never waits forever
    assert "taskkill /PID 4321 /F" in s                         # an app that didn't quite close is stopped
    assert "Stop-Process -Force" in s and root.replace("'", "''") in s    # ExifTool etc. from the program folder
    assert f'/LOG:"{core.update_log_path()}"' in s
    assert "failed 2026.11.23.3" in s and "installed 2026.11.23.3" in s and core.update_report_path() in s
    assert s.index("Stop-Process") < s.index("robocopy") < s.index('start ""')
    # a folder that isn't the app's own: nothing in it is stopped
    other = str(tmp_path / "Desktop")
    os.makedirs(other)
    assert "Stop-Process" not in core.updater_script("x", other, "w", 1, False, windows=True)


import shutil
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="runs the real Windows updater script")
def test_windows_updater_replaces_a_locked_program(tmp_path):
    """A program still running from the app folder (like the background ExifTool) locks its file; the updater
    must stop it, copy the new version over and say it worked."""
    root = tmp_path / "IPTC Tagger"
    (root / "_internal").mkdir(parents=True)
    ping = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "PING.EXE")
    shutil.copy(ping, root / "locker.exe")
    (root / "IPTC Tagger.exe").write_bytes(b"old")
    new = tmp_path / "work" / "new" / "IPTC Tagger"
    new.mkdir(parents=True)
    shutil.copy(ping, new / "locker.exe")
    with open(new / "locker.exe", "ab") as f:
        f.write(b"\0new version")                               # different bytes, same program
    (new / "IPTC Tagger.exe").write_bytes(b"new")
    locker = subprocess.Popen([str(root / "locker.exe"), "-n", "120", "127.0.0.1"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    gone = subprocess.Popen(["cmd", "/c", "exit"])
    gone.wait()                                                 # the "app": already closed
    try:
        if os.path.exists(core.update_report_path()):
            os.remove(core.update_report_path())
        script = core.updater_script(str(new), str(root), str(tmp_path / "work"), gone.pid, False, windows=True,
                                     version="9.9.9")
        path = tmp_path / "update.cmd"
        path.write_text(script, encoding="utf-8", newline="")
        run = subprocess.run(["cmd", "/c", str(path)], timeout=120, capture_output=True, text=True, errors="replace")
        time.sleep(1)
        log = open(core.update_log_path(), errors="replace").read()[-3000:] if os.path.exists(
            core.update_log_path()) else "(no log)"
        assert (root / "IPTC Tagger.exe").read_bytes() == b"new", (script, run.stdout, run.stderr, log)
        assert (root / "locker.exe").read_bytes().endswith(b"\0new version")
        with open(core.update_report_path(), encoding="utf-8") as f:
            assert f.read().strip() == "installed 9.9.9"
        assert locker.poll() is not None                        # it was stopped
    finally:
        if locker.poll() is None:
            locker.kill()
