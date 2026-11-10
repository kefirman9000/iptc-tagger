"""Uploading to stock sites, against small FTP/FTPS servers running inside the tests (adapted from StockUp)."""
import csv
import hashlib
import json
import os
import time

import pytest
from PIL import Image

import uploader as up
from ftp_server import FTPTestServer, make_cert
from test_window import qapp, window  # noqa: F401  (the real window, offscreen)


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def wait(cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture(autouse=True)
def no_keychain_or_stockup(monkeypatch, tmp_path):
    monkeypatch.setattr(up, "_keyring", lambda: None)               # never touch your real keychain
    monkeypatch.setattr(up, "stockup_dirs", lambda: [str(tmp_path / "StockUp")])


@pytest.fixture
def cert(tmp_path):
    c = make_cert(str(tmp_path))
    if not c:
        pytest.skip("openssl isn't installed, so the encrypted-connection tests can't run")
    return c


@pytest.fixture
def servers(tmp_path):
    made = []

    def make(name, cert=None, **kw):
        root = tmp_path / f"server_{name}"
        root.mkdir()
        s = FTPTestServer(str(root), cert=cert, **kw)
        s.root = str(root)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def photos(folder, n=3, size=(1600, 1200)):
    out = []
    for i in range(n):
        p = os.path.join(folder, f"p{i}.jpg")
        Image.effect_noise(size, 60 + i).convert("RGB").save(p, quality=95)   # big enough to take a moment
        out.append(p)
    return out


def site(server, name, password="pw"):
    s = server.site(name, password=password)
    s["label"] = up.SITES[name]["label"]
    return s


def run(paths, sites, **kw):
    u = up.Uploader(paths, sites, **kw)
    assert u.start()
    wait(lambda: not u.status()["running"])
    return u


def test_uploads_exact_files_plain_and_encrypted(tmp_path, servers, cert):
    ps = photos(str(tmp_path))
    plain, enc = servers("plain"), servers("enc", cert=cert)
    u = run(ps, [site(plain, "shutterstock"), site(enc, "dreamstime")])
    st = u.status()["sites"]
    assert st["shutterstock"]["done"] == st["dreamstime"]["done"] == 3
    assert st["shutterstock"]["encrypted"] is False and st["dreamstime"]["encrypted"] is True
    for s in (plain, enc):
        assert sorted(os.listdir(s.root)) == ["p0.jpg", "p1.jpg", "p2.jpg"]
        assert all(sha(os.path.join(s.root, f)) == sha(os.path.join(str(tmp_path), f)) for f in os.listdir(s.root))
    rows = list(csv.DictReader(open(up.receipt_path(), encoding="utf-8")))
    assert len(rows) == 6 and {r["encrypted"] for r in rows if r["site"] == "Dreamstime"} == {"yes"}


def test_nothing_is_sent_twice(tmp_path, servers):
    ps = photos(str(tmp_path), 2)
    srv = servers("a")
    run(ps, [site(srv, "alamy")])
    assert len(srv.stors) == 2
    u = run(ps, [site(srv, "alamy")])
    assert len(srv.stors) == 2 and u.status()["sites"]["alamy"]["skipped"] == 2       # remembered
    # a photo StockUp already sent counts too
    os.makedirs(up.stockup_dirs()[0], exist_ok=True)                # (a temp folder in tests)
    os.makedirs(tmp_path / "x")
    extra = photos(str(tmp_path / "x"), 1)
    with open(os.path.join(up.stockup_dirs()[0], "upload_history.json"), "w", encoding="utf-8") as fh:
        json.dump({"sha": {sha(extra[0]): {"pond5": {"date": "2026-11-01"}}}}, fh)
    p = up.plan(extra, "pond5")
    assert not p["send"] and p["sent"][0][1] == "before (by StockUp)"


def test_a_file_already_on_the_server_is_not_overwritten(tmp_path, servers):
    ps = photos(str(tmp_path), 1)
    srv = servers("s")
    with open(os.path.join(srv.root, "p0.jpg"), "wb") as fh:
        fh.write(b"someone else's file")
    u = run(ps, [site(srv, "123rf")])
    assert open(os.path.join(srv.root, "p0.jpg"), "rb").read() == b"someone else's file"
    assert "already on the 123RF server" in " ".join(u.status()["sites"]["123rf"]["notes"])


def test_stop_part_way_removes_the_half_sent_photo(tmp_path, servers):
    ps = photos(str(tmp_path), 1, size=(3000, 2400))
    srv = servers("slow", slow=0.05)
    u = up.Uploader(ps, [site(srv, "depositphotos")])
    u.start()
    wait(lambda: u.status()["sites"]["depositphotos"]["bytes"] > 100_000)
    u.stop()
    wait(lambda: not u.status()["running"])
    assert u.status()["sites"]["depositphotos"]["state"] == "stopped"
    assert os.listdir(srv.root) == []                                  # never half a photo on the site


def test_pause_and_resume_sends_a_complete_photo(tmp_path, servers):
    ps = photos(str(tmp_path), 1, size=(3000, 2400))
    srv = servers("slow", slow=0.02)
    u = up.Uploader(ps, [site(srv, "pond5")])
    u.start()
    wait(lambda: u.status()["sites"]["pond5"]["bytes"] > 100_000)
    u.pause(True)
    wait(lambda: u.status()["sites"]["pond5"]["state"] == "paused")
    assert os.listdir(srv.root) == []                                  # the half-sent part was removed
    srv.slow = 0
    u.pause(False)
    wait(lambda: not u.status()["running"])
    assert sha(os.path.join(srv.root, "p0.jpg")) == sha(ps[0])


def test_wrong_password_stops_that_site_only(tmp_path, servers):
    ps = photos(str(tmp_path), 1)
    good, bad = servers("good"), servers("bad")
    u = run(ps, [site(good, "alamy"), site(bad, "shutterstock", password="nope")])
    st = u.status()["sites"]
    assert st["shutterstock"]["state"] == "error" and "Couldn't connect" in st["shutterstock"]["errors"][0]
    assert st["alamy"]["done"] == 1


def test_only_jpegs_go(tmp_path):
    ps = photos(str(tmp_path), 1)
    raw = tmp_path / "p.nef"
    raw.write_bytes(b"raw")
    p = up.plan(ps + [str(raw)], "shutterstock")
    assert p["send"] == ps and p["left_out"][0][0] == str(raw)


def test_safe_names_for_sites_that_mangle_accents():
    assert up.remote_name("shutterstock", "/x/Café Crème #2.jpg") == "Cafe_Creme_2.jpg"
    assert up.remote_name("alamy", "/x/Café Crème #2.jpg") == "Café Crème #2.jpg"


def test_logins_saved_and_shared_with_stockup(tmp_path):
    up.save_login("alamy", "me@example.com", "secret")
    s = {x["name"]: x for x in up.load_sites()}
    assert s["alamy"]["username"] == "me@example.com" and s["alamy"]["password"] == "secret"
    assert s["alamy"]["source"] == "IPTC Tagger"
    if os.name != "nt":
        assert oct(os.stat(up.logins_path()).st_mode & 0o777) == "0o600"      # only you can read it
    # a login saved in StockUp is used when IPTC Tagger has none for that site
    d = up.stockup_dirs()[0]
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "stockup_sites.ini"), "w", encoding="utf-8") as fh:
        fh.write("[shutterstock]\nusername = ssuser\npassword = sspw\nremote_dir = /\n")
    s = {x["name"]: x for x in up.load_sites()}
    assert (s["shutterstock"]["username"], s["shutterstock"]["password"], s["shutterstock"]["source"]) == \
        ("ssuser", "sspw", "StockUp")
    up.save_login("alamy", "", clear=True)
    assert {x["name"]: x for x in up.load_sites()}["alamy"]["username"] == ""


