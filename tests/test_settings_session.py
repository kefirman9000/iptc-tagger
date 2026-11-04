"""Settings survive a restart (and bad values are fixed), and the list is kept safe after a crash."""
import tagger_core as core


def test_settings_round_trip_and_bad_values():
    core.save_settings({**core.DEFAULT_SETTINGS, "detect_style": ["factual", "nope"], "title_caps": "weird",
                        "profiles": {"Grocery": {"context": "x"}, "": {}, "bad": "x"}})
    s = core.load_settings()
    assert s["detect_style"] == ["factual"]
    assert s["title_caps"] == "title"
    assert list(s["profiles"]) == ["Grocery"]


def test_new_options_have_sensible_defaults():
    s = core.load_settings()
    assert s["direct_titles"] is True and s["title_caps"] == "title" and s["detect_style"] == []
    assert s["stockup_after_save"] is False and s["avoid_on"] is False
    assert s["text_checks"] == core.DEFAULT_TEXT_CHECKS and "fit" not in s["text_checks"]


def test_checks_setting_is_cleaned():
    core.save_settings({**core.DEFAULT_SETTINGS, "text_checks": ["numbers", "made-up"]})
    assert core.load_settings()["text_checks"] == ["numbers"]


def test_list_kept_after_a_crash(make_photos):
    a, b = make_photos(2)
    core.save_session([{"path": a, "title": "Unsaved Title", "keywords": ["k"], "note": "heron",
                        "location": "Kyoto, Japan"},
                       {"path": b}])
    back = {e["path"]: e for e in core.load_session()}
    assert back[a]["title"] == "Unsaved Title" and back[a]["location"] == "Kyoto, Japan"
    import os
    os.remove(b)                                               # deleted meanwhile: left out
    assert [e["path"] for e in core.load_session()] == [a]
    core.clear_session()
    assert core.load_session() == []


def test_changelog_has_notes_for_this_version():
    import os
    text = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "CHANGELOG.md"), encoding="utf-8").read()
    notes = dict(core.changelog_sections(text))
    assert notes.get(core.APP_VERSION, "").strip(), f"CHANGELOG.md has no section for {core.APP_VERSION}"
