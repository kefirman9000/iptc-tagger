"""Titles that name a company say what it really is (companies.tsv), not a guess or vague filler."""
import tagger_core as core
from conftest import FakeAI, schema_fields


def test_companies_are_found_in_titles():
    names = lambda t: [c["name"] for c in core.find_companies(t)]
    assert names("AIG Logo Displayed on Smartphone Screen") == ["American International Group"]
    assert names("Akamai Logo on a Black Display") == ["Akamai Technologies"]
    assert names("AsteraLabs Logo on Smartphone Screen") == ["Astera Labs"]           # written as one word
    assert names("AOSmith Screen Display with Stock Charts") == ["A. O. Smith"]
    assert names("AlbeMarle Smartphone Display with Stock Charts") == ["Albemarle"]
    assert names("AMD Radeon Graphics Card on a Monitor with Stock Charts") == ["Advanced Micro Devices"]
    assert names("Target Store Sign") == ["Target"] and names("Caterpillar Excavator Logo") == ["Caterpillar"]
    # everyday words aren't companies without a sign of one
    for t in ("Caterpillar on a Green Leaf", "Target Audience Analysis", "Apple Pie on a Plate",
              "Stripe Pattern Wallpaper Screen", "Shell on a Beach", "Crane Standing in a Lake"):
        assert names(t) == [], t
    assert core.find_companies("Crane Standing in a Lake", note="it's a grey heron, not a crane") == []
    # a product on a shelf isn't about the company
    assert not core.about_the_company("Hormel Chili Cans on a Grocery Shelf")
    assert core.about_the_company("Hormel Foods Logo on a Smartphone")
    # your own lines win over the built-in list
    own = core.find_companies("Luna Skin Logo on a Phone", extra="Luna Skin, Luna = skincare brand")
    assert [(c["name"], c["what"]) for c in own] == [("Luna Skin", "skincare brand")]


def test_detect_says_what_the_company_is():
    sent = []

    def answer(body):
        text = body["messages"][-1]["content"]
        sent.append(text)
        if text.startswith("This stock photo title names a company"):
            return {"title": "American International Group, a Multinational Insurance Company, Logo on a Smartphone "
                             "Screen with a Stock Market Chart"}
        if {"title", "keywords"} <= schema_fields(body):
            return {"title": "AIG Logo on Smartphone Screen with Stock Market Chart for Financial Concepts",
                    "keywords": ["aig", "stock market", "smartphone", "finance"] + [f"k{i}" for i in range(30)]}
        return {}
    r = core.identify(_photo(), "http://ollama", "model", 30, 45, opener=FakeAI(answer), revise=False)
    assert r["title"].startswith("American International Group, a Multinational Insurance Company")
    asked = next(s for s in sent if s.startswith("This stock photo title names a company"))
    assert "American International Group (shown as AIG) is a multinational insurance company" in asked
    assert r["keywords"][:3] == ["aig", "american international group", "multinational insurance"]


def test_when_the_ai_cant_the_app_says_it_itself():
    def answer(body):
        text = body["messages"][-1]["content"]
        if text.startswith("This stock photo title names a company"):
            return {"title": "ADP Trading Platform Logo with Stock Chart"}          # still wrong
        if {"title", "keywords"} <= schema_fields(body):
            return {"title": "ADP Trading Platform Logo with Stock Chart",
                    "keywords": ["adp"] + [f"k{i}" for i in range(30)]}
        return {}
    r = core.identify(_photo(), "http://ollama", "model", 30, 45, opener=FakeAI(answer), revise=False)
    assert "ADP (Automatic Data Processing, Payroll and Human Resources Software Company)" in r["title"]
    # off: nothing changes
    r = core.identify(_photo(), "http://ollama", "model", 30, 45, opener=FakeAI(answer), revise=False,
                      companies=False)
    assert r["title"] == "ADP Trading Platform Logo with Stock Chart"


def test_reword_gets_the_company_facts():
    p = core.build_reword_prompt("Autodesk Logo on Smartphone with Stock Chart", "", [], ["title"])
    assert "Autodesk is a design and engineering software company" in p
    assert "Company facts" not in core.build_reword_prompt("Autodesk Logo on Smartphone", "", [], ["title"],
                                                           companies=False)
    # a product on a shelf: no company facts (the brand is just the brand)
    assert "Company facts" not in core.build_reword_prompt("Hormel Chili Cans on a Shelf", "", [], ["title"])


def _photo():
    import os
    import tempfile
    from PIL import Image
    path = os.path.join(tempfile.mkdtemp(), "logo.jpg")
    Image.new("RGB", (64, 48), (20, 20, 20)).save(path)
    return path