def test_upload_window_end_to_end(window, make_photos, servers, monkeypatch):
    from test_window import add, wait as wait_ui
    app, win = window
    paths = make_photos(2)
    add(win, paths)
    a, b = win.photos
    ss, al = servers("ss"), servers("al")
    sites = []
    for name, srv in (("shutterstock", ss), ("alamy", al)):
        s = site(srv, name)
        s["source"] = "IPTC Tagger"
        sites.append(s)
    for name in [n for n in up.SITE_ORDER if n not in ("shutterstock", "alamy")]:     # the rest: no login
        sites.append({"name": name, "label": up.SITES[name]["label"], "protocol": "ftp", "host": "", "port": 21,
                      "username": "", "password": "", "remote_dir": "/", "source": ""})
    monkeypatch.setattr(up, "load_sites", lambda: [dict(x) for x in sites])
    a.title = "A New Title Not Saved Yet"                       # unsaved: the upload has to wait
    w = win.open_upload([a, b])
    wait_ui(lambda: bool(w.plans), what="plans")
    assert w.rows["shutterstock"]["cb"].isChecked() and "dreamstime" not in w.rows   # only agencies with a login
    assert "more agencies" in w.more.text()
    w._refresh_buttons()
    assert not w.btn_start.isEnabled() and w.warn_box.isVisibleTo(w)
    win.save_photos([a])
    wait_ui(lambda: not a.changed and not a.busy, what="save")
    w._refresh_buttons()
    assert w.btn_start.isEnabled()
    w.start()
    wait_ui(lambda: not w.uploader.status()["running"], what="upload", timeout=60)
    w._tick()
    names = sorted(os.path.basename(p) for p in paths)
    assert sorted(os.listdir(al.root)) == names                              # Alamy: the names as they are
    assert sorted(os.listdir(ss.root)) == sorted(up.safe_name(n) for n in names)   # Shutterstock: no accents
    assert all(sha(os.path.join(al.root, os.path.basename(p))) == sha(p) for p in paths)   # exact, as saved
    assert w.next_steps.isVisibleTo(w) and "Submit page" in w.next_steps.text()
    assert w.btn_ss_csv.isVisibleTo(w)
    w.close()


