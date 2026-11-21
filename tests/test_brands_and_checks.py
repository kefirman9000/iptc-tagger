"""Brand spellings, keyword order, look-alike photos and Check Before Upload (the parts that need no window)."""
import tagger_core as core
import precheck


RULES = core.brand_rules(core.DEFAULT_BRAND_SPELLINGS)


def test_brands_are_spelled_your_way_everywhere():
    r = core.fix_brand_spellings("Bjs Wholesale Club Store with Haagen Dazs, Kelloggs and Wal-Mart Signs",
                                 "Ben and Jerrys ice cream at bj’s", ["bjs", "kelloggs", "ice cream"], RULES)
    assert r["title"] == "BJ's Wholesale Club Store with Häagen-Dazs, Kellogg's and Walmart Signs"
    assert r["description"] == "Ben & Jerry's ice cream at BJ's"
    # keywords keep their lowercase; the other ways buyers search come right after the brand
    assert r["keywords"] == ["bj's", "bjs", "kellogg's", "kelloggs", "ice cream"]
    assert any("Kelloggs → Kellogg's" in f for f in r["fixes"])


def test_brand_rules_leave_other_words_alone():
    r = core.fix_brand_spellings("Flowers and Lowest Prices at a Hebrew Market", "", ["flowers"], RULES)
    assert r["title"] == "Flowers and Lowest Prices at a Hebrew Market" and r["keywords"] == ["flowers"]
    assert not r["fixes"]
    # no room left: nothing is added past the maximum
    r = core.fix_brand_spellings("x", "", ["bj's", "a", "b"], RULES, keyword_max=3)
    assert r["keywords"] == ["bj's", "a", "b"]
    own = core.brand_rules("Acme Foods = Acme Food, AcmeFoods | acme")
    assert core.fix_brand_spellings("Acmefoods Crackers", "", ["acme food"], own)["keywords"] == ["acme foods", "acme"]


def test_the_first_ten_keywords_are_the_ones_that_matter():
    kws = ["food", "shelf", "retail", "aisle", "grocery", "snack", "healthy", "fitness", "nutrition", "price",
           "kirkland", "protein bar"]
    out = core.order_keywords(kws, "Kirkland Protein Bars on a Grocery Store Shelf")
    assert out[:4] == ["kirkland", "protein bar", "shelf", "grocery"] and out[-2:] == ["retail", "aisle"]
    assert "kirkland" in core.keyword_order_problem(kws, "Kirkland Protein Bars")
    assert core.keyword_order_problem(out, "Kirkland Protein Bars on a Grocery Store Shelf") == ""
    # your keywords to put first stay first; the month and year stay last
    out = core.order_keywords(["tokyo", "food", "kirkland", "november 2026"], "Kirkland Bars", lead=1,
                              keep_last=["november 2026"])
    assert out == ["tokyo", "kirkland", "food", "november 2026"]


def test_look_alike_groups():
    a, b, c = 0b1010 << 40, (0b1010 << 40) | 0b11, 0xFFFF_FFFF
    assert core.look_alike_groups({"a": a, "b": b, "c": c, "d": None}) == [["a", "b"]]
    grid = [x * 20 + y for y in range(8) for x in range(9)]
    assert core.image_hash(grid) == 0                 # brighter to the right everywhere: no bit set


def test_check_before_upload_finds_what_sites_reject():
    p = {"file": "a.jpg", "title": "Protein Bars on a Shelf", "keywords": ["protein"], "editorial": True,
         "unsaved": True, "width": 1600, "height": 1200, "bytes": 2_000_000, "look_alikes": ["b.jpg"],
         "order_note": "not in the first ten: kirkland", "brand_fixes": ["Kelloggs → Kellogg's"]}
    issues = precheck.check_photo(p, ["shutterstock", "adobe", "alamy"])
    what = {i["what"] for i in issues}
    assert {"Not saved", "Too small", "Editorial caption", "Look-alike", "Keyword order", "Brand spelling"} <= what
    small = [i for i in issues if i["what"] == "Too small"]                # one line per size rule, sites together
    assert [i["sites"] for i in small] == [["Shutterstock", "Adobe Stock"], ["Alamy"]]
    assert "Illustrative Editorial" in precheck.notes([p], ["adobe"])[0] and not precheck.notes([p], ["alamy"])
    ok = dict(p, title="Tokyo, Japan - November 9, 2026: Protein Bars on a Shelf", unsaved=False, width=4000,
              height=3000, look_alikes=[], order_note="", brand_fixes=[], editorial=False)
    assert precheck.check_photo(ok, ["shutterstock", "dreamstime"]) == []
    assert any(i["what"] == "Model release" for i in precheck.check_photo(
        dict(ok, title="Woman Shopping for Groceries"), ["shutterstock"]))
    big = precheck.check_photo(dict(ok, bytes=48 * precheck.MB), ["shutterstock", "adobe"])
    assert [i["sites"] for i in big if i["what"] == "File too big"] == [["Adobe Stock"]]


def test_fix_plan_for_site_rules():
    import tagger_core as core
    assert core.site_fix_plan("Red Apple", ["apple"] * 3, ["shutterstock", "adobe"]) == \
        {"kw_more": 7, "title_longer": True}                     # Shutterstock: 5+ words, 7+ keywords
    assert core.site_fix_plan("Red apple on a wooden table", [f"k{i}" for i in range(60)], ["shutterstock"]) == \
        {"kw_trim": 50}
    long_desc = "A red apple sits on a wooden table in warm morning light, " * 6
    assert core.site_fix_plan("Red apple on a wooden table", [f"k{i}" for i in range(20)], ["depositphotos"],
                              long_desc)["desc_cut"] == 250
    assert core.site_fix_plan("Red apple on a wooden table", [f"k{i}" for i in range(20)], ["shutterstock"]) == {}


