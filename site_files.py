"""Each stock site's CSV, with categories picked for every photo. The category tables and the picker come from
StockUp (github.com/kefirman9000/stockup): Shutterstock, Adobe Stock and Dreamstime categories are chosen from a
photo's title, description and keywords; the AI's own Shutterstock choice is used first when there is one."""
import csv
import os
import re

import tagger_core as core

# (words that point to this subject, Shutterstock category, Adobe category,
#  Dreamstime category, Dreamstime editorial category when the photo is editorial)
ADOBE_CATEGORIES = {
    1: "Animals", 2: "Buildings and Architecture", 3: "Business", 4: "Drinks",
    5: "The Environment", 6: "States of Mind", 7: "Food",
    8: "Graphic Resources", 9: "Hobbies and Leisure", 10: "Industry",
    11: "Landscapes", 12: "Lifestyle", 13: "People", 14: "Plants and Flowers",
    15: "Culture and Religion", 16: "Science", 17: "Social Issues",
    18: "Sports", 19: "Technology", 20: "Transport", 21: "Travel",
}

# Dreamstime categories and codes, from Dreamstime's Image_spreadsheet_template.xls
DREAMSTIME_CATEGORIES = [
    ("Abstract -> Aerial", 211), ("Abstract -> Backgrounds", 112), ("Abstract -> Blurs", 39),
    ("Abstract -> Colors", 164), ("Abstract -> Competition", 40),
    ("Abstract -> Craftsmanship", 41), ("Abstract -> Danger", 42),
    ("Abstract -> Exploration", 43), ("Abstract -> Fun", 158), ("Abstract -> Help", 44),
    ("Abstract -> Love", 149), ("Abstract -> Luxury", 45), ("Abstract -> Mobile", 187),
    ("Abstract -> Peace", 46), ("Abstract -> Planetarium", 165), ("Abstract -> Power", 47),
    ("Abstract -> Purity", 48), ("Abstract -> Religion", 128),
    ("Abstract -> Seasonal & Holiday", 155), ("Abstract -> Security", 49),
    ("Abstract -> Sports", 50), ("Abstract -> Stress", 51), ("Abstract -> Success", 52),
    ("Abstract -> Teamwork", 53), ("Abstract -> Textures", 141), ("Abstract -> Unique", 54),
    ("Animals -> Birds", 31), ("Animals -> Farm", 33), ("Animals -> Insects", 36),
    ("Animals -> Mammals", 32), ("Animals -> Marine life", 34), ("Animals -> Pets", 30),
    ("Animals -> Reptiles & Amphibians", 35), ("Animals -> Rodents", 37),
    ("Animals -> Wildlife", 168), ("Arts & Architecture -> Details", 124),
    ("Arts & Architecture -> Generic architecture", 71),
    ("Arts & Architecture -> Historic buildings", 132), ("Arts & Architecture -> Home", 153),
    ("Arts & Architecture -> Indoor", 73), ("Arts & Architecture -> Landmarks", 70),
    ("Arts & Architecture -> Modern buildings", 131),
    ("Arts & Architecture -> Night scenes", 130), ("Arts & Architecture -> Outdoor", 72),
    ("Arts & Architecture -> Ruins & Ancient", 174),
    ("Arts & Architecture -> Work places", 154), ("Business -> Communications", 79),
    ("Business -> Computers", 78), ("Business -> Finance", 80), ("Business -> Industries", 77),
    ("Business -> Metaphors", 83), ("Business -> Objects", 84), ("Business -> People", 75),
    ("Business -> Still-life", 81), ("Business -> Teams", 76),
    ("Business -> Transportation", 82), ("Business -> Travel", 85),
    ("Editorial -> Celebrities", 178), ("Editorial -> Commercial", 185),
    ("Editorial -> Events", 179), ("Editorial -> Landmarks", 184),
    ("Editorial -> People", 180), ("Editorial -> Politics", 181), ("Editorial -> Sports", 182),
    ("Editorial -> Weather & Environment", 183), ("Holidays -> Chinese New Year", 204),
    ("Holidays -> Christmas", 190), ("Holidays -> Cinco de Mayo", 207),
    ("Holidays -> Diwali", 203), ("Holidays -> Easter", 193), ("Holidays -> Fathers Day", 196),
    ("Holidays -> Halloween", 192), ("Holidays -> Hanukkah", 208),
    ("Holidays -> Mardi Gras", 206), ("Holidays -> Mothers Day", 195),
    ("Holidays -> New Years", 189), ("Holidays -> Other", 202), ("Holidays -> Ramadan", 205),
    ("Holidays -> Thanksgiving", 191), ("Holidays -> Valentines Day", 194),
    ("IT & C -> Artificial Intelligence", 210), ("IT & C -> Connectivity", 110),
    ("IT & C -> Equipment", 113), ("IT & C -> Internet", 111), ("IT & C -> Networking", 109),
    ("Illustrations & Clipart -> AI generated", 212),
    ("Illustrations & Clipart -> 3D & Computer generated", 166),
    ("Illustrations & Clipart -> Hand drawn & Artistic", 167),
    ("Illustrations & Clipart -> Illustrations", 163),
    ("Illustrations & Clipart -> Vector", 186), ("Industries -> Agriculture", 101),
    ("Industries -> Architecture", 89), ("Industries -> Banking", 87),
    ("Industries -> Cargo & Shipping", 93), ("Industries -> Communications", 94),
    ("Industries -> Computers", 91), ("Industries -> Construction", 90),
    ("Industries -> Education", 150), ("Industries -> Entertainment", 136),
    ("Industries -> Environment", 99), ("Industries -> Food & Beverages", 127),
    ("Industries -> Healthcare & Medical", 92), ("Industries -> Insurance", 96),
    ("Industries -> Legal", 95), ("Industries -> Manufacturing", 100),
    ("Industries -> Military", 102), ("Industries -> Oil and gas", 161),
    ("Industries -> Power and energy", 97), ("Industries -> Sports", 157),
    ("Industries -> Transportation", 98), ("Industries -> Travel", 88),
    ("Nature -> Clouds and skies", 22), ("Nature -> Deserts", 17), ("Nature -> Details", 14),
    ("Nature -> Fields & Meadows", 27), ("Nature -> Flowers & Gardens", 25),
    ("Nature -> Food ingredients", 28), ("Nature -> Forests", 18),
    ("Nature -> Fruits & Vegetables", 137), ("Nature -> Generic vegetation", 11),
    ("Nature -> Geologic and mineral", 143), ("Nature -> Lakes and rivers", 16),
    ("Nature -> Landscapes", 146), ("Nature -> Mountains", 15),
    ("Nature -> Plants and trees", 12), ("Nature -> Sea & Ocean", 19),
    ("Nature -> Seasons specific", 26), ("Nature -> Sunsets & Sunrises", 23),
    ("Nature -> Tropical", 20), ("Nature -> Water", 171), ("Nature -> Waterfalls", 24),
    ("Objects -> Clothing & Accessories", 142), ("Objects -> Electronics", 147),
    ("Objects -> Home related", 138), ("Objects -> Isolated", 135),
    ("Objects -> Music and sound", 151), ("Objects -> Other", 145), ("Objects -> Retro", 152),
    ("Objects -> Sports", 156), ("Objects -> Still life", 144), ("Objects -> Tools", 140),
    ("Objects -> Toys", 134), ("People -> Active", 123), ("People -> Body parts", 139),
    ("People -> Children", 119), ("People -> Cosmetic & Makeup", 175),
    ("People -> Couples", 115), ("People -> Diversity", 122), ("People -> Expressions", 159),
    ("People -> Families", 118), ("People -> Men", 117), ("People -> Nudes", 173),
    ("People -> Portraits", 162), ("People -> Seniors", 121), ("People -> Teens", 120),
    ("People -> Women", 116), ("People -> Workers", 160), ("Technology -> Computers", 105),
    ("Technology -> Connections", 106), ("Technology -> Electronics", 129),
    ("Technology -> Other", 148), ("Technology -> Retro", 107), ("Technology -> Science", 209),
    ("Technology -> Telecommunications", 104), ("Travel -> Africa", 56),
    ("Travel -> America", 58), ("Travel -> Antarctica", 176),
    ("Travel -> Arts & Architecture", 65), ("Travel -> Asia", 57),
    ("Travel -> Australasian", 60), ("Travel -> Cruise", 62), ("Travel -> Cuisine", 63),
    ("Travel -> Currencies", 67), ("Travel -> Destination scenics", 61),
    ("Travel -> Europe", 59), ("Travel -> Flags", 68), ("Travel -> Resorts", 64),
    ("Travel -> Tropical", 66), ("Web Design Graphics -> Banners", 201),
    ("Web Design Graphics -> Buttons", 200),
    ("Web Design Graphics -> Web Backgrounds & Textures", 199),
    ("Web Design Graphics -> Web Icons", 198),
]
DREAMSTIME_CODES = {c for _, c in DREAMSTIME_CATEGORIES}


