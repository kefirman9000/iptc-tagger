"""The AI's usual mistakes, fixed without asking it again (core.fix_common_problems), and the check before a
Shutterstock CSV. Every example here is a real row from a Shutterstock CSV the app made."""
import csv
import os

import tagger_core as core
from test_window import add, qapp, window  # noqa: F401  (the real window, offscreen)


# ---------- titles: an answer that ran on, quotes that don't pair up
def test_run_on_answer_is_cut_out_of_the_title():
    assert core.clean_title("Sheetz Fuel Pump Display Showing Octane Grades at Gilbarco Equipment”, “Keywords") \
        == "Sheetz Fuel Pump Display Showing Octane Grades at Gilbarco Equipment"
    assert core.clean_title("Welch’s Mixed Fruit Snacks on Grocery Shelf”, “Keywords") \
        == "Welch’s Mixed Fruit Snacks on Grocery Shelf"


def test_a_quote_that_does_not_pair_up_is_taken_out():
    assert core.clean_title("StoveTop Stuffing Mix for Chicken with Real Broth”, Six Boxes") \
        == "StoveTop Stuffing Mix for Chicken with Real Broth, Six Boxes"


def test_quotes_that_pair_up_are_kept():
    assert core.clean_title("Sign Saying “Fresh Bread” Above the Counter") \
        == "Sign Saying “Fresh Bread” Above the Counter"


# ---------- keywords that are no use to a buyer
def test_prices_codes_and_package_numbers_are_junk():
    for k in ("13 79", "11 99", "save 30", "cs1301", "n794 1x", "l2 0933", "l2 0933 date code", "best before date",
              "150 calories", "90 pouches", "145 pancakes", "2 in minutes", "2 in 2026", "87"):
        assert core.junk_keyword(k), k


def test_real_keywords_with_numbers_are_kept():
    for k in ("2026", "november 2026", "18 pack", "4 month supply", "60v max", "ms 162 chainsaw", "f150", "a350",
              "iphone 15", "rtx 4090", "93 octane", "5 percent minoxidil", "up to 135 mph"):
        assert not core.junk_keyword(k), k


# ---------- stores
def test_another_stores_name_is_taken_out_of_the_title_only():
    r = core.fix_common_problems(
        "BJ's Wholesale Club Interior Featuring Costco Baking Nuts Sweeteners Display", "",
        ["bjs wholesale club", "costco", "kirkland baking needs", "krogerpay", "nuts", "sweeteners"])
    assert "Costco" not in r["title"] and r["title"].startswith("BJ's Wholesale Club Interior")
    # related stores and brands are fine as keywords
    assert r["keywords"] == ["bjs wholesale club", "costco", "kirkland baking needs", "krogerpay", "nuts",
                             "sweeteners"]


def test_a_store_named_in_your_notes_is_never_taken_out():
    r = core.fix_common_problems("BJ's Display of Kirkland Almonds", "", ["kirkland", "almonds"],
                                 known="Kirkland almonds sold at BJ's")
    assert "Kirkland" in r["title"] and "kirkland" in r["keywords"]


def test_no_grocery_at_a_hardware_store():
    r = core.fix_common_problems("Clark Kensington Paint Plus Primer on Grocery Store Shelf", "",
                                 ["clark kensington paint", "grocery store shelf", "paint"])
    assert r["title"] == "Clark Kensington Paint Plus Primer on Store Shelf"
    assert r["keywords"] == ["clark kensington paint", "paint"]


# ---------- keywords that aren't true or aren't in the photo
def test_no_frozen_for_canned_food():
    r = core.fix_common_problems("Dinty Moore Beef Stew Cans on Grocery Store Shelf", "",
                                 ["dinty moore", "beef stew", "frozen meal", "frozen foods", "canned goods"])
    assert r["keywords"] == ["dinty moore", "beef stew", "canned goods"]


def test_frozen_food_keeps_frozen():
    r = core.fix_common_problems("Mrs Budds Chicken Pies 4 Pack in the Freezer Section", "",
                                 ["chicken pies", "frozen food"])
    assert "frozen food" in r["keywords"]


