"""Dreamstime's stricter text checks (Check Before Upload) and their fixes, on real titles and descriptions."""
import precheck
import tagger_core as core

PLACE = "Charlotte, North Carolina - October 12, 2026: "


def test_long_title_is_cut_back_to_its_main_phrase():
    t = PLACE + ("Hungry-Man Homestyle Meatloaf Fold's of Honor with Mixed Vegetables, Gravy, Mashed Potatoes and a "
                 "Brownie on Grocery Store Shelf for Quick and Easy Meals, Family Dinner Options")
    short = core.shorten_title(t)
    assert short.startswith(PLACE) and len(short) <= core.DT_TITLE_LONG
    assert not short.endswith(("Gravy", ",", "with"))
    ok = PLACE + "Lever 2000 Laundry Detergent on Grocery Shelf"
    assert core.shorten_title(ok) == ok


def test_descriptions_that_stop_half_way():
    cut = {
        "A retail display at a Walgreens photo kiosk showcases various personalized photo gift options, including "
        "prints, calendars.": "A retail display at a Walgreens photo kiosk showcases various personalized photo "
                              "gift options",
        "Boxes of Ivory deodorant bars arranged on a grocery store shelf alongside Coast and.":
            "Boxes of Ivory deodorant bars arranged on a grocery store shelf",
        "Cereal boxes on shelves within a brightly lit grocery store aisle stocked.":
            "Cereal boxes on shelves within a brightly lit grocery store aisle",
    }
    for before, after in cut.items():
        assert core._drop_unfinished_tail(before) == after
    for whole in ("A box of Dolly Parton's Fried Chicken Mac and Cheese.", "A shelf with cookies, cakes and pies.",
                  "A display of masks, including Scream, Ghostface, and more.",
                  "Bottles of soda on a shelf, with price tags."):
        assert core._drop_unfinished_tail(whole) == whole.rstrip(".")


def test_new_descriptions_are_not_cut_in_a_list():
    long = ("A retail display at a Walgreens photo kiosk showcases various personalized photo gift options for "
            "shoppers in the store, including prints, calendars, mugs, blankets, phone cases and posters for every "
            "occasion of the year.")
    out = core.clean_description(long, max_len=150)
    assert out.endswith("for shoppers in the store.")


def test_capitals_and_keyword_repeats_are_fixed():
    t = PLACE + "Coast THE EYE OPENER Deodorant Soap in the USA"
    kws = ["hungry man", "hungryman", "cookie", "cookies", "hershey's", "hersheys", "frozen meal", "bbq"]
    whats = {i["what"] for i in core.dreamstime_checks(t, "A bar of soap on a shelf.", kws)}
    assert {"Capital letters", "Keyword repeats"} <= whats
    r = core.dreamstime_fix(t, "A bar of soap on a shelf.", kws)
    assert "Coast The Eye Opener Deodorant Soap in the USA" in r["title"]
    assert r["keywords"] == ["hungry man", "cookie", "hershey's", "frozen meal", "bbq"]


def test_good_text_passes_and_only_dreamstime_gets_these():
    p = {"file": "a.jpg", "title": PLACE + "Lever 2000 Laundry Detergent on Grocery Shelf",
         "description": PLACE + "A close-up shot captures a shelf display of Lever 2000 soap bars.",
         "keywords": ["lever 2000", "soap", "bar soap", "grocery store", "shelf", "hygiene", "retail", "bathroom"],
         "editorial": True, "unsaved": False, "width": 6000, "height": 4000, "bytes": 5_000_000, "raw": False,
         "site_problems": []}
    assert not [i for i in precheck.check_photo(p, ["dreamstime"]) if "Dreamstime" in i["sites"]]
    bad = dict(p, title="Soap")
    assert any(i["what"] == "Title too short" and i["level"] == precheck.SEVERE
               for i in precheck.check_photo(bad, ["dreamstime"]))
    assert not any(i["what"] == "Title too short" for i in precheck.check_photo(bad, ["shutterstock"]))
    assert core.site_problems("Soap on a shelf", ["soap"] * 5, ["dreamstime"])          # under 7 keywords


def test_only_the_dreamstime_csv_gets_the_fixes(tmp_path):
    import csv
    import site_files
    title = PLACE + ("Werther's Original Candy Display on Grocery Store Shelf, Halloween Seasonal Promotion for Sweet "
                     "Treat Consumers and FAMILIES")
    rows = [{"file": "a.jpg", "folder": str(tmp_path), "title": title,
             "description": PLACE + "A skeleton on a shelf with Werther's Original candy, including caramels, toffees.",
             "keywords": ["werthers", "werther's", "candy", "halloween", "skeleton", "shelf", "store", "caramel"],
             "editorial": True}]
    dt, ss = site_files.write_site_csvs(str(tmp_path), rows, ["dreamstime", "shutterstock"])
    d = next(csv.DictReader(open(dt, encoding="utf-8")))
    # no place and date in Dreamstime's title, still editorial, and the description keeps them
    assert d["Image Name"] == ("Werther's Original Candy Display on Grocery Store Shelf, Halloween Seasonal Promotion "
                               "for Sweet Treat Consumers and Families")
    assert d["Editorial"] == "1" and d["Description"].startswith(PLACE)
    assert d["Description"].endswith("Werther's Original candy.")
    assert d["keywords"].split(",")[:2] == ["werthers", "candy"]
    s = next(csv.DictReader(open(ss, encoding="utf-8")))
    assert title in s.values()                                    # Shutterstock keeps the full title
    assert "werther's" in next(v for v in s.values() if "candy" in v)
    full = site_files.write_site_csvs(str(tmp_path), rows, ["dreamstime"], dt_tidy=False)[0]
    kept = next(csv.DictReader(open(full, encoding="utf-8")))["Image Name"]     # untidied: place kept, cut to 130
    assert kept.startswith(PLACE) and len(kept) <= 130


def test_check_before_upload_leaves_csv_fixes_out():
    p = {"file": "a.jpg", "title": PLACE + "Coast THE EYE OPENER Soap on a Shelf", "description": "Soap on a shelf.",
         "keywords": ["soap", "soaps", "shelf", "bar", "store", "hygiene", "bath", "clean"], "editorial": True,
         "unsaved": False, "width": 6000, "height": 4000, "bytes": 5_000_000, "raw": False, "site_problems": []}
    assert not any(i["what"] in ("Capital letters", "Keyword repeats") for i in precheck.check_photo(p, ["dreamstime"]))
    shown = {i["what"] for i in precheck.check_photo(dict(p, dt_tidy=False), ["dreamstime"])}
    assert {"Capital letters", "Keyword repeats"} <= shown


def test_dreamstime_titles_never_go_over_130():
    for t in ["Red " * 60, PLACE + "Amazingly Detailed Close Up View of a Bright Red Vintage Sports Car Parked Along "
              "the Seaside Promenade During a Warm Summer Evening Sunset Glow and the Crowd",
              "A" * 300]:
        out = core.dreamstime_title(t)
        assert 0 < len(out) <= 130 and not out.endswith((" and", " the", ","))
    assert core.dreamstime_title(PLACE + "Lever 2000 Soap on a Shelf") == "Lever 2000 Soap on a Shelf"
    long = PLACE + "Big Red Barn " * 12
    assert any(i["what"] == "Long title" for i in core.dreamstime_checks(long, "A barn.", ["barn"] * 8))