TOPICS = [
    ("sunset sunrise dusk dawn twilight golden-hour blue-hour afterglow", "Nature", 11, 23, 183),
    ("sky cloud cloudy storm thunderstorm lightning rainbow overcast", "Nature", 11, 22, 183),
    ("mountain peak alpine alps hill hillside valley summit ridge volcano", "Nature", 11, 15, 183),
    ("sea ocean beach coast coastal wave surf shore shoreline bay harbor harbour cliff seaside "
     "lighthouse", "Nature", 11, 19, 183),
    ("lake river pond stream creek reflection lagoon", "Nature", 11, 16, 183),
    ("waterfall cascade rapids", "Nature", 11, 24, 183),
    ("forest woods woodland jungle rainforest pine oak", "Nature", 11, 18, 183),
    ("desert dune sand canyon arid", "Nature", 11, 17, 183),
    ("field meadow grass grassland prairie countryside vineyard wheat pasture rural farmland",
     "Nature", 11, 27, 183),
    ("landscape scenic scenery panorama panoramic vista nature outdoor wilderness", "Nature", 11, 146, 183),
    ("flower flowers bloom blossom garden rose tulip petal bouquet daisy sunflower lavender",
     "Nature", 14, 25, 183),
    ("plant leaf leaves fern moss succulent cactus tree foliage branch", "Nature", 14, 12, 183),
    ("snow winter autumn fall-foliage frost frozen ice icy spring summer seasonal", "Nature", 11, 26, 183),
    ("tropical palm island paradise exotic", "Nature", 21, 20, 183),
    ("water drop droplet splash ripple", "Nature", 5, 171, 183),
    ("night stars star milky-way astronomy moon galaxy astrophotography", "Nature", 11, 130, 183),
    ("aerial drone birds-eye top-view overhead", "Nature", 11, 211, 183),
    ("bird birds eagle owl gull seagull duck parrot heron hawk swan pigeon robin", "Animals/Wildlife", 1, 31, 179),
    ("dog puppy cat kitten pet pets", "Animals/Wildlife", 1, 30, 179),
    ("cow horse sheep goat pig chicken cattle lamb donkey", "Animals/Wildlife", 1, 33, 179),
    ("wildlife deer bear fox wolf lion elephant tiger zebra giraffe monkey squirrel rabbit safari",
     "Animals/Wildlife", 1, 168, 179),
    ("fish whale dolphin shark coral jellyfish seal octopus underwater", "Animals/Wildlife", 1, 34, 179),
    ("insect butterfly bee ant dragonfly spider beetle ladybug moth", "Animals/Wildlife", 1, 36, 179),
    ("snake lizard frog toad crocodile turtle tortoise reptile", "Animals/Wildlife", 1, 35, 179),
    ("food meal dish dinner lunch breakfast cooking cuisine recipe bread cake dessert pizza pasta "
     "salad soup meat cheese baking restaurant", "Food and drink", 7, 127, 185),
    ("fruit vegetable vegetables apple tomato berry berries orange lemon carrot produce banana grape",
     "Food and drink", 7, 137, 185),
    ("drink coffee tea wine beer cocktail juice beverage espresso", "Food and drink", 4, 127, 185),
    ("building skyscraper architecture tower modern facade office-building", "Buildings/Landmarks", 2, 131, 184),
    ("castle church cathedral temple historic palace monastery medieval old-town chapel mosque",
     "Buildings/Landmarks", 2, 132, 184),
    ("ruins ruin ancient archaeology", "Buildings/Landmarks", 2, 174, 184),
    ("landmark monument bridge statue famous memorial", "Buildings/Landmarks", 21, 70, 184),
    ("city street urban downtown skyline cityscape town village square alley", "Buildings/Landmarks", 21, 72, 179),
    ("interior room kitchen living-room bedroom bathroom apartment furniture indoor cozy",
     "Interiors", 2, 73, 185),
    ("home house cottage cabin real-estate", "Buildings/Landmarks", 2, 153, 185),
    ("travel tourism tourist vacation trip destination journey sightseeing", "Parks/Outdoor", 21, 61, 179),
    ("business office meeting corporate entrepreneur startup teamwork workplace coworker",
     "Business/Finance", 3, 75, 185),
    ("money finance bank banking coin cash investment dollar currency economy savings",
     "Business/Finance", 3, 80, 185),
    ("computer laptop code coding programming software keyboard", "Technology", 19, 105, 185),
    ("internet network data cloud-computing online cyber", "Technology", 19, 111, 185),
    ("smartphone phone mobile tablet electronics gadget device", "Technology", 19, 129, 185),
    ("artificial-intelligence ai robot robotics automation", "Technology", 19, 210, 185),
    ("science laboratory lab research microscope chemistry experiment scientist", "Science", 16, 209, 185),
    ("health medical doctor hospital nurse medicine pill healthcare clinic patient", "Healthcare/Medical",
     16, 92, 185),
    ("woman women girl female lady", "People", 13, 116, 180),
    ("man men boy male guy", "People", 13, 117, 180),
    ("child children kid kids baby toddler", "People", 13, 119, 180),
    ("family parents mother father", "People", 13, 118, 180),
    ("couple romance romantic love", "People", 6, 115, 180),
    ("senior elderly grandmother grandfather retired", "People", 13, 121, 180),
    ("teen teenager", "People", 13, 120, 180),
    ("portrait face people person crowd", "People", 13, 162, 180),
    ("happy smile smiling joy sad stress anxiety emotion lonely fear excited", "People", 6, 159, 180),
    ("fashion beauty makeup cosmetic style model dress clothing outfit", "Beauty/Fashion", 12, 175, 185),
    ("sport sports football soccer basketball tennis baseball golf hockey athlete stadium match",
     "Sports/Recreation", 18, 157, 182),
    ("running runner marathon race cycling cyclist swimming gym fitness yoga workout ski skiing "
     "surfing climbing", "Sports/Recreation", 18, 123, 182),
    ("hiking hiker camping park picnic kayak canoe trail fishing adventure", "Parks/Outdoor", 9, 123, 179),
    ("car boat ship sailboat yacht train plane airplane airport bus truck bicycle bike vehicle road "
     "highway traffic transport transportation ferry", "Transportation", 20, 98, 179),
    ("industry factory industrial warehouse manufacturing machinery steel", "Industrial", 10, 100, 185),
    ("construction crane building-site excavator worker", "Industrial", 10, 90, 185),
    ("energy solar wind-turbine power-plant electricity renewable", "Industrial", 10, 97, 183),
    ("oil gas refinery pipeline", "Industrial", 10, 161, 185),
    ("cargo shipping container port freight logistics", "Industrial", 10, 93, 185),
    ("agriculture farming harvest tractor crop farmer", "Industrial", 10, 101, 185),
    ("environment pollution climate recycling ecology sustainability conservation", "Nature", 5, 99, 183),
    ("education school student classroom study learning teacher university book", "Education", 12, 150, 185),
    ("religion religious prayer faith spiritual buddha worship", "Religion", 15, 128, 179),
    ("christmas xmas santa", "Holidays", 15, 190, 179),
    ("halloween pumpkin", "Holidays", 15, 192, 179),
    ("easter", "Holidays", 15, 193, 179),
    ("thanksgiving", "Holidays", 15, 191, 179),
    ("valentine valentines", "Holidays", 6, 194, 179),
    ("new-year fireworks", "Holidays", 15, 189, 179),
    ("holiday celebration festival parade carnival fourth-of-july independence-day", "Holidays", 15, 202, 179),
    ("protest demonstration rally homeless poverty refugee activism", "People", 17, 181, 181),
    ("abstract", "Abstract", 8, 112, 185),
    ("background backdrop wallpaper copy-space", "Backgrounds/Textures", 8, 112, 185),
    ("texture pattern grunge surface", "Backgrounds/Textures", 8, 141, 185),
    ("bokeh blur blurred defocused", "Backgrounds/Textures", 8, 39, 185),
    ("art painting sculpture artwork mural graffiti artist gallery museum", "Arts", 15, 124, 185),
    ("music guitar concert piano musician instrument band", "Arts", 9, 151, 179),
    ("isolated white-background cutout", "Objects", 8, 135, 185),
    ("tool tools hammer wrench", "Objects", 10, 140, 185),
    ("toy toys", "Objects", 9, 134, 185),
    ("still-life object objects product", "Objects", 12, 144, 185),
    ("vintage retro antique nostalgic old-fashioned", "Vintage", 12, 152, 185),
    ("sign symbol icon signage arrow", "Signs/Symbols", 8, 145, 185),
    ("flag flags", "Signs/Symbols", 15, 68, 179),
]

