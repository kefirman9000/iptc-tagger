"""Each site's photo rules, adjusted copies, the shuffled order and Results (no window needed)."""
import hashlib
import os
import time

from PIL import Image, ImageCms

import site_results as sr
import upload_prep as prep
import uploader as up
from ftp_server import FTPTestServer


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def jpeg(path, size=(2400, 1600), **kw):
    Image.effect_noise(size, 50).convert("RGB").save(path, quality=92, **kw)
    return str(path)


def fake_xmp(title, keywords):
    lis = "".join(f"<rdf:li>{k}</rdf:li>" for k in keywords)
    x = (f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
         f'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title><rdf:Alt>'
         f'<rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title><dc:subject><rdf:Bag>{lis}'
         f'</rdf:Bag></dc:subject></rdf:Description></rdf:RDF></x:xmpmeta>')
    return b"http://ns.adobe.com/xap/1.0/\x00" + x.encode()


def test_rules_leave_out_small_photos_unless_send_anyway(tmp_path):
    small = jpeg(tmp_path / "small.jpg", (1200, 800))
    info = prep.photo_info(small)
    c = prep.check("shutterstock", info, {})
    assert not c["send"] and "too small" in c["refused"][0]
    assert prep.check("shutterstock", info, {"send_anyway": True})["send"]
    assert prep.check("pond5", info, {})["send"] is False          # short side under 1200 px


def test_alamy_progressive_and_adobe_rgb_get_copies_only_when_allowed(tmp_path):
    adobe_rgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()   # stand-in profile bytes
    p = jpeg(tmp_path / "prog.jpg", (3000, 2000), progressive=True)
    info = prep.photo_info(p)
    assert info["progressive"]
    c = prep.check("alamy", info, {"copies": False})
    assert c["send"] and not c["ops"] and "baseline" in c["warn"][0]
    c = prep.check("alamy", dict(info, width=3000, height=2000), {"copies": True})
    assert c["ops"] == {"baseline"}
    info2 = dict(info, color="Adobe RGB")
    assert prep.check("shutterstock", info2, {"copies": True})["ops"] == {"srgb"}
    assert not prep.check("pond5", info2, {"copies": True})["ops"]      # Pond5 takes Adobe RGB
    assert adobe_rgb


def test_copy_keeps_metadata_and_never_touches_the_original(tmp_path):
    src = str(tmp_path / "orig.jpg")
    Image.effect_noise((3000, 2000), 50).convert("RGB").save(src, quality=92, progressive=True,
                                                               exif=Image.Exif())
    # put an XMP packet in, as a real tagged photo has
    raw = open(src, "rb").read()
    seg = fake_xmp("Harbour at Dawn", ["harbour", "boats", "dawn"])
    raw = raw[:2] + b"\xff\xe1" + (len(seg) + 2).to_bytes(2, "big") + seg + raw[2:]
    open(src, "wb").write(raw)
    before = sha(src)
    dst = str(tmp_path / "copy" / "orig.jpg")
    prep.make_copy(src, dst, {"baseline", "downscale"}, "alamy", 0.8)
    assert sha(src) == before
    info = prep.photo_info(dst)
    assert not info["progressive"] and info["width"] == 2400
    assert sr.read_words(dst) == ("Harbour at Dawn", ["harbour", "boats", "dawn"])


def test_resize_never_goes_below_a_sites_minimum(tmp_path):
    info = {"width": 6000, "height": 4000, "bytes": 10_000_000, "color": "sRGB"}
    c = prep.check("123rf", info, {"copies": True, "resize_mode": "long_side", "resize_value": 2000})
    assert c["ops"] == {"downscale"} and 6000 * 4000 * c["scale"] ** 2 >= 6_000_000


def test_shuffle_keeps_look_alikes_apart():
    files = [f"a{i}.jpg" for i in range(4)] + [f"b{i}.jpg" for i in range(4)]
    looks = {f: ("0" * 16 if f.startswith("a") else "f" * 16) for f in files}
    for seed in range(20):
        order = prep.spread_order(files, looks, seed)
        assert sorted(order) == sorted(files)
        assert all(x[0] != y[0] for x, y in zip(order, order[1:]))       # never two of a series in a row


def test_uploader_sends_an_adjusted_copy_with_the_original_name(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "_keyring", lambda: None)
    monkeypatch.setattr(up, "stockup_dirs", lambda: [str(tmp_path / "StockUp")])
    folder = tmp_path / "shoot"
    folder.mkdir()
    big = jpeg(folder / "big.jpg", (3000, 2000), progressive=True)
    small = jpeg(folder / "tiny.jpg", (800, 600))
    root = tmp_path / "srv"
    root.mkdir()
    srv = FTPTestServer(str(root))
    try:
        site = dict(srv.site("alamy"), label="Alamy")
        u = up.Uploader([big, small], [site], opts={"copies": True, "shuffle": True})
        assert u.start()
        end = time.time() + 60
        while u.status()["running"] and time.time() < end:
            time.sleep(0.05)
        st = u.status()["sites"]["alamy"]
        assert st["done"] == 1 and st["refused"] == 1, st
        assert os.listdir(root) == ["big.jpg"]
        assert not prep.photo_info(str(root / "big.jpg"))["progressive"]       # the baseline copy went
        assert prep.photo_info(big)["progressive"]                              # your original didn't change
        assert up.sent_before("alamy", sha(big))                                # recorded by your own file
    finally:
        srv.close()


def test_results_import_matches_photos_you_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "stockup_dirs", lambda: [str(tmp_path / "StockUp")])
    p = jpeg(tmp_path / "IMG_1.jpg")
    raw = open(p, "rb").read()
    seg = fake_xmp("Red Barn", ["barn", "farm", "red"])
    open(p, "wb").write(raw[:2] + b"\xff\xe1" + (len(seg) + 2).to_bytes(2, "big") + seg + raw[2:])
    up._record("adobe", sha(p), p)
    rep = tmp_path / "adobe_stock_insights.csv"
    rep.write_text("Original Filename;Status;Rejection Reason;Earnings\nIMG_1.jpg;Approved;;$1,50\n"
                   "IMG_2.jpg;Rejected;Noise;0\n", encoding="utf-8")
    r = sr.import_report(str(rep))
    assert r["site"] == "adobe" and r["photos"] == 2 and r["matched"] == 1
    assert sr.import_report(str(rep))["already"]
    s = sr.summary(min_photos=1)["sites"][0]
    assert (s["accepted"], s["rejected"], s["earn"]) == (1, 1, 1.5)
    assert s["reasons"] == [("Noise", 1)]
    assert "Red Barn" in sr.summary_html()


def test_pond5_csv_titles_fit_80_characters(tmp_path):
    import csv
    import site_files
    long = ("Apex, North Carolina - November 11, 2026: Highlights Hidden Pictures Happy Holidays Special Edition "
            "Magazines on Store Shelf for Children's Entertainment and Puzzle Solving")
    rows = [{"file": "a.jpg", "title": long, "description": "Apex, North Carolina - November 11, 2026: A shelf.",
             "keywords": ["magazine"] * 1, "editorial": True, "city": "Apex", "region": "North Carolina",
             "country": "United States"}]
    path = site_files.write_site_csvs(str(tmp_path), rows, ["pond5"])[0]
    got = list(csv.reader(open(path, encoding="utf-8")))[1]
    assert got[1] == "Highlights Hidden Pictures Happy Holidays Special Edition Magazines"
    assert got[2].startswith("Apex, North Carolina - November 11, 2026") and got[7] == "yes"
