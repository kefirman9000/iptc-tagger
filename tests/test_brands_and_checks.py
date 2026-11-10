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