_TOPIC_WORDS = []
for _i, (_words, *_rest) in enumerate(TOPICS):
    for _w in _words.split():
        _TOPIC_WORDS.append((_w.replace("-", " "), _i))
_SINGLE = {}
_PHRASES = []
for _w, _i in _TOPIC_WORDS:
    if " " in _w:
        _PHRASES.append((_w, _i))
    else:
        _SINGLE.setdefault(_w, set()).add(_i)


def _forms(tok):
    yield tok
    if len(tok) > 3 and tok.endswith("ies"):
        yield tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("es"):
        yield tok[:-2]
    if len(tok) > 3 and tok.endswith("s"):
        yield tok[:-1]


def suggest_categories(title, keywords, editorial=False, description=""):
    """Best guesses for each site's categories, and the words they came from."""
    scores, why = {}, {}

    def score(text, weight):
        low = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
        hit = set()
        for phrase, i in _PHRASES:
            if f" {phrase} " in low:
                hit.add((i, phrase))
        for tok in low.split():
            for form in _forms(tok):
                if form in _SINGLE:
                    for i in _SINGLE[form]:
                        hit.add((i, form))
                    break
        for i, word in hit:
            scores[i] = scores.get(i, 0) + weight
            why.setdefault(i, [])
            if word not in why[i]:
                why[i].append(word)

    score(title or "", 3)
    if description and description != title:
        score(description, 1)
    for n, k in enumerate(keywords):
        score(k, 2 if n < 10 else 1)
    if not scores:
        return {"ss": [], "adobe": None, "dt": [], "why": []}

    def best(index, limit, share):
        per = {}
        for i, sc in scores.items():
            per.setdefault(TOPICS[i][index], []).append(sc)
        # the strongest subject counts in full, related ones a little: a photo of a puppy in
        # autumn leaves is about the puppy, even though several nature words appear
        tally = {c: max(v) + 0.3 * (sum(v) - max(v)) for c, v in per.items() if c is not None}
        ranked = sorted(tally.items(), key=lambda kv: -kv[1])
        top = ranked[0][1] if ranked else 0
        return [c for c, sc in ranked[:limit] if sc >= max(2, top * share)]

    ss = best(1, 2, 0.5)
    adobe = (best(2, 1, 0) or [None])[0]
    dt = best(3, 3, 0.4)
    if editorial:
        ed = best(4, 1, 0)
        if ed and ed[0] not in dt:
            dt = (ed + dt)[:3]
    top_topics = sorted(scores, key=lambda i: -scores[i])[:4]
    words = [w for i in top_topics for w in why[i]][:6]
    return {"ss": ss, "adobe": adobe, "dt": dt, "why": words}


