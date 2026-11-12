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
    assert r["Image Name"] == "Pepperidge Farm Sausalito Cookies on Grocery Shelf"     # place and date left out
    assert r["keywords"] == "pepperidge farm,cookies,grocery"
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


class FakeFolder(FakeFTP):
    """An FTP folder that Dreamstime empties: each listing shows the next list of files still waiting."""
    def __init__(self, listings):
        super().__init__()
        self.listings = list(listings)

    def listdir(self):
        return self.listings.pop(0) if len(self.listings) > 1 else self.listings[0]


def _login(monkeypatch):
    monkeypatch.setattr(up, "load_sites", lambda: [{"name": "dreamstime", "username": "1234567", "password": "pw",
                                                    "host": "upload.dreamstime.com", "port": 21, "protocol": "ftp",
                                                    "remote_dir": "/"}])


def test_csv_is_sent_once_dreamstime_has_taken_the_photos(tmp_path, monkeypatch):
    _login(monkeypatch)
    csv_file = tmp_path / "dreamstime_2026-11-11_11-20.csv"
    csv_file.write_text("x")
    folder = FakeFolder([["a.jpg", "b.jpg", "old.csv"], ["b.jpg"], ["old.csv"]])   # taken in, one by one
    w = up.CsvAfterUpload("dreamstime", str(csv_file), ["a.jpg", "b.jpg"], every=0.01, first_wait=0,
                          connect_fn=lambda site, **kw: folder)
    w.start()
    w.join(5)
    assert w.state["stage"] == "sent" and w.state["checks"] == 3
    assert folder.put_as == [(str(csv_file), csv_file.name)]                      # only after both had left


def test_csv_is_sent_anyway_after_the_longest_wait(tmp_path, monkeypatch):
    _login(monkeypatch)
    csv_file = tmp_path / "d.csv"
    csv_file.write_text("x")
    folder = FakeFolder([["a.jpg"]])                                               # never taken in
    w = up.CsvAfterUpload("dreamstime", str(csv_file), ["a.jpg"], every=0.01, first_wait=0, longest=0.05,
                          connect_fn=lambda site, **kw: folder)
    w.start()
    w.join(5)
    assert w.state["stage"] == "sent" and "sent anyway" in w.state["message"] and folder.put_as


def test_waiting_can_be_stopped(tmp_path, monkeypatch):
    _login(monkeypatch)
    w = up.CsvAfterUpload("dreamstime", str(tmp_path / "d.csv"), ["a.jpg"], first_wait=30)
    w.start()
    w.stop()
    w.join(5)
    assert w.state["stage"] == "stopped"
