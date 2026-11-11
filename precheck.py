"""Check Before Upload: everything the stock sites would reject, or that would hurt sales, for a set of photos,
in one list. The window side is in iptc_tagger.CheckDialog; this file only works things out.

Image rules (from the agencies' contributor pages): Shutterstock and Adobe Stock want at least 4 MP, Adobe Stock at
most 100 MP; Shutterstock takes JPEGs up to 50 MB, Adobe Stock up to 45 MB; Dreamstime wants at least 3 MP; Alamy
wants at least 17 MB uncompressed (about 6 MP). The text rules are the ones in tagger_core.STOCK_SITES."""
import re

import tagger_core as core
import site_files

MB = 1024 * 1024
IMAGE_RULES = {   # site: (minimum megapixels, maximum megapixels, maximum JPEG size in MB)
    "shutterstock": (4.0, None, 50),
    "adobe": (4.0, 100.0, 45),
    "dreamstime": (3.0, None, None),
    "alamy": (17 * MB / 3 / 1e6, None, None),   # 17 MB uncompressed = width × height × 3 bytes
}
# a Shutterstock editorial caption: "City, State, Country - Month Day, Year: what's happening"
DATELINE = re.compile(r"^[^:]{2,}? [-–] (?:[A-Z][a-z]+\.? \d{1,2}, \d{4}|\d{4}-\d\d-\d\d|\d{1,2} [A-Z][a-z]+ \d{4})[:.] ")

SEVERE, WARN = "fix", "check"


def check_photo(p: dict, sites) -> list[dict]:
    """p: file, title, description, keywords, editorial, unsaved, width, height, bytes, raw, site_problems,
    order_note, brand_fixes, look_alikes (other file names), ss_text.
    Returns [{'level': 'fix'|'check', 'what': short name, 'detail': sentence, 'sites': [labels], 'fixable': bool}]."""
    out = []
    labels = lambda keys: [core.STOCK_SITES.get(k, {}).get("label") or site_files.SITE_LABELS.get(k, k) for k in keys]

    def add(level, what, detail, keys=(), fixable=False):
        out.append({"level": level, "what": what, "detail": detail, "sites": labels(keys), "fixable": fixable})

    if not (p.get("title") or "").strip():
        add(SEVERE, "No title", "The photo has no title yet: Detect it.")
    if not p.get("keywords"):
        add(SEVERE, "No keywords", "The photo has no keywords yet: Detect it.")
    if p.get("unsaved"):
        add(SEVERE, "Not saved", "Its title and keywords are only in the list, not in the file the sites will read. "
                                 "Save it first.", fixable=True)
    by_label = {v["label"]: k for k, v in core.STOCK_SITES.items()}
    for prob in p.get("site_problems") or []:
        site, _, rest = prob.partition(": ")            # "Shutterstock: 5 keywords (at least 7)"
        if site in by_label and rest:
            add(SEVERE, "Site rule", rest[:1].upper() + rest[1:], [by_label[site]])
        else:
            add(SEVERE, "Site rule", prob)
    # the picture itself
    w, h, size = p.get("width") or 0, p.get("height") or 0, p.get("bytes") or 0
    if w and h and not p.get("raw"):
        mp = w * h / 1e6
        for site in sites:
            rule = IMAGE_RULES.get(site)
            if not rule:
                continue
            lo, hi, max_mb = rule
            if mp < lo - 1e-9:
                need = "17 MB uncompressed (about 6 MP)" if site == "alamy" else f"{lo:g} MP"
                add(SEVERE, "Too small", f"{w} × {h} px is {mp:.1f} MP; it needs at least {need}.", [site])
            if hi and mp > hi:
                add(SEVERE, "Too large", f"{w} × {h} px is {mp:.0f} MP; at most {hi:g} MP.", [site])
            if max_mb and size > max_mb * MB:
                add(SEVERE, "File too big", f"The file is {size / MB:.0f} MB; at most {max_mb} MB.", [site])
    if "dreamstime" in sites and ((p.get("title") or "").strip() or p.get("keywords")):
        for i in core.dreamstime_checks(p.get("title", ""), p.get("description", ""), p.get("keywords") or []):
            if i["fixable"] and p.get("dt_tidy", True):
                continue                          # done in the Dreamstime CSV; the photo keeps its full text
            add(SEVERE if i["what"] in ("Title too short", "Description repeats title") else WARN,
                i["what"], i["detail"], ["dreamstime"], fixable=i["fixable"])
    if p.get("ai_markers"):
        add(WARN, "AI markers", "; ".join(p["ai_markers"][:3]) + ". Stock sites read these and may refuse the photo "
            "as AI generated. Lightroom's can be taken out at save (Settings → Saving); others need a fresh export.")
    # editorial
    if p.get("editorial") and "shutterstock" in sites:
        text = p.get("title") if p.get("ss_text", "title") != "description" else (p.get("description") or p.get("title"))
        if text and not DATELINE.match(text):
            add(SEVERE, "Editorial caption", "Shutterstock wants editorial captions to start with the place and date: "
                "\"City, Country - Month Day, Year: …\". Turn on Editorial titling with the location before the title.",
                ["shutterstock"])
    reason = site_files.release_reason(p)
    if reason == "people":
        add(WARN, "Model release", "Not marked editorial, and it seems to show people: attach a signed model release "
            "on each site, or make it editorial.")
    elif reason == "property":
        add(WARN, "Property release", "Not marked editorial, and it seems to show a pet, artwork or a home: some sites "
            "(Adobe Stock in particular) want a property release.")
    if p.get("look_alikes"):
        add(WARN, "Look-alike", "Nearly the same picture as " + ", ".join(p["look_alikes"][:3])
            + ": sites reject near-duplicates, so send the best one.")
    if p.get("brand_fixes"):
        add(WARN, "Brand spelling", "; ".join(p["brand_fixes"][:4]), fixable=True)
    if p.get("order_note"):
        add(WARN, "Keyword order", "The title's subject or brand is " + p["order_note"]
            + " (sites weigh those the most).", fixable=True)
    # one line for a problem several sites share ("Too small" for Shutterstock and Adobe Stock)
    merged = []
    for i in out:
        same = next((m for m in merged if m["what"] == i["what"] and m["detail"] == i["detail"]), None)
        if same and i["sites"]:
            same["sites"] += [s for s in i["sites"] if s not in same["sites"]]
        else:
            merged.append(i)
    return merged


def notes(photos: list[dict], sites) -> list[str]:
    """Reminders for the whole batch, said once rather than on every photo."""
    out = []
    n_ed = sum(1 for p in photos if p.get("editorial"))
    if n_ed and "adobe" in sites:
        out.append(f"Adobe Stock: {n_ed} photo{'s are' if n_ed != 1 else ' is'} editorial. Adobe only takes editorial "
                   "as Illustrative Editorial (an account with 100+ downloads, no recognisable people), marked on its "
                   "upload page.")
    return out


def summary(results: dict) -> tuple[int, int, int]:
    """(photos with something to fix, photos to check, photos all good)"""
    fix = sum(1 for issues in results.values() if any(i["level"] == SEVERE for i in issues))
    warn = sum(1 for issues in results.values() if issues and not any(i["level"] == SEVERE for i in issues))
    return fix, warn, sum(1 for issues in results.values() if not issues)
