"""Fixes from a review of real Shutterstock CSVs: instructions handed back as keywords, hashtag keywords, phone and
stock chart words on shelf photos, and categories (core only, no window)."""
import tagger_core as core



# ---------- the AI's instructions handed back as keywords
def test_words_from_the_instructions_are_junk():
    for k in ("keywords", "keywords backup1", "title", "plus", "json format", "single json object", "all lowercase",
              "no duplicates", "no guessing", "following all rules", "original keywords", "product name"):
        assert core.junk_keyword(k), k
    for k in ("plush toy", "titleist", "unique gift", "product display", "keyword research"):
        assert not core.junk_keyword(k), k


def test_hashtag_keywords_with_the_spaces_taken_out_go():
    kws = ["equinox gold", "smartphone", "stock market", "financial display", "digital branding", "mobile technology",
           "modern finance", "equinoxgold", "financialdisplay", "digitalbranding", "mobiletechnology", "modernfinance"]
    r = core.fix_common_problems("Equinox Gold Logo on a Smartphone Screen with Stock Market Chart", "", kws)
    assert r["keywords"] == kws[:7]


def test_real_compound_words_are_kept():
    kws = ["gingerbread", "ginger", "bread", "watermelon", "water", "melon", "healthcare", "health care",
           "metamucil", "meta mucil", "peppermint"]
    r = core.fix_common_problems("Gingerbread Cookie Mix on a Store Shelf", "", kws)
    assert r["keywords"] == kws


def test_phone_and_stock_chart_keywords_need_a_phone_or_chart_in_the_photo():
    kws = ["grand theft auto", "vinyl record", "smartphone display", "financial chart", "stock market"]
    r = core.fix_common_problems("Grand Theft Auto the Album Vinyl Record at Target", "", kws)
    assert r["keywords"] == ["grand theft auto", "vinyl record"]
    r = core.fix_common_problems("Cerebras Logo on a Smartphone Screen with a Stock Market Chart", "",
                                 ["cerebras", "smartphone display", "financial chart", "stock market"])
    assert r["keywords"] == ["cerebras", "smartphone display", "financial chart", "stock market"]


# ---------- categories
def test_food_and_drink_only_for_food():
    assert core.fit_categories(["Objects", "Food and drink"], "Zuru Fuggler Toys on a Store Shelf",
                               keywords=["zuru", "toys", "plush"]) == ["Objects"]
    assert core.fit_categories(["Objects", "Food and drink"], "Kit Kat Bits Chocolate Candy on a Shelf") == \
        ["Objects", "Food and drink"]


def test_cat_food_is_animals_not_food_and_drink():
    assert core.fit_categories(["Objects", "Food and drink"], "Fancy Feast Canned Cat Food on a Grocery Shelf") == \
        ["Animals/Wildlife", "Objects"]


def test_medicine_gets_healthcare_when_food_and_drink_goes():
    assert core.fit_categories(["Objects", "Food and drink"], "Metamucil Fiber Capsules on Store Shelves",
                               keywords=["fiber", "capsules"]) == ["Healthcare/Medical", "Objects"]


def test_a_chart_background_is_not_a_texture():
    assert core.fit_categories(["Technology", "Backgrounds/Textures"],
                               "Fastly Logo on a Smartphone with a Stock Market Chart Background") == ["Technology"]
    assert core.fit_categories(["Backgrounds/Textures"], "Weathered Wood Texture with Copy Space") == \
        ["Backgrounds/Textures"]


# ---------- is the editorial location still right?
def test_a_location_is_asked_about_for_new_days_only():
    rec = core.confirm_location("NYC, New York", {}, ["2026-11-18"])
    assert rec == {"location": "NYC, New York", "dates": ["2026-11-18"]}
    assert core.unconfirmed_dates("NYC, New York", rec, ["2026-11-18"]) == []
    assert core.unconfirmed_dates("NYC,  new york", rec, ["2026-11-20", "2026-11-18", "2026-11-19"]) == \
        ["2026-11-19", "2026-11-20"]
    # a location just typed is meant for these photos: nothing to ask, and it starts its own record
    assert core.unconfirmed_dates("Charlotte, North Carolina", rec, ["2026-11-20"]) == []
    assert core.confirm_location("Charlotte, North Carolina", rec, ["2026-11-20"])["dates"] == ["2026-11-20"]
    # no location, no dates: nothing to ask
    assert core.unconfirmed_dates("", rec, ["2026-11-20"]) == []
    assert core.unconfirmed_dates("NYC, New York", rec, ["", "not a date"]) == []
    # used again: the new days are added
    assert core.confirm_location("NYC, New York", rec, ["2026-11-20"])["dates"] == ["2026-11-18", "2026-11-20"]


def test_dates_are_written_for_people():
    assert core.describe_dates(["2026-11-20"]) == "November 20, 2026"
    assert core.describe_dates(["2026-11-20", "2026-11-19"]) == "November 19 and 20, 2026"
    assert core.describe_dates(["2026-11-18", "2026-11-19", "2026-11-20"]) == "November 18 to 20, 2026"
    assert core.describe_dates(["2026-10-30", "2026-11-02"]) == "October 30, 2026 to November 2, 2026"
    assert core.describe_dates([]) == ""


def test_the_default_photo_type_goes_back_to_automatic():
    s = {"default_type": "logo_stock"}
    assert core.reset_default_type(s) == "logo_stock" and s["default_type"] == "auto"
    assert core.reset_default_type(s) == ""
