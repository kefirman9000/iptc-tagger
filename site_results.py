"""Results: import the reports the stock sites let you download (accepted, rejected, sales, earnings) and see
what works where: the acceptance rate, why photos were rejected, the keywords that get rejected, accepted and
earn most, and your best sellers. Adapted from StockUp (github.com/kefirman9000/stockup).

Columns are found by their names, so most sites' CSV exports work. Photos are matched to the ones you sent
(by IPTC Tagger or StockUp) by file name, so their keywords can be counted. No Qt here."""
import csv
import hashlib
import html
import json
import os
import re
from datetime import datetime

import tagger_core as core

SITES = {"shutterstock": "Shutterstock", "adobe": "Adobe Stock", "dreamstime": "Dreamstime", "alamy": "Alamy",
         "istock": "iStock", "depositphotos": "Depositphotos", "123rf": "123RF", "pond5": "Pond5"}

REPORT_COLUMNS = {
    "file": ("original filename", "original file name", "filename", "file name", "file", "media name",
             "image name", "original name", "name"),
    "status": ("status", "review status", "state", "result", "decision", "moderation"),
    "reason": ("rejection reason", "reasons", "reason", "rejection", "reviewer comment", "comments",
               "comment", "note", "notes"),
    "earn": ("earnings", "your earnings", "revenue", "royalty", "royalties", "commission", "amount",
             "income", "earned", "payout", "usd"),
    "sales": ("downloads", "sales", "licenses", "licences", "times sold", "count", "quantity"),
}
ACCEPTED_WORDS = ("accept", "approv", "online", "active", "publish", "live", "ok")
REJECTED_WORDS = ("reject", "declin", "refus", "denied", "not accepted", "failed")


def results_path() -> str:
    return os.path.join(core.config_dir(), "site_results.json")


def _find_column(header, kind):
    names = [h.strip().lower().lstrip("﻿") for h in header]
    for want in REPORT_COLUMNS[kind]:          # exact names first, then names that contain it
        if want in names:
            return names.index(want)
    for want in REPORT_COLUMNS[kind]:
        for i, n in enumerate(names):
            if want in n and not (kind == "file" and ("id" == n or "url" in n)):
                return i
    return None


def _money(text) -> float:
    t = re.sub(r"[^0-9.,\-]", "", str(text or ""))
    if t.count(",") and t.count("."):
        t = t.replace(",", "")
    elif t.count(",") == 1 and len(t.split(",")[1]) <= 2:
        t = t.replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _status_word(text) -> str:
    t = str(text or "").strip().lower()
    if any(w in t for w in REJECTED_WORDS):
        return "rejected"
    if any(w in t for w in ACCEPTED_WORDS):
        return "accepted"
    return "pending" if t else ""


def guess_site(name: str, header=()):
    t = (os.path.basename(name) + " " + " ".join(header)).lower().replace(" ", "")
    for site, label in SITES.items():
        if site in t or label.lower().replace(" ", "") in t:
            return site
    if "ss_" in t or "submit" in t:
        return "shutterstock"
    return None


def load_results() -> dict:
    try:
        with open(results_path(), encoding="utf-8") as fh:
            r = json.load(fh)
        r.setdefault("sites", {})
        r.setdefault("imports", {})
        return r
    except (OSError, ValueError):
        return {"sites": {}, "imports": {}}


def _sent_index(site: str) -> dict:
    """file name (lowercase, also without extension) -> the photo's path, for photos sent to this site
    by IPTC Tagger or by StockUp."""
    import uploader as up
    idx = {}

    def add(path):
        name = os.path.basename(path).lower()
        idx[name] = path
        idx.setdefault(os.path.splitext(name)[0], path)
        idx.setdefault(up.safe_name(os.path.basename(path)).lower(), path)   # as the site saw it
    for e in (up.load_history().get(site) or {}).values():
        if e.get("file"):
            add(e["file"])
    for d in up.stockup_dirs():
        try:
            with open(os.path.join(d, "upload_history.json"), encoding="utf-8") as fh:
                h = json.load(fh)
        except (OSError, ValueError):
            continue
        for sites in (h.get("sha") or {}).values():
            e = (sites or {}).get(site)
            if e and e.get("file"):
                add(os.path.join(e.get("batch") or "", e["file"]))
    return idx