def test_christmas_is_named_not_just_holiday():
    r = core.name_the_holiday("Holiday Decorations Display with Santa Claus Figurines and Red Ornaments",
                              "Festive holiday display in a store.", ["holiday", "decorations", "santa claus"])
    assert r["title"] == "Christmas Holiday Decorations Display with Santa Claus Figurines and Red Ornaments"
    assert r["description"] == "Festive Christmas holiday display in a store."
    assert "christmas" in r["keywords"] and "holiday" in r["keywords"]            # holiday stays as well
    # ornaments and wreaths together are Christmas; one of them alone isn't proof
    assert "Christmas" in core.name_the_holiday("Festive Ornaments and Wreaths", "", ["ornaments", "wreath"])["title"]
    assert core.name_the_holiday("Festive Glass Ornaments", "", ["ornaments"])["fixes"] == []
    # no sign of Christmas: "holiday" is left alone; your note is enough of a sign
    assert core.name_the_holiday("Holiday Lights on a House", "", ["lights"])["fixes"] == []
    assert core.name_the_holiday("Holiday Lights on a House", "", ["lights"],
                                 known="christmas display")["title"] == "Christmas Holiday Lights on a House"
    # already named, or two different holidays: nothing changes
    assert core.name_the_holiday("Christmas Tree Lot", "", ["christmas tree"])["fixes"] == []
    assert core.name_the_holiday("Holiday Candles", "", ["menorah", "christmas tree"])["fixes"] == []
    assert core.name_the_holiday("Holiday Candy", "", ["candy cane"])["title"] == "Christmas Holiday Candy"
    assert "Halloween" in core.name_the_holiday("Holiday Decor with Jack-O'-Lanterns", "", [])["title"]
    # the AI is told to name the holiday
    assert "Christmas" in core.HOLIDAY_RULE and core.HOLIDAY_RULE in core.build_prompt(7, 50)


def test_check_flags_your_own_keyword_minimum_and_title_length():
    import precheck
    base = {"file": "a.jpg", "title": "Benefiber Prebiotic Fiber Supplement on Store Shelf",
            "bare_title": "Benefiber Prebiotic Fiber Supplement on Store Shelf", "keywords": [f"k{i}" for i in range(9)],
            "keyword_min": 30, "title_words_min": 14}
    whats = {i["what"]: i for i in precheck.check_photo(base, ["shutterstock"])}
    assert whats["Few keywords"]["fixable"] and "9 keywords" in whats["Few keywords"]["detail"]
    assert whats["Short title"]["fixable"] and "7 words" in whats["Short title"]["detail"]
    ok = dict(base, keywords=[f"k{i}" for i in range(32)], bare_title=" ".join(["word"] * 15))
    assert not {"Few keywords", "Short title"} & {i["what"] for i in precheck.check_photo(ok, ["shutterstock"])}


# ---------- misspellings the AI read off the packaging (rows from real Shutterstock CSVs)
def test_a_misspelled_brand_keyword_is_kept_next_to_the_fixed_one():
    r = core.fix_brand_spellings("Meta Mucil Fiber Capsules on Store Shelves", "",
                                 ["fiber capsules", "meta mucil", "psyllium"], RULES, keep_misspelled=True)
    assert r["title"] == "Metamucil Fiber Capsules on Store Shelves"
    assert r["keywords"] == ["fiber capsules", "metamucil", "meta mucil", "psyllium"]
    # running it again changes nothing and reports nothing, so Check Before Upload doesn't keep flagging it
    again = core.fix_brand_spellings(r["title"], "", r["keywords"], RULES, keep_misspelled=True)
    assert again["keywords"] == r["keywords"] and not again["fixes"]
    # off: the misspelling is just fixed
    assert core.fix_brand_spellings("x", "", ["meta mucil"], RULES)["keywords"] == ["metamucil"]


def test_only_real_misspellings_are_kept_not_punctuation():
    r = core.fix_brand_spellings("x", "", ["campbells", "coca cola"], RULES, add_alternates=False,
                                 keep_misspelled=True)
    assert r["keywords"] == ["campbell's", "coca-cola"]


def test_the_misspellings_from_the_packaging_are_fixed():
    for wrong, right in (("Realemom Lemon Juice", "ReaLemon Lemon Juice"), ("Buttoni Ravioli", "Buitoni Ravioli"),
                         ("Berkeley Jensen Famotidine", "Berkley Jensen Famotidine"), ("Bon Am Cleanser", "Bon Ami Cleanser"),
                         ("Stove Stop Turkey Gravy", "Stove Top Turkey Gravy"), ("Jakts Mario Racer", "Jakks Mario Racer"),
                         ("Freoport McMoran Logo", "Freeport-McMoRan Logo"), ("Fruit of Loom Logo", "Fruit of the Loom Logo")):
        assert core.fix_brand_spellings(wrong, "", [], RULES)["title"] == right, wrong
    # Bon Ami isn't turned into Bon Amii
    assert core.fix_brand_spellings("Bon Ami Cleanser", "", [], RULES)["title"] == "Bon Ami Cleanser"


def test_shutterstock_csv_has_no_repeats_after_accents_are_taken_off(tmp_path):
    import csv
    path = tmp_path / "ss.csv"
    core.export_csv(str(path), [{"file": "a.jpg", "title": "Stouffer's Fettuccine", "keywords":
                                 ["nestlé", "nestle", "pasta", "pokémon", "pokemon"], "categories": ["Objects"]}])
    rows = list(csv.reader(open(path, encoding="utf-8")))
    assert rows[1][2] == "nestle,pasta,pokemon"
