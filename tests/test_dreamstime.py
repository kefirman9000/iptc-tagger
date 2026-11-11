"""The Dreamstime CSV: what's in it, and sending it to the Dreamstime FTP folder (the only place it's read from)."""
import csv
import os

import pytest

import site_files
import uploader as up

HEADER = ["Filename", "Image Name", "Description", "Category 1", "Category 2", "Category 3", "keywords", "Free",
          "W-EL", "P-EL", "SR-EL", "SR-Price", "Editorial", "MR doc Ids", "Pr Docs"]


def _rows():
    return [{"file": "20261111-_DSC8043.jpg", "folder": "", "editorial": True,
             "title": "Apex, North Carolina - November 11, 2026: Pepperidge Farm Sausalito Cookies on Grocery Shelf",
             "description": "Apex, North Carolina - November 11, 2026: A close-up of Pepperidge Farm cookies on a shelf.",
             "keywords": ["pepperidge farm", "cookies", "grocery"]}]


def test_dreamstime_csv_has_dreamstimes_columns(tmp_path):
    path = site_files.write_site_csvs(str(tmp_path), _rows(), ["dreamstime"])[0]
    rows = list(csv.reader(open(path, encoding="utf-8", newline="")))
    assert rows[0] == HEADER
    r = dict(zip(HEADER, rows[1]))
    assert r["Filename"] == "20261111-_DSC8043.jpg" and r["Editorial"] == "1"
    assert r["Image Name"].startswith("Apex, North Carolina") and r["keywords"] == "pepperidge farm,cookies,grocery"
    assert all(r[f"Category {i}"] not in ("", "0") for i in (1, 2, 3))


class FakeFTP:
    def __init__(self):
        self.put_as, self.closed, self.encrypted = [], False, True

    def put(self, local, name, progress):
        self.put_as.append((local, name))

    def close(self):
        self.closed = True


def test_send_puts_the_csv_in_the_ftp_folder(tmp_path, monkeypatch):
    csv_file = tmp_path / "dreamstime_2026-11-11_09-56.csv"
    csv_file.write_text("x")
    login = {"name": "dreamstime", "username": "1234567", "password": "pw", "host": "upload.dreamstime.com",
             "port": 21, "protocol": "ftp", "remote_dir": "/"}
    monkeypatch.setattr(up, "load_sites", lambda: [login])
    ftp, used = FakeFTP(), []
    msg = up.send_csv("dreamstime", str(csv_file), connect_fn=lambda site, **kw: used.append(site) or ftp)
    assert used[0]["username"] == "1234567"
    assert ftp.put_as == [(str(csv_file), "dreamstime_2026-11-11_09-56.csv")] and ftp.closed
    assert "encrypted" in msg


def test_send_without_a_login_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "load_sites", lambda: [{"name": "dreamstime", "username": "", "password": ""}])
    with pytest.raises(RuntimeError, match="No Dreamstime login"):
        up.send_csv("dreamstime", str(tmp_path / "x.csv"))


def test_dreamstime_next_step_mentions_the_csv():
    assert "Send Dreamstime CSV" in up.SITES["dreamstime"]["after"] and "Unfinished" in up.SITES["dreamstime"]["after"]