def test_agencies_that_show_their_server_and_your_own(tmp_path, servers):
    srv = servers("fp")
    up.save_login("freepik", "me", "pw", host="127.0.0.1", port=srv.port, protocol="ftp")
    up.save_login("other1", "u", "pw", host="127.0.0.1", port=srv.port, protocol="ftp", label="Mostphotos")
    s = {x["name"]: x for x in up.load_sites()}
    assert (s["freepik"]["host"], s["freepik"]["port"]) == ("127.0.0.1", srv.port)
    assert s["other1"]["label"] == "Mostphotos"
    assert s["vecteezy"]["protocol"] == "sftp" and s["vecteezy"]["host"] == ""     # nothing saved yet
    ps = photos(str(tmp_path), 1)
    u = run(ps, [dict(s["other1"], username="u", password="pw")])
    assert u.status()["sites"]["other1"]["label"] == "Mostphotos" and os.listdir(srv.root) == ["p0.jpg"]
    rows = list(csv.DictReader(open(up.receipt_path(), encoding="utf-8")))
    assert rows[-1]["site"] == "Mostphotos"
    assert "no server yet" in up.test_login(dict(s["vecteezy"], username="x", password="y"))


def test_a_csv_for_each_site_with_categories(tmp_path):
    import site_files as sf
    rows = [{"file": "a.jpg", "folder": "", "title": "Protein Shakes on a Store Shelf for Health-Conscious Consumers",
             "description": "", "keywords": ["protein shake", "nutrition", "fitness", "grocery store"],
             "editorial": True, "categories": [], "adobe_category": None, "city": "Tokyo", "region": "",
             "country": "Japan", "bare_title": "Protein Shakes on a Store Shelf for Health-Conscious Consumers"},
            {"file": "b.jpg", "folder": "", "title": "Paint Cans on a Store Shelf", "description": "",
             "keywords": ["paint", "hardware store", "diy"], "editorial": False, "categories": [],
             "adobe_category": None}]
    files = sf.write_site_csvs(str(tmp_path), rows, [k for k, _, _ in sf.SITE_CSVS], day="2026-11-09")
    assert sorted(os.path.basename(f) for f in files) == sorted(
        ["shutterstock_2026-11-09.csv", "adobe_2026-11-09.csv", "dreamstime_2026-11-09.csv",
         "depositphotos_2026-11-09.csv", "123rf_2026-11-09.csv", "pond5_2026-11-09.csv",
         "metadata_2026-11-09.csv"])
    read = lambda n: list(csv.DictReader(open(os.path.join(str(tmp_path), n + "_2026-11-09.csv"), encoding="utf-8-sig")))
    ss, ad, dt = read("shutterstock"), read("adobe"), read("dreamstime")
    assert ss[0]["Categories"] == "Food and drink,Objects" and ss[1]["Categories"] == "Objects,Industrial"
    assert ad[0]["Category"] == "4" and ad[1]["Category"] == "10"            # Drinks, Industry
    assert dt[0]["Category 1"] == "185" and dt[0]["Editorial"] == "1"       # Editorial -> Commercial first
    assert read("pond5")[0]["city"] == "Tokyo" and read("pond5")[0]["country"] == "Japan"
    assert read("123rf")[0]["country"] == "Japan"
    assert ad[0]["Title"] == "Protein Shakes on a Store Shelf for Health-Conscious Consumers"
    # the same minute again: a second set, nothing replaced
    again = sf.write_site_csvs(str(tmp_path), rows, ["adobe"], day="2026-11-09")
    assert os.path.basename(again[0]) == "adobe_2026-11-09 (2).csv"
    # the AI's own choice is used first
    c = sf.pick_categories("Protein Shakes on a Shelf", "", ["protein"], False, ["Food and drink"], 7)
    assert c["shutterstock"][0] == "Food and drink" and c["adobe"] == 7


