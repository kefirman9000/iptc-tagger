"""Keywords and batch checks: never-use words, find and replace, editorial keywords, near-identical titles."""
import tagger_core as core


def test_never_use_removes_whole_words_only():
    kws = ["beautiful", "beautiful sunset", "beautify", "lake", "stunning view", "crane", "red-crowned crane"]
    assert core.drop_never_keywords(kws, ["beautiful", "stunning", "crane"]) == ["beautify", "lake"]


def test_word_lists_and_avoid():
    assert core.parse_word_list("beautiful, Stunning\n competitor co ; beautiful") == \
        ["beautiful", "Stunning", "competitor co"]
    assert core.avoid_terms("crane, sunset, don't mention the weather please") == ["crane", "sunset"]


def test_find_and_replace_in_keywords():
    assert core.find_replace_keywords(["cocacola", "Cocacola Zero", "soda"], "cocacola", "Coca-Cola") == \
        ["coca-cola", "Coca-Cola Zero", "soda"]
    assert core.find_replace_keywords(["stock", "stock photo", "soda"], "stock", "") == ["photo", "soda"]


def test_editorial_keywords_go_where_they_fit():
    extra = core.editorial_keywords("Tokyo, Japan", "2026-10-26")
    assert extra == ["tokyo", "japan", "october 2026", "2026"]
    out = core.place_editorial_keywords(["temple", "gate", "night", "shrine"], extra, title="Temple Gate at Night")
    assert out.index("tokyo") > out.index("night") and out[-2:] == ["october 2026", "2026"]


def test_near_identical_titles_are_grouped():
    titles = ["Red Barn in a Snowy Field with Wooden Fence and Bare Trees",
              "Red Barn in Snowy Fields with a Wooden Fence and Bare Trees",
              "Old Red Barn in Snowy Field with Wooden Fence, Bare Trees",
              "Red Barn in a Green Summer Field with Wildflowers and Blue Sky",
              "Doritos Cool Ranch Tortilla Chips on a Grocery Store Shelf",
              "Doritos Nacho Cheese Tortilla Chips on a Grocery Store Shelf",
              "Sunset", "Sunset"]
    assert core.similar_title_groups(titles) == [[0, 1, 2]]


def test_every_detect_checklist():
    keys = ["mood", "factual", "not-a-choice", "words"]
    assert core.style_keys(keys) == ["factual", "words", "mood"]
    assert core.style_summary(keys) == "Factual · Signs · Mood"
    rules = core.style_rules(keys)
    assert rules.count("\n- ") == 2 and "factually" in rules
    assert core.style_rules([]) == ""