def test_store_photo_extras_go_unless_they_are_in_the_photo():
    r = core.fix_common_problems("Walmart Produce Section Featuring Potatoes on Display", "",
                                 ["potatoes", "shopping cart", "supply chain", "cold storage", "customer shopping"])
    assert r["keywords"] == ["potatoes"]
    r = core.fix_common_problems("Shopper Pushing a Shopping Cart Past Potatoes", "", ["potatoes", "shopping cart"])
    assert "shopping cart" in r["keywords"]


# ---------- categories
def test_categories_that_do_not_fit_are_replaced():
    fit = core.fit_categories
    assert fit(["Food and drink", "People"], "Mrs Budds Chicken Pies 4 Pack on Grocery Shelf") \
        == ["Food and drink", "Objects"]
    assert fit(["Food and drink", "Celebrities"], "Hormel Chili with Beans on Grocery Shelf") \
        == ["Food and drink", "Objects"]
    assert fit(["Food and drink", "Parks/Outdoor"], "Bisquick Pancake Mix Boxes on Grocery Store Shelf") \
        == ["Food and drink", "Objects"]
    assert fit(["Holidays", "Nature"], "Red Snowflake Buckets Stacked on Store Shelf") == ["Objects"]
    # paint labelled Interior Exterior isn't a room or a building
    assert fit(["Interiors", "Buildings/Landmarks"], "Benjamin Moore Interior Exterior Paint Jars on Store Shelf") \
        == ["Objects"]


def test_categories_that_fit_are_kept():
    fit = core.fit_categories
    assert fit(["Transportation", "Nature"], "Gray Vehicle Covered in Autumn Maple Leaves, Raindrops on Windshield") \
        == ["Transportation", "Nature"]
    assert fit(["Buildings/Landmarks", "Business/Finance"], "Ace Hardware Storefront on a Rainy Day") \
        == ["Buildings/Landmarks", "Business/Finance"]
    assert fit(["Healthcare/Medical", "Beauty/Fashion"], "Rogaine 5 Percent Minoxidil Hair Regrowth Treatment") \
        == ["Healthcare/Medical", "Beauty/Fashion"]


# ---------- the Shutterstock CSV
def test_shutterstock_csv_is_always_plain_characters(tmp_path):
    path = str(tmp_path / "s.csv")
    core.export_csv(path, [{"file": "a.jpg", "folder": "", "title": "BJ’s Wholesale Club Interior – Nuts Display",
                            "description": "", "keywords": ["bj’s", "nuts"], "categories": ["Food and drink"]}],
                    "shutterstock")
    row = next(csv.DictReader(open(path, encoding="utf-8")))
    assert row["Description"] == "BJ's Wholesale Club Interior - Nuts Display"
    assert row["Keywords"] == "bj's,nuts"


def test_check_before_csv_fixes_the_photos_first(window, make_photos):
    app, win = window
    add(win, make_photos(1))
    ph = win.photos[0]
    ph.title = "Welch’s Mixed Fruit Snacks on Grocery Shelf”, “Keywords"
    ph.keywords = ["welchs", "fruit snacks", "90 pouches", "shopping cart", "snacks", "candy", "grocery store",
                   "packaged food"]
    ph.categories = ["Food and drink", "Healthcare/Medical"]
    win._show(ph)
    path = win.make_shutterstock_csv(ask_categories=lambda n, t: ["Food and drink"])
    row = next(csv.DictReader(open(path, encoding="utf-8")))
    assert row["Description"] == "Welch's Mixed Fruit Snacks on Grocery Shelf"
    assert "90 pouches" not in row["Keywords"] and "shopping cart" not in row["Keywords"]
    assert row["Categories"] == "Food and drink,Objects"
    assert ph.title == "Welch’s Mixed Fruit Snacks on Grocery Shelf"           # the photo in the list is fixed too
    assert any(h["how"] == "Before fixes" for h in ph.history)                  # and the old text can be had back


# ---------- parent companies: in a title only when printed on the package or in your notes
def test_a_guessed_parent_company_comes_out_of_the_title():
    r = core.fix_common_problems("BelVita Crunchy Gingerbread Breakfast Boxes, Limited Edition Variety by General Mills",
                                 "", ["belvita", "general mills", "gingerbread"], check_parents=True)
    assert r["title"] == "BelVita Crunchy Gingerbread Breakfast Boxes, Limited Edition Variety"
    assert "general mills" in r["keywords"]                     # keywords may keep related brands
    assert core.drop_unproven_parents("Lays Classic Potato Chips, a PepsiCo Brand, on Store Shelf", " ") \
        == "Lays Classic Potato Chips on Store Shelf"


