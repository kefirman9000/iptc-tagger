"""Titles: capitals, subject-first wording, never-use words, find and replace."""
import pytest

import tagger_core as core


@pytest.mark.parametrize("before, after", [
    ("A Picture of Magazines on Shelf for Reading", "Magazines on Shelf for Reading"),
    ("a photo of magazines on a shelf", "Magazines on a shelf"),
    ("An Image Showing Fresh Strawberries in a Bowl", "Fresh Strawberries in a Bowl"),
    ("This Is a Photo of a Red Barn in Autumn", "Red Barn in Autumn"),
    ("Stock Photo of Business People in a Meeting", "Business People in a Meeting"),
    ("A Beautiful Picture of Sunset over the Lake", "Sunset over the Lake"),
    ("A Stack of Colorful Magazines", "Stack of Colorful Magazines"),
    ("A Photo of Cats", "Cats"),
    ("A photo of an iPhone on a desk", "iPhone on a desk"),
    ("iPhone 15 on a Desk", "iPhone 15 on a Desk"),
    ("eBay Packages on a Porch", "eBay Packages on a Porch"),
])
def test_titles_start_with_the_subject(before, after):
    assert core.direct_title(before) == after


@pytest.mark.parametrize("keep", [
    "Aerial Photo of Tokyo Skyline at Night",       # tells you something: kept
    "Close-Up Photo of Coffee Beans",
    "The Hague City Hall Exterior",                 # "The" can be part of a name
    "Photo Studio with Softbox Lighting",           # "photo" is the subject here
    "Picture Frames on a Wall",
    "An Apple",                                     # too little would be left
])
def test_titles_that_are_already_direct_stay(keep):
    assert core.direct_title(keep) == keep


def test_title_case_and_every_word():
    t = "magazines on a shelf for reading"
    assert core.style_title(t, caps="title") == "Magazines on a Shelf for Reading"
    assert core.style_title(t, caps="every") == "Magazines On A Shelf For Reading"


def test_own_capitals_are_kept():
    assert core.style_title("iPhone 15 on a desk with NASA mug", caps="every") == "iPhone 15 On A Desk With NASA Mug"
    assert core.style_title("McDonald's drive-thru at night") == "McDonald's Drive-Thru at Night"


def test_directions_asking_for_sentence_case_win():
    assert core.style_title("A picture of magazines on a shelf", "Use sentence case for titles",
                            caps="every") == "Magazines on a shelf"


def test_wording_option_off_leaves_the_opening():
    assert core.style_title("A picture of magazines", direct=False).startswith("A Picture of")


@pytest.mark.parametrize("title, out", [
    ("Beautiful Sunset over Lake Biwa", "Sunset over Lake Biwa"),
    ("Heron Standing in Water, Beautiful, Japan", "Heron Standing in Water, Japan"),
    ("Morning at the Beautiful", "Morning"),
    ("Lake Beautiful in Autumn", "Lake in Autumn"),
])
def test_never_use_words_come_out_of_titles_cleanly(title, out):
    assert core.drop_never_from_title(title, ["beautiful", "stunning"]) == out


def test_find_and_replace_in_titles():
    assert core.find_replace("Cocacola Bottle on Cocacola Shelf", "cocacola", "Coca-Cola") == \
        "Coca-Cola Bottle on Coca-Cola Shelf"
    assert core.find_replace("Cat on Category Page", "cat", "Kitten") == "Kitten on Category Page"   # whole words
    assert core.find_replace("apple Apple", "Apple", "Pear", match_case=True) == "apple Pear"
    assert core.find_replace("Old Red Barn", "red", "") == "Old Barn"


def test_keyword_list_written_into_the_title_is_split_off():
    title, leaked = core.split_leaked_keywords('Car Care Products", "Keywords": "car wax", "polish')
    assert "Keywords" not in title and leaked


@pytest.mark.parametrize("before, after", [
    ("Salt & Pepper Shakers", "Salt and Pepper Shakers"),
    ("Café Ñandú in Zürich — Old Town", "Cafe Nandu in Zurich - Old Town"),
    ("Coca-Cola™ Bottle", "Coca-Cola Bottle"),
    ("“Best Deal” Sign at 50% Off", "Best Deal Sign at 50 percent Off"),
    ("Tokyo 東京 Skyline", "Tokyo Skyline"),
    ("Sunset 🌅 over Lake", "Sunset over Lake"),
    ("Smørrebrød in København", "Smorrebrod in Kobenhavn"),
    ("Ice at -5°C", "Ice at -5 degrees C"),
])
def test_stock_site_safe_text(before, after):
    assert core.plain_text(before) == after
    assert core.tidy_symbols(before, "safe") == after
    assert core.special_characters(after) == []


@pytest.mark.parametrize("keep", ["McDonald's Drive-Thru at Night", "Kyoto, Japan - October 26, 2026: Temple Gate",
                                  "24/7 Store, $5 Coffee", "Close-Up of a Well-Known NASA Logo (Night)"])
def test_stock_site_safe_leaves_plain_text_alone(keep):
    assert core.plain_text(keep) == keep and core.special_characters(keep) == []


def test_shutterstock_flags_special_characters():
    out = core.site_problems("Fish & Chips in a Café", ["fish", "chips"] * 4, ["shutterstock", "adobe"])
    assert any(o.startswith("Shutterstock:") and "special characters & é" in o for o in out)
    assert not any(o.startswith("Adobe Stock:") and "special" in o for o in out)


@pytest.mark.parametrize("title, found", [
    ("1965 Ford Mustang 289 V8 with 4 Speed and 15 Inch Wheels", "numbers"),
    ("Coffee Cup with Coffee Beans and a Coffee Grinder on Table", "more than once"),
    ("Red, Blue, Green, Yellow, Orange Balloons", "list of keywords"),
])
def test_checks_find_problems(title, found):
    tp, _ = core.text_problems(title, "", core.DEFAULT_TEXT_CHECKS)
    assert len(tp) == 1 and found in tp[0]


@pytest.mark.parametrize("good", ["iPhone 15 Pro on a Desk", "Doritos Cool Ranch Tortilla Chips, a Frito-Lay Brand",
                                  "Magazines on a Wooden Shelf for Reading in a Cozy Home Library"])
def test_checks_leave_good_titles_alone(good):
    assert core.text_problems(good, "", core.DEFAULT_TEXT_CHECKS) == ([], [])


def test_check_for_a_description_that_copies_the_title_and_for_length():
    _, dp = core.text_problems("Magazines on a Wooden Shelf", "Magazines on a wooden shelf.", ["desc_copy"])
    assert dp
    tp, _ = core.text_problems("Magazines on a Wooden Shelf for Reading in a Cozy Home Library", "", ["fit"],
                               {"title_max": 40})
    assert "at most 40 characters" in tp[0]
    assert core.text_problems("Magazines on a Shelf", "", ["fit"], {"title_max": 40}) == ([], [])
