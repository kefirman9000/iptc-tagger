"""The location and date in editorial titles: building them, finding them, and swapping the location."""
import pytest

import tagger_core as core

DATE = "October 26, 2026"


@pytest.mark.parametrize("pos, sep, loc_sep", [
    ("before_title", "dash", "dash"), ("start", "colon", "dash"), ("end", "dash", "dash"),
    ("end", "paren", "dash"), ("before_title", "colon", "comma"), ("before_title", "dash", "space")])
@pytest.mark.parametrize("prefix", ["", "Editorial Use Only:"])
def test_any_old_location_is_found_and_removed(pos, sep, loc_sep, prefix):
    full = core.compose_title("Temple Gate, Old Town at Night", prefix, DATE, pos, sep, "Kyoto, Japan", loc_sep)
    assert core.dateline_location(full, prefix, DATE) == "Kyoto, Japan"
    assert core.strip_title_extras(full, prefix, DATE, "", any_location=True) == "Temple Gate, Old Town at Night"


def test_composing_twice_changes_nothing():
    once = core.compose_title("Deer in the Park", "", DATE, "before_title", "colon", "Nara, Japan", "dash")
    assert core.compose_title(once, "", DATE, "before_title", "colon", "Nara, Japan", "dash") == once


def test_dash_inside_the_title_is_not_taken_for_the_location():
    t = "Pier - Old Town - Kyoto, Japan - October 26, 2026"
    assert core.dateline_location(t, "", DATE) == "Kyoto, Japan"
    assert core.strip_title_extras(t, "", DATE, "", any_location=True) == "Pier - Old Town"


def test_no_location_and_hyphenated_places():
    assert core.dateline_location("October 26, 2026: Temple Gate", "", DATE) == ""
    assert core.dateline_location("Saint-Tropez, France - October 26, 2026: Harbour", "", DATE) == \
        "Saint-Tropez, France"


def test_old_location_swapped_for_a_new_one():
    old = "Osaka, Japan - October 26, 2026: Castle Moat in Autumn"
    bare = core.strip_title_extras(old, "", DATE, "Kyoto, Japan", any_location=True)
    assert core.compose_title(bare, "", DATE, "before_title", "colon", "Kyoto, Japan", "dash") == \
        "Kyoto, Japan - October 26, 2026: Castle Moat in Autumn"