def test_a_printed_parent_company_stays_in_the_title():
    r = core.fix_common_problems("Lays Classic Potato Chips, a PepsiCo Brand, on Store Shelf", "", ["lays"],
                                 label="brand: Lay's; parent company: PepsiCo", check_parents=True)
    assert r["title"] == "Lays Classic Potato Chips, a PepsiCo Brand, on Store Shelf"


def test_a_brand_named_on_its_own_is_never_touched():
    for t in ("Hormel Chili with Beans on Grocery Shelf", "Kraft Macaroni and Cheese Boxes on Shelf",
              "Stand by the Window at Sunset"):
        assert core.drop_unproven_parents(t, " ") == t


# ---------- the location and date: given to the AI only with "Give to AI" ticked
def _detect_with_stand_in(win, ph, monkeypatch):
    """Run Detect on one photo with a stand-in for the AI; returns what the AI was given."""
    from test_window import wait
    seen = {}

    def fake_identify(path, url, model, kmin, kmax, context="", existing_title="", existing_keywords=None,
                      direction="", photo_note="", photo_type="", lead_keywords=None, title_prefix="",
                      date_in_title=False, revise=True, title_location="", copyright_free=False, **kw):
        seen.update(existing_title=existing_title, existing_keywords=list(existing_keywords or []),
                    lead_keywords=list(lead_keywords or []), title_location=title_location, style=kw.get("style", ""))
        return {"title": "Welch's Mixed Fruit Snacks on Store Shelf", "description": "",
                "keywords": ["welchs", "fruit snacks", "snacks", "candy", "packaged food", "treats", "boxes"],
                "categories": ["Food and drink"], "seconds": 0.1}
    monkeypatch.setattr(core, "identify", fake_identify)
    monkeypatch.setattr(core, "ai_ready", lambda url, opener=None: {"version": "1", "names": [win.settings["model"]]})
    win.keep_kw.setChecked(True)
    win.identify_photos([ph])
    wait(lambda: not ph.busy, what="detect")
    return seen


def _set_editorial(win, location_for_ai, lead=""):
    """Editorial on, Cary, the date before the title, set the way you would in the window."""
    from PySide6.QtCore import QDate
    win.editorial.setChecked(True)
    win.location.setText("Cary, North Carolina")
    win.date_pos.setCurrentIndex(win.date_pos.findData("before_title"))
    win.custom_date_on.setChecked(True)
    win.custom_date.setDate(QDate(2026, 11, 6))
    win.loc_ai.setChecked(location_for_ai)
    win.lead.setText(lead)
    win._remember_options()


def test_location_and_date_stay_away_from_the_ai_unless_given(window, make_photos, monkeypatch):
    app, win = window
    add(win, make_photos(1))
    ph = win.photos[0]
    _set_editorial(win, False, "myshoot, {month} {year}")
    # saved on the file earlier: a dateline and place / month keywords
    ph.title = "Cary, North Carolina - November 6, 2026: Welch's Fruit Snacks on Shelf"
    ph.keywords = ["welchs", "cary", "north carolina", "november 2026", "2026"]
    seen = _detect_with_stand_in(win, ph, monkeypatch)
    given = repr(seen).casefold()
    for word in ("cary", "north carolina", "november", "2026"):
        assert word not in given, (word, seen)
    assert seen["lead_keywords"] == ["myshoot"]                  # your keyword without the date token
    # and the app adds them itself afterwards
    assert ph.title.startswith("Cary, North Carolina - November 6, 2026: ")
    assert {"cary", "north carolina", "november 2026", "myshoot"} <= {k.casefold() for k in ph.keywords}


def test_location_and_date_go_to_the_ai_when_given(window, make_photos, monkeypatch):
    app, win = window
    add(win, make_photos(1))
    ph = win.photos[0]
    _set_editorial(win, True)
    seen = _detect_with_stand_in(win, ph, monkeypatch)
    assert seen["title_location"] == "Cary, North Carolina"
    assert ph.title.startswith("Cary, North Carolina - November 6, 2026: ")