def test_export_csv_dialog_makes_one_per_ticked_site(window, make_photos, monkeypatch, tmp_path):
    import upload_window as uw
    from test_window import add
    app, win = window
    add(win, make_photos(2))
    for ph in win.photos:
        ph.title, ph.keywords = "Christmas Lights Display on a Store Shelf", ["christmas", "lights", "holiday"]
    out = tmp_path / "csvs"

    def fake_exec(self):
        for k, cb in self.boxes.items():
            cb.setChecked(k in ("shutterstock", "dreamstime"))
        self.where.setText(str(out))
        self._accept()
        return True
    monkeypatch.setattr(uw.SiteCsvDialog, "exec", fake_exec)
    win.export_csv()
    names = sorted(os.listdir(out))
    assert len(names) == 2 and names[0].startswith("dreamstime_") and names[1].startswith("shutterstock_")
    assert win.settings["csv_sites"] == ["shutterstock", "dreamstime"]
    line = win.review._categories_line(win.photos[0])
    assert "Shutterstock: Holidays" in line and "Dreamstime: Christmas" in line


def test_csv_names_have_the_time_and_what_is_left_to_do():
    import re
    import site_files as sf
    import tagger_core as core
    assert re.fullmatch(r"shutterstock_\d{4}-\d\d-\d\d_\d\d-\d\d\.csv", core.csv_file_name("shutterstock"))
    # Adobe: plain, no commas, no dateline, 70 characters at most, cut at a word
    t = core.adobe_title("Tokyo, Japan - November 9, 2026: Protein Shakes, Bars and Powders on a Supermarket Shelf "
                         "for Health-Conscious Shoppers", "Protein Shakes, Bars and Powders on a Supermarket Shelf for "
                         "Health-Conscious Shoppers")
    assert "," not in t and "Tokyo" not in t and len(t) <= 70 and t.startswith("Protein Shakes Bars")
    assert not t.endswith(" ")
    assert core.parse_location("Austin, TX") == {"city": "Austin", "state": "TX", "country": "United States"}
    assert core.parse_location("Tokyo, Japan")["country"] == "Japan"
    assert core.parse_location("Tbilisi, Georgia")["country"] == "Georgia"
    rows = [{"file": "a.jpg", "title": "Woman Shopping for Groceries", "keywords": ["woman", "shopping"]},
            {"file": "b.jpg", "title": "Dog in a Park", "keywords": ["dog"]},
            {"file": "c.jpg", "title": "Store Shelf", "keywords": ["shelf"], "editorial": True, "country": "Japan"},
            {"file": "d.jpg", "title": "Crowd at a Parade", "keywords": ["crowd"], "editorial": True}]
    assert [sf.release_reason(r) for r in rows] == ["people", "property", "", ""]   # editorial needs none
    w = " | ".join(sf.csv_warnings(rows, ["adobe", "pond5"]))
    assert "Model release: 1 photo" in w and "a.jpg" in w and "Property release" in w
    assert "Adobe Stock: 2 editorial photos" in w and "Illustrative Editorial" in w
    assert "Country: 3 photos" in w
    assert "Adobe Stock:" not in " ".join(sf.csv_warnings(rows, ["shutterstock"]))
