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


TITLE = ("Tokyo, Japan - November 4, 2026: Gerber Puffs Cereal Snacks Variety Pack on Grocery "
         "Store Shelf, Featuring Banana, Apple Cinnamon, Sweet Potato and Strawberry Flavors")
CAPTION = ("Tokyo, Japan - November 4, 2026: A colorful display of Gerber Graduates Puffs cereal "
           "snacks is arranged on a grocery store shelf, showcasing various flavors like banana.")


def _shutterstock_csv(tmp_path, **kw):
    import csv
    p = tmp_path / "shutterstock.csv"
    core.export_csv(str(p), [{"file": "a.jpg", "folder": "", "title": TITLE, "description": CAPTION,
                              "keywords": ["gerber", "puffs"], "categories": ["Food and drink"]}], "shutterstock", **kw)
    return list(csv.DictReader(open(p, encoding="utf-8")))[0]


def test_shutterstock_csv_gets_the_title(tmp_path):
    assert _shutterstock_csv(tmp_path)["Description"] == TITLE                 # what you see in the list


def test_shutterstock_csv_can_get_the_description(tmp_path):
    assert _shutterstock_csv(tmp_path, ss_text="description")["Description"] == CAPTION


def test_shutterstock_checks_look_at_what_it_gets():
    long_title = "Word " * 45                                                 # 225 characters
    out = core.site_problems(long_title.strip(), ["k"] * 10, ["shutterstock"], "A short caption that fits fine here.")
    assert out and "title 224 characters (most 200)" in out[0]
    out = core.site_problems(long_title.strip(), ["k"] * 10, ["shutterstock"], "A short caption that fits fine here.",
                             ss_text="description")
    assert out == []


def _fake_stockup(path, when):
    """A stand-in StockUp.exe whose file date is `when` (like an unzipped download keeps its build date)."""
    import time as _t
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("stand-in")
    stamp = _t.mktime(_t.strptime(when, "%Y-%m-%d"))
    os.utime(path, (stamp, stamp))
    return path


def test_newest_copy_is_opened_even_if_an_older_one_was_chosen(tmp_path, monkeypatch):
    old = _fake_stockup(str(tmp_path / "StockUp-Windows" / "StockUp" / "StockUp.exe"), "2026-10-01")
    new = _fake_stockup(str(tmp_path / "StockUp-Windows (1)" / "StockUp" / "StockUp.exe"), "2026-11-04")
    monkeypatch.setattr(core, "stockup_candidates", lambda custom="": [old, new])
    assert core.find_stockup(old) == new


def test_stockups_own_update_backup_is_never_used(tmp_path):
    backup = _fake_stockup(str(tmp_path / "StockUp-update" / "previous" / "StockUp" / "StockUp.exe"), "2026-11-05")
    assert core.stockup_candidates(backup) == []
    assert core.find_stockup(backup) is None


@pytest.mark.skipif(os.name != "nt", reason="the Windows folders that are searched")
def test_windows_search_finds_every_copy_and_opens_the_newest(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for var, sub in (("USERPROFILE", "home"), ("LOCALAPPDATA", "local"), ("ProgramFiles", "programs"),
                     ("OneDrive", "onedrive")):
        (tmp_path / sub).mkdir(exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setattr(core, "program_root", lambda: str(tmp_path / "tagger" / "IPTC Tagger"))
    old = _fake_stockup(str(home / "Downloads" / "StockUp-Windows" / "StockUp" / "StockUp.exe"), "2026-10-01")
    new = _fake_stockup(str(home / "Downloads" / "StockUp-Windows (1)" / "StockUp" / "StockUp.exe"), "2026-11-04")
    on_drive = _fake_stockup(str(tmp_path / "onedrive" / "Desktop" / "StockUp" / "StockUp.exe"), "2026-09-01")
    found = {os.path.normcase(p) for p in core.stockup_candidates("")}
    assert {os.path.normcase(p) for p in (old, new, on_drive)} <= found
    assert os.path.normcase(core.find_stockup(old)) == os.path.normcase(new)   # an old one chosen in Settings