# ---------- every site's categories for one photo -------------------------------------------------------------
# From the Shutterstock category to Adobe Stock's (number) and Dreamstime's (codes)
SS_TO_ADOBE = {
    "Abstract": 8, "Animals/Wildlife": 1, "Arts": 9, "Backgrounds/Textures": 8, "Beauty/Fashion": 12,
    "Buildings/Landmarks": 2, "Business/Finance": 3, "Celebrities": 13, "Education": 12, "Food and drink": 7,
    "Healthcare/Medical": 12, "Holidays": 15, "Industrial": 10, "Interiors": 2, "Miscellaneous": 3,
    "Nature": 11, "Objects": 3, "Parks/Outdoor": 11, "People": 13, "Religion": 15, "Science": 16,
    "Signs/Symbols": 8, "Sports/Recreation": 18, "Technology": 19, "Transportation": 20, "Vintage": 9,
}
SS_TO_DREAMSTIME = {
    "Abstract": 164, "Animals/Wildlife": 168, "Arts": 167, "Backgrounds/Textures": 112, "Beauty/Fashion": 142,
    "Buildings/Landmarks": 71, "Business/Finance": 80, "Celebrities": 178, "Education": 150,
    "Food and drink": 127, "Healthcare/Medical": 92, "Holidays": 202, "Industrial": 100, "Interiors": 73,
    "Miscellaneous": 145, "Nature": 146, "Objects": 145, "Parks/Outdoor": 72, "People": 162, "Religion": 128,
    "Science": 209, "Signs/Symbols": 145, "Sports/Recreation": 157, "Technology": 148, "Transportation": 98,
    "Vintage": 152,
}
DREAMSTIME_HOLIDAYS = [("christmas", 190), ("halloween", 192), ("thanksgiving", 191), ("easter", 193),
                       ("valentine", 194), ("hanukkah", 208), ("new year", 189), ("mother s day", 195),
                       ("father s day", 196), ("diwali", 203), ("ramadan", 205), ("chinese new year", 204)]
