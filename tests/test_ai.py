"""Detect and Reword, with a stand-in for the AI: what it's told, and how its answers are cleaned up."""
import tagger_core as core
from conftest import FakeAI, schema_fields

LONG = ("A picture of beautiful magazines on a wooden shelf for reading in a cozy home library with soft "
        "light, plants and a crane ornament")


def answer(body):
    f = schema_fields(body)
    text = body["messages"][-1]["content"]
    if text.startswith("Rewrite this stock photo title"):
        return {"title": "Magazines on a Wooden Shelf for Reading in a Cozy Home Library with Soft Light and Plants"}
    if {"title", "keywords"} <= f:
        return {"title": LONG, "keywords": ["magazines", "beautiful shelf", "crane", "library"]
                + [f"kw{i}" for i in range(35)]}
    if "keywords" in f:
        return {"keywords": ["reading"]}
    return {}


def detect(make_photos, **kw):
    ai = FakeAI(answer)
    kw.setdefault("revise", False)
    r = core.identify(make_photos(1)[0], "http://ollama", "model", 30, 40, opener=ai, **kw)
    return r, ai


def test_title_comes_back_direct_with_chosen_capitals(make_photos):
    r, _ = detect(make_photos, title_caps="every")
    assert r["title"].startswith("Beautiful Magazines On A Wooden Shelf")
    r, _ = detect(make_photos, title_caps="title", direct_titles=False)
    assert r["title"].startswith("A Picture of")


def test_never_use_and_avoid(make_photos):
    r, ai = detect(make_photos, never_use=["beautiful"], photo_avoid="crane, don't mention the weather")
    assert "beautiful" not in r["title"].lower() and "crane" not in r["title"].lower()
    assert len(r["title"].split()) >= 10                     # rewritten, not left with holes
    assert not [k for k in r["keywords"] if "beautiful" in k or "crane" in k]
    prompt = ai.prompts()[0]
    assert "Never use these words" in prompt and "AVOID for this photo" in prompt and "weather" in prompt


def test_every_detect_choices_reach_the_ai(make_photos):
    _, ai = detect(make_photos, style=core.style_rules(["factual", "subject"]))
    assert "standing preferences" in ai.prompts()[0]
    _, ai = detect(make_photos)
    assert "standing preferences" not in ai.prompts()[0]


def test_subject_first_rule_is_in_the_prompt(make_photos):
    _, ai = detect(make_photos)
    assert "Start the title directly with the main subject" in ai.prompts()[0]


def test_label_pass_is_one_extra_look(make_photos):
    _, without = detect(make_photos, read_label=False)
    _, with_label = detect(make_photos, read_label=True)
    assert with_label.looks() == without.looks() + 1


def test_reword_uses_the_same_title_rules():
    ai = FakeAI(lambda body: {"title": "a picture of magazines on a shelf for reading",
                              "keywords": ["magazines", "shelf"]})
    r = core.reword("http://ollama", "model", "Old Title", "", ["old"], {"title", "keywords"}, opener=ai,
                    title_caps="every", style=core.style_rules(["factual"]))
    assert r["title"] == "Magazines On A Shelf For Reading"
    assert "standing preferences" in ai.prompts()[0]
