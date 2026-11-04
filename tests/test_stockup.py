"""Handing photos to StockUp."""
import os

import pytest

import tagger_core as core


def test_a_chosen_stockup_is_used(tmp_path):
    script = tmp_path / "stockup.py"
    script.write_text("print('hi')")
    assert core.find_stockup(str(script)) == str(script)


@pytest.mark.skipif(os.name != "nt", reason="Windows command line")
def test_windows_command_and_long_lists(tmp_path):
    exe = r"C:\StockUp\StockUp.exe"
    two = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
    cmd, note = core.stockup_command(exe, two)
    assert cmd == [exe] + two and note == ""
    many = [str(tmp_path / f"{i:04d}_a_long_file_name_from_the_camera.jpg") for i in range(900)]
    cmd, note = core.stockup_command(exe, many)
    assert cmd == [exe, str(tmp_path)] and "whole folder" in note
    with pytest.raises(ValueError):
        core.stockup_command(exe, many + [str(tmp_path / "other" / "x.jpg")])


def test_mac_command_shapes():
    cmd, _ = core.stockup_command("/Applications/StockUp.app", ["/p/a.jpg"])
    assert cmd == ["open", "-a", "/Applications/StockUp.app", "/p/a.jpg"]
    cmd, _ = core.stockup_command("/Applications/StockUp.app", ["/p/a.jpg"], new_window=True)
    assert cmd[:3] == ["open", "-n", "-a"]