DRINK_WORDS = ("drink", "drinks", "beverage", "beverages", "shake", "shakes", "soda", "coffee", "tea", "beer", "wine",
               "juice", "smoothie", "water bottle", "bottled water", "milk", "energy drink", "cocktail", "liquor")
TOOL_WORDS = ("tool", "tools", "hammer", "wrench", "drill", "saw", "chainsaw", "shovel", "rake", "screwdriver")
FOOD_WORDS = ("food", "foods", "snack", "snacks", "cereal", "protein", "candy", "chips", "cookies", "crackers", "stew",
              "chili", "soup", "pie", "pies", "pancake", "caramels", "chocolate", "fruit", "vegetables", "meat",
              "bread", "cheese", "grocery", "groceries", "nuts", "baking", "sauce", "pasta", "rice", "stuffing")
HARDWARE_WORDS = ("paint", "primer", "hardware", "tool", "tools", "lumber", "building supplies", "diy",
                  "home improvement", "garden tools", "chainsaw", "blower", "fertilizer", "plumbing", "screws")
MEDICAL_WORDS = ("medicine", "medical", "pharmacy", "minoxidil", "treatment", "vitamin", "vitamins", "supplement",
                 "supplements", "pain relief", "allergy", "pharmaceutical", "over the counter", "first aid")
