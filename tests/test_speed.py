"""Detect: Fast (one look at the photo), text-first retries, and several photos at once."""
import tagger_core as core
from conftest import FakeAI, schema_fields

LONG = "Glass Jars of Honey on a Wooden Kitchen Shelf with Soft Morning Light and Dried Flowers"


def _answer(desc=True):
    def answer(body):
        f = schema_fields(body)
        if {"title", "keywords"} <= f:
            out = {"title": LONG, "keywords": [f"k{i}" for i in range(35)]}
            if "description" in f:
                out.update(description="Several glass jars of golden honey stand on a rustic wooden kitchen shelf.",
                           categories=["Food and drink"], adobe_category=7)
            return out
        if "description" in f:
            return {"description": "Glass jars of honey on a shelf beside dried flowers in a sunny kitchen.",
                    "categories": ["Food and drink"], "adobe_category": 7}
        if "brand" in f:
            return {"brand": "", "product": "", "text": ""}
        if "title" in f:
            return {"title": LONG}
        return {"keywords": []}
    return answer


def test_fast_is_one_look_at_the_photo(make_photos):
    photo = make_photos(1)[0]
    slow = FakeAI(_answer())
    r1 = core.identify(photo, "http://ollama", "model", 30, 45, opener=slow, read_label=True, description=True,
                       context="Honey from a farm market", revise=True)
    fast = FakeAI(_answer())
    r2 = core.identify(photo, "http://ollama", "model", 30, 45, opener=fast, read_label=True, description=True,
                       context="Honey from a farm market", revise=True, fast=True)
    assert len(fast.asked) == 1 and fast.looks() == 1              # title, keywords, description, categories
    assert len(slow.asked) >= 4 and slow.looks() >= 3               # label, draft, double-check, description
    assert r2["description"].startswith("Several glass jars") and r2["categories"][0] == "Food and drink"
    assert r2["adobe_category"] == 7 and r1["description"]
    assert "exact spellings" in fast.prompts()[0]                   # the label is read in the same request


def test_retries_try_the_text_model_first(make_photos):
    def answer(body):
        f, text = schema_fields(body), body["messages"][-1]["content"]
        if {"title", "keywords"} <= f:
            return {"title": "Honey Jars", "keywords": ["honey", "jar", "shelf"]}
        if "too short" in text:
            return {"title": LONG}
        if "MORE keywords" in text:
            return {"keywords": [f"extra{i}" for i in range(30)]}
        return {}
    ai = FakeAI(answer)
    r = core.identify(make_photos(1)[0], "http://ollama", "model", 30, 45, opener=ai, revise=False,
                      expand_short=True, text_model="small-text")
    assert r["title"] == LONG and len(r["keywords"]) >= 30
    retries = [b for b in ai.asked if "too short" in b["messages"][-1]["content"]
               or "MORE keywords" in b["messages"][-1]["content"]]
    assert retries and all(not b["messages"][-1].get("images") for b in retries)   # no second look needed
    assert all(b["model"] == "small-text" for b in retries)                        # the text model did them