def read_words(path: str) -> tuple[str, list[str]]:
    """The title and keywords saved in a photo's XMP (quick: no ExifTool needed)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512 * 1024)
    except OSError:
        return "", []
    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", head, re.S)
    if not m:
        return "", []
    x = m.group(0).decode("utf-8", "replace")

    def items(tag):
        b = re.search(rf"<{tag}>(.*?)</{tag}>", x, re.S)
        return [html.unescape(t).strip() for t in re.findall(r"<rdf:li[^>]*>(.*?)</rdf:li>", b.group(1), re.S)] \
            if b else []
    title = (items("dc:title") or items("dc:description") or [""])[0]
    return title, [k for k in items("dc:subject") if k]


def import_report(path: str, site: str | None = None) -> dict:
    """Read one site report (CSV) into the results. Importing the same file twice changes nothing.
    Returns {"site", "label", "already", "rows", "photos", "matched", "date", "file"}."""
    with open(path, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig", "replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(text.splitlines(), dialect) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("That file has no rows to read.")
    header = rows[0]
    site = site if site in SITES else guess_site(path, header)
    if not site:
        raise ValueError("Couldn't tell which site this report is from. Choose the site, then import it again.")
    col = {k: _find_column(header, k) for k in REPORT_COLUMNS}
    cols = ", ".join(h.strip() for h in header if h.strip())
    if col["file"] is None:
        raise ValueError(f"Couldn't find a file name column in this report. Its columns are: {cols}.")
    if col["status"] is None and col["earn"] is None and col["sales"] is None:
        raise ValueError(f"This report has no status, earnings or downloads column, so there's nothing to import. "
                         f"Its columns are: {cols}.")
    res = load_results()
    if digest in res["imports"]:
        return {"site": site, "label": SITES[site], "already": True, **res["imports"][digest]}
    sent = _sent_index(site)
    per = {}
    for r in rows[1:]:
        cell = lambda k: r[col[k]].strip() if col[k] is not None and col[k] < len(r) else ""
        name = os.path.basename(cell("file").replace("\\", "/"))
        if not name:
            continue
        e = per.setdefault(name.lower(), {"name": name, "status": "", "reason": "", "earn": 0.0, "sales": 0})
        st = _status_word(cell("status"))
        if st:
            e["status"] = st
        if cell("reason") and (st == "rejected" or not st):
            e["reason"] = cell("reason")[:300]
        if col["earn"] is not None:
            e["earn"] += _money(cell("earn"))
            if col["sales"] is None:
                e["sales"] += 1                      # one row per sale
        if col["sales"] is not None:
            e["sales"] += int(_money(cell("sales")) or 0)
    store = res["sites"].setdefault(site, {})
    matched, when = 0, datetime.now().strftime("%Y-%m-%d")
    for key, e in per.items():
        hit = sent.get(key) or sent.get(os.path.splitext(key)[0])
        rec = store.setdefault(key, {"name": e["name"], "status": "", "reason": "", "earn": 0.0, "sales": 0,
                                     "keywords": [], "title": ""})
        if e["status"]:
            rec["status"] = e["status"]
            rec["reason"] = e["reason"] or (rec["reason"] if e["status"] == "rejected" else "")
        rec["earn"] = round(rec.get("earn", 0.0) + e["earn"], 4)
        rec["sales"] = rec.get("sales", 0) + e["sales"]
        rec["updated"] = when
        if hit:
            matched += 1
            rec["file"] = hit
            if not rec.get("keywords"):
                rec["title"], kws = read_words(hit)
                rec["keywords"] = kws[:60]
    summary = {"rows": len(rows) - 1, "photos": len(per), "matched": matched, "date": when,
               "file": os.path.basename(path)}
    res["imports"][digest] = {**summary, "site": site}
    os.makedirs(os.path.dirname(results_path()), exist_ok=True)
    tmp = results_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    os.replace(tmp, results_path())
    return {"site": site, "label": SITES[site], "already": False, **summary}


def summary(min_photos: int = 3) -> dict:
    """Per site: accepted / rejected / earnings, the commonest rejection reasons, and the keywords that do best
    and worst (keywords on at least min_photos photos)."""
    res = load_results()
    out = []
    for site, label in SITES.items():
        recs = list(res["sites"].get(site, {}).values())
        if not recs:
            continue
        acc = sum(1 for r in recs if r.get("status") == "accepted")
        rej = sum(1 for r in recs if r.get("status") == "rejected")
        reasons = {}
        for r in recs:
            if r.get("status") == "rejected" and r.get("reason"):
                k = r["reason"].strip().rstrip(".")
                reasons[k] = reasons.get(k, 0) + 1
        kw = {}
        for r in recs:
            for k in {x.lower() for x in r.get("keywords", [])}:
                d = kw.setdefault(k, {"photos": 0, "accepted": 0, "rejected": 0, "earn": 0.0, "sales": 0})
                d["photos"] += 1
                d["accepted"] += r.get("status") == "accepted"
                d["rejected"] += r.get("status") == "rejected"
                d["earn"] += r.get("earn", 0.0)
                d["sales"] += r.get("sales", 0)
        rows = []
        for k, d in kw.items():
            if d["photos"] < min_photos:
                continue
            judged = d["accepted"] + d["rejected"]
            rows.append({"keyword": k, **d, "earn": round(d["earn"], 2),
                         "rate": round(d["accepted"] / judged, 3) if judged else None,
                         "per_photo": round(d["earn"] / d["photos"], 3)})
        by_rate = [r for r in rows if r["rate"] is not None and r["accepted"] + r["rejected"] >= min_photos]
        earners = sorted((r for r in recs if r.get("earn") or r.get("sales")),
                         key=lambda r: (-r.get("earn", 0), -r.get("sales", 0)))[:10]
        out.append({
            "site": site, "label": label, "photos": len(recs), "accepted": acc, "rejected": rej,
            "rate": round(acc / (acc + rej), 3) if acc + rej else None,
            "earn": round(sum(r.get("earn", 0.0) for r in recs), 2),
            "sales": sum(r.get("sales", 0) for r in recs),
            "unmatched": sum(1 for r in recs if not r.get("file")),
            "reasons": sorted(reasons.items(), key=lambda kv: -kv[1])[:8],
            "rejected_most": sorted(by_rate, key=lambda r: (r["rate"], -r["photos"]))[:10],
            "accepted_most": sorted(by_rate, key=lambda r: (-r["rate"], -r["photos"]))[:10],
            "earns_most": sorted((r for r in rows if r["earn"] or r["sales"]),
                                 key=lambda r: (-r["per_photo"], -r["sales"]))[:10],
            "top_photos": [{"name": r["name"], "title": r.get("title", ""), "earn": round(r.get("earn", 0), 2),
                            "sales": r.get("sales", 0)} for r in earners],
        })
    imports = sorted(res["imports"].values(), key=lambda i: i.get("date", ""), reverse=True)[:12]
    return {"sites": out, "imports": imports}


def summary_html(s: dict | None = None) -> str:
    """The results as a page for the Results window."""
    s = summary() if s is None else s
    esc = html.escape
    if not s["sites"]:
        return ("<p>No reports imported yet.</p><p>Download a report from a site (accepted / rejected files, or "
                "sales / earnings) as CSV, then click <b>Import report…</b>. Shutterstock: Portfolio › Download "
                "CSV or Earnings › Download. Adobe Stock: Insights › Download CSV. Dreamstime, Depositphotos, 123RF "
                "and Pond5: their earnings or file pages' CSV export.</p>")
    pct = lambda r: "–" if r is None else f"{r * 100:.0f}%"
    parts = []
    for x in s["sites"]:
        parts.append(f"<h3>{esc(x['label'])}</h3><p><b>{x['photos']}</b> photos in reports · "
                     f"accepted <b>{x['accepted']}</b> · rejected <b>{x['rejected']}</b> · acceptance "
                     f"<b>{pct(x['rate'])}</b> · {x['sales']} sales · earnings <b>{x['earn']:.2f}</b>"
                     + (f" · <span style='color:#8a8f98'>{x['unmatched']} not matched to a photo you sent "
                        "(their keywords aren't counted)</span>" if x["unmatched"] else "") + "</p>")
        if x["reasons"]:
            parts.append("<p><b>Why photos were rejected</b></p><ul>"
                         + "".join(f"<li>{esc(r)} ({n})</li>" for r, n in x["reasons"]) + "</ul>")

        def kw_list(title, items, show):
            if items:
                parts.append(f"<p><b>{title}</b>: " + ", ".join(f"{esc(i['keyword'])} ({show(i)})" for i in items)
                             + "</p>")
        kw_list("Keywords rejected most", x["rejected_most"], lambda i: f"{pct(i['rate'])} accepted of {i['photos']}")
        kw_list("Keywords accepted most", x["accepted_most"], lambda i: f"{pct(i['rate'])} of {i['photos']}")
        kw_list("Keywords that earn most", x["earns_most"], lambda i: f"{i['per_photo']:.2f} a photo")
        if x["top_photos"]:
            parts.append("<p><b>Best sellers</b></p><ol>" + "".join(
                f"<li>{esc(p['title'] or p['name'])}: {p['sales']} sales, {p['earn']:.2f}</li>"
                for p in x["top_photos"]) + "</ol>")
    if s["imports"]:
        parts.append("<p style='color:#8a8f98'>Imported: " + "; ".join(
            f"{esc(i.get('file', ''))} ({SITES.get(i.get('site'), '')}, {i.get('date', '')})"
            for i in s["imports"]) + "</p>")
    return "".join(parts)