PRODUCT_WORDS = ("shelf", "shelves", "display", "package", "packaging", "product", "products", "store", "retail",
                 "box", "boxes", "cans", "bottles", "pack", "aisle")
DT_NAMES = dict((code, name) for name, code in DREAMSTIME_CATEGORIES)


def _words(*texts) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", " ".join(t or "" for t in texts).casefold()) + " "


def pick_categories(title: str, description: str = "", keywords=(), editorial: bool = False, shutterstock=(),
                    adobe=None) -> dict:
    """Each site's categories for one photo: {"shutterstock": [names], "adobe": number, "dreamstime": [codes]}.
    shutterstock / adobe: the AI's own choice, used first. Otherwise StockUp's picker guesses from the words;
    either way, categories the photo gives no sign of are taken out (People with nobody in it, Buildings for
    paint cans)."""
    kws = list(keywords or [])
    guess = suggest_categories(title or "", kws, editorial=editorial, description=description or "")
    text = _words(title, description, " ".join(kws[:12]))
    first = core.shutterstock_categories(list(shutterstock or []))
    if not first:                                  # no AI choice: a product on a shelf goes by what it is
        product = any(f" {w} " in text for w in PRODUCT_WORDS)
        if product and any(f" {w} " in text for w in FOOD_WORDS + DRINK_WORDS):
            first = ["Food and drink", "Objects"]
        elif product and any(f" {w} " in text for w in HARDWARE_WORDS):
            first = ["Objects", "Industrial"]
        elif product and any(f" {w} " in text for w in MEDICAL_WORDS):
            first = ["Healthcare/Medical", "Beauty/Fashion" if any(f" {w} " in text for w in (
                "hair", "skin", "skincare", "beauty", "cosmetic", "cosmetics")) else "Objects"]
    ss = core.fit_categories(first or guess["ss"], title, description, kws)
    a = core.adobe_category(adobe) if adobe is not None else None
    if a is None:
        main = ss[0] if ss else ""
        if main == "Food and drink" and any(f" {w} " in text for w in DRINK_WORDS):
            a = 4
        elif main == "Objects" and "Industrial" in ss:
            a = 10
        else:
            a = SS_TO_ADOBE.get(main) or guess["adobe"] or 3
    dt = []
    product = any(f" {w} " in text for w in PRODUCT_WORDS)
    if editorial:                                  # Dreamstime's editorial section first
        dt.append(185 if product else next((c for c in guess["dt"] if 178 <= c <= 185), 179))
    for c in ss:
        code = SS_TO_DREAMSTIME.get(c)
        if c == "Holidays":
            code = next((h for word, h in DREAMSTIME_HOLIDAYS if f" {word} " in text), 202)
        if c == "Objects" and any(f" {w} " in text for w in TOOL_WORDS):
            code = 140
        if code and code not in dt:
            dt.append(code)
    for c in guess["dt"]:                          # the picker's own ideas fill a spare place
        if len(dt) >= 3:
            break
        if c not in dt and not (178 <= c <= 185) and DT_NAMES.get(c, "").split(" -> ")[0] in (
                "Industries", "Objects", "Nature", "Holidays", "Travel", "Business", "Technology"):
            dt.append(c)
    return {"shutterstock": ss, "adobe": a, "dreamstime": dt[:3]}


def describe_categories(cats: dict) -> str:
    """One line for the side panel: 'Shutterstock: Food and drink, Objects · Adobe: Drinks · Dreamstime: …'."""
    parts = []
    if cats.get("shutterstock"):
        parts.append("Shutterstock: " + ", ".join(cats["shutterstock"]))
    if cats.get("adobe"):
        parts.append("Adobe: " + ADOBE_CATEGORIES.get(cats["adobe"], str(cats["adobe"])))
    if cats.get("dreamstime"):
        parts.append("Dreamstime: " + ", ".join(DT_NAMES.get(c, str(c)).split(" -> ")[-1] for c in cats["dreamstime"]))
    return " · ".join(parts)


# ---------- each site's CSV (columns from StockUp, which follows each site's own sample file) ------------------
SITE_CSVS = [   # (key, label, what it's for)
    ("shutterstock", "Shutterstock", "titles, keywords, categories and the editorial flag"),
    ("adobe", "Adobe Stock", "titles (70 characters, no location or date), keywords and the category"),
    ("dreamstime", "Dreamstime", "titles, descriptions, keywords, 3 categories and the editorial flag"),
    ("depositphotos", "Depositphotos", "descriptions, keywords and the editorial flag"),
    ("123rf", "123RF", "descriptions, keywords and the country"),
    ("pond5", "Pond5", "titles, descriptions, keywords, city, state, country and the editorial flag"),
    ("full", "All details (for Excel)", "everything, with lengths and counts, for your own records"),
]
SITE_LABELS = {k: label for k, label, _ in SITE_CSVS}


def csv_name(site: str, day: str = "") -> str:
    return core.csv_file_name(site, day)


def _write(path: str, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def write_site_csvs(folder: str, rows: list[dict], sites, day: str = "", ss_text: str = "title",
                    dt_tidy: bool = True) -> list[str]:
    """Write one CSV per site into folder. rows: dicts with file, folder, title, description, keywords, editorial,
    and optionally categories (the AI's Shutterstock choice), adobe_category, city, region, plus the 'full' extras.
    Every photo gets each site's categories. Returns the files written.
    dt_tidy: the Dreamstime CSV gets Dreamstime's fixes (title without the editorial place and date, no capitals,
    no half-finished description, no keyword repeats, see core.dreamstime_fix); the photos and the other sites keep
    the full text. Its titles are always cut to Dreamstime's 130 characters."""
    os.makedirs(folder, exist_ok=True)
    picked = []
    for r in rows:
        c = pick_categories(r["title"], r.get("description", ""), r["keywords"], bool(r.get("editorial")),
                            r.get("categories") or (), r.get("adobe_category"))
        picked.append(dict(r, categories=c["shutterstock"], adobe_category=c["adobe"], dt=c["dreamstime"]))
    plain = lambda t: core.plain_text(core.single_line(t or ""))
    desc = lambda r: plain(r.get("description") or "") or plain(r["title"])
    kw = lambda r: ",".join(k for k in (plain(x) for x in r["keywords"]) if k)
    out = []
    for site in sites:
        path = core.unused_path(os.path.join(folder, csv_name(site, day)))
        if site in ("shutterstock", "adobe", "full"):
            core.export_csv(path, picked, site, ss_text=ss_text)
        elif site == "dreamstime":
            # its title goes without the place and date (still editorial: the description has them and the
            # Editorial column says so), and never over the 130 characters Dreamstime allows
            dt_rows = [dict(r, title=core.dreamstime_title(r["title"], r.get("bare_title")) if dt_tidy
                            else core.shorten_title(r["title"])) for r in picked]
            if dt_tidy:
                dt_rows = [dict(r, **{k: v for k, v in core.dreamstime_fix(r["title"], r.get("description", ""),
                                                                           r["keywords"]).items() if k != "fixes"})
                           for r in dt_rows]
            _write(path, ["Filename", "Image Name", "Description", "Category 1", "Category 2", "Category 3",
                          "keywords", "Free", "W-EL", "P-EL", "SR-EL", "SR-Price", "Editorial", "MR doc Ids",
                          "Pr Docs"],
                   [[r["file"], plain(r["title"]), desc(r), *(list(r["dt"]) + [0, 0, 0])[:3], kw(r),
                     0, 0, 0, 0, 0, 1 if r.get("editorial") else 0, "", ""] for r in dt_rows])
        elif site == "depositphotos":
            _write(path, ["Filename", "Description", "Keywords", "Editorial", "Nudity", "Releases"],
                   [[r["file"], desc(r), kw(r), "yes" if r.get("editorial") else "no", "no", ""] for r in picked])
        elif site == "123rf":
            _write(path, ["oldfilename", "123rf_filename", "description", "keywords", "country"],
                   [[r["file"], "", desc(r), kw(r), r.get("country", "")] for r in picked])
        elif site == "pond5":
            _write(path, ["originalfilename", "title", "description", "keywords", "city", "region", "country",
                          "editorial"],
                   [[r["file"], plain(r["title"]), desc(r), kw(r), r.get("city", ""), r.get("region", ""),
                     r.get("country", ""), "yes" if r.get("editorial") else "no"] for r in picked])
        else:
            continue
        out.append(path)
    return out


# What the CSVs can't do for you ----------------------------------------------------------------------------------
PEOPLE_WORDS = {
    "person", "people", "man", "men", "woman", "women", "boy", "boys", "girl", "girls", "child", "children", "kid",
    "kids", "baby", "babies", "toddler", "teen", "teenager", "teenagers", "couple", "family", "families", "portrait",
    "selfie", "crowd", "bride", "groom", "worker", "workers", "employee", "customer", "customers",
    "shopper", "shoppers", "tourist", "tourists", "student", "students", "senior", "seniors", "athlete",
    "dancer", "musician", "chef", "doctor", "nurse", "businessman", "businesswoman", "mother", "father", "daughter",
    "son", "friends", "lady", "gentleman", "adult", "adults", "elderly", "pedestrian", "pedestrians", "spectators",
}
PROPERTY_WORDS = {"dog", "puppy", "cat", "kitten", "horse", "pet", "pets", "artwork", "mural", "sculpture",
                  "painting", "graffiti", "tattoo", "home interior", "house interior", "living room", "bedroom"}


def release_reason(r: dict) -> str:
    """'people' / 'property' when a commercial (not editorial) photo seems to need a release, else ''.
    Going by the words only: the AI names people and pets it sees in the title and keywords."""
    if r.get("editorial"):
        return ""
    words = set()
    for text in [r.get("title", ""), r.get("description", "")] + list(r.get("keywords", [])):
        low = str(text).lower()
        words.update(re.findall(r"[a-z]+", low))
        words.update(w for w in PROPERTY_WORDS if " " in w and w in low)
    if words & PEOPLE_WORDS:
        return "people"
    if words & PROPERTY_WORDS:
        return "property"
    return ""


def csv_warnings(rows: list[dict], sites) -> list[str]:
    """What still has to be done on the sites' own pages after uploading these CSVs."""
    out, sites = [], set(sites)
    people = [r["file"] for r in rows if release_reason(r) == "people"]
    prop = [r["file"] for r in rows if release_reason(r) == "property"]
    upload = sites & {"shutterstock", "adobe", "dreamstime", "depositphotos", "123rf", "pond5"}
    few = lambda names: ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
    if people and upload:
        out.append(f"Model release: {len(people)} photo{'s' if len(people) != 1 else ''} not marked editorial "
                   f"seem{'s' if len(people) == 1 else ''} to show people ({few(people)}). Attach a signed model "
                   "release on each site, or make them editorial if the people can be recognised and you have none.")
    if prop and upload:
        out.append(f"Property release: {len(prop)} photo{'s' if len(prop) != 1 else ''} not marked editorial "
                   f"seem{'s' if len(prop) == 1 else ''} to show pets, artwork or a home ({few(prop)}). Some sites "
                   "(Adobe Stock in particular) want a property release for these.")
    ed = [r["file"] for r in rows if r.get("editorial")]
    if ed and "adobe" in sites:
        out.append(f"Adobe Stock: {len(ed)} editorial photo{'s' if len(ed) != 1 else ''}. Adobe only takes editorial "
                   "from contributors as Illustrative Editorial (brands, no recognisable people, and an account "
                   "with 100+ downloads), marked on its upload page; its CSV has no editorial column. Their Adobe "
                   "titles are written without the location and date, as Adobe asks.")
    if "123rf" in sites or "pond5" in sites:
        nowhere = [r["file"] for r in rows if not r.get("country")]
        if nowhere and len(nowhere) < len(rows):
            out.append(f"Country: {len(nowhere)} photo{'s' if len(nowhere) != 1 else ''} have no location, so the "
                       "123RF / Pond5 country column is empty for them (set one with Set location…).")
    return out
