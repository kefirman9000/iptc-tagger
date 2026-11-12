"""Getting photos ready for each stock site before they're sent, adapted from StockUp
(github.com/kefirman9000/stockup):

- each site's photo rules (smallest and largest size, biggest file, colour profile, baseline JPEG),
- adjusted copies made only for the sites that need them (sRGB, smaller file, baseline, resize), with the
  photo's own metadata carried over byte for byte; your originals are never changed,
- a shuffled upload order that keeps look-alike photos apart, so reviewers don't see a run of
  near-identical frames,
- the iStock folder (iStock has no FTP: you drag its Creative and Editorial folders into its upload page).

No Qt here, so all of it can be tested on its own."""
import math
import os
import random
import re
import shutil
from io import BytesIO

# min_px: fewest pixels (width x height). min_side: shortest side. max_px: most pixels. max_mb: biggest file.
# srgb: True = asks for sRGB; "pond5" = sRGB or Adobe RGB. baseline: standard (not progressive) JPEGs only.
# min_quality: re-exporting is better than sending below this JPEG quality. batch_gb: most per upload session.
RULES = {
    "shutterstock": {"min_px": 4_000_000, "min_label": "4 MP", "max_mb": 50, "srgb": True},
    "adobe": {"min_px": 3_840_000, "min_label": "4 MP", "max_px": 100_000_000, "max_mb": 45, "srgb": True},
    "dreamstime": {"min_px": 3_000_000, "min_label": "3 MP", "max_mb": 1024, "min_quality": 90},
    "alamy": {"min_px": 17_000_000 // 3, "min_label": "17 MB when opened (about 6 MP)", "baseline": True},
    "istock": {"min_px": 3_000_000, "min_label": "3 MP", "max_mb": 50},
    "depositphotos": {"min_px": 3_840_000, "min_label": "3.8 MP (2400 x 1600)", "max_mb": 50},
    "123rf": {"min_px": 6_000_000, "min_label": "6 MP", "max_mb": 30, "batch_gb": 4},
    "pond5": {"min_px": 2_000_000, "min_side": 1200, "min_label": "2 MP, 1200 px on each side",
              "max_px": 170_000_000, "srgb": "pond5"},
}
LABELS = {"shutterstock": "Shutterstock", "adobe": "Adobe Stock", "dreamstime": "Dreamstime", "alamy": "Alamy",
          "istock": "iStock", "depositphotos": "Depositphotos", "123rf": "123RF", "pond5": "Pond5"}
COPY_OPS = {"srgb", "smaller", "resize", "baseline", "downscale"}
DEFAULT_OPTS = {"copies": False, "resize_mode": "off", "resize_value": 0, "send_anyway": False, "shuffle": True}


# ---------- what a photo is ------------------------------------------------------------------------------------
_STD_LUMA_SUM = sum([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99])


def quality_from_table(vals) -> int:
    """Rough 1-100 JPEG quality from the luminance table, whatever order it's stored in."""
    scale = sum(vals) * 100 / _STD_LUMA_SUM
    q = (200 - scale) / 2 if scale <= 100 else 5000 / scale
    return max(1, min(100, round(q)))


def color_kind(icc: bytes, mode: str) -> str:
    """'sRGB', 'Adobe RGB', 'ProPhoto RGB', 'Display P3', 'CMYK', 'Grayscale', 'untagged' or the profile's name."""
    if mode == "CMYK":
        return "CMYK"
    if mode in ("L", "LA", "1"):
        return "Grayscale"
    if not icc:
        return "untagged"
    name = ""
    try:
        from PIL import ImageCms
        name = ImageCms.ImageCmsProfile(BytesIO(icc)).profile.profile_description or ""
    except Exception:
        pass
    low = name.lower()
    if not name or "srgb" in low or "61966" in low:
        return "sRGB"
    if "adobe rgb" in low or "adobergb" in low or "compatible with adobe" in low:
        return "Adobe RGB"
    if "prophoto" in low or "romm" in low:
        return "ProPhoto RGB"
    if "p3" in low:
        return "Display P3"
    return name


def photo_info(path: str) -> dict:
    """width, height, bytes, color, progressive, quality (reads only the start of the file)."""
    from PIL import Image
    info = {"width": 0, "height": 0, "bytes": os.path.getsize(path), "color": "untagged", "progressive": False,
            "quality": None}
    with Image.open(path) as im:
        info["width"], info["height"] = im.size
        info["color"] = color_kind(im.info.get("icc_profile") or b"", im.mode)
        info["progressive"] = bool(im.info.get("progressive") or im.info.get("progression"))
        q = getattr(im, "quantization", None) or {}
        if 0 in q and len(q[0]) == 64:
            info["quality"] = quality_from_table(list(q[0]))
    return info


# ---------- each site's rules ----------------------------------------------------------------------------------
def check(site: str, info: dict, opts: dict | None = None) -> dict:
    """What this site makes of this photo, with these upload options.
    Returns {"send": bool, "ops": set, "scale": float, "refused": [why], "warn": [msg], "fixes": [msg]}.
    refused: the site won't take it (too small, too big, CMYK…) and no copy can fix it, unless Send anyway.
    fixes: what an adjusted copy changes. warn: sent as is, but worth knowing."""
    o = dict(DEFAULT_OPTS, **(opts or {}))
    rules, label = RULES.get(site, {}), LABELS.get(site, site)
    out = {"send": True, "ops": set(), "scale": 1.0, "refused": [], "warn": [], "fixes": []}
    w, h = info.get("width") or 0, info.get("height") or 0
    px, mp = w * h, w * h / 1e6

    def need(op, msg, refuse_without_copy=False):
        if o["copies"]:
            out["ops"].add(op)
            out["fixes"].append(msg)
        elif refuse_without_copy:
            out["refused"].append(msg + " Allow adjusted copies to fix it.")
        else:
            out["warn"].append(msg + " Sent as it is (allow adjusted copies to fix it).")

    if rules.get("min_px") and px and px < rules["min_px"]:
        out["refused"].append(f"too small for {label}: {mp:.1f} MP, it needs at least {rules['min_label']}.")
    elif rules.get("min_side") and px and min(w, h) < rules["min_side"]:
        out["refused"].append(f"too small for {label}: the short side is {min(w, h)} px, it needs "
                              f"{rules['min_side']} px.")
    if rules.get("max_px") and px > rules["max_px"]:
        need("resize", f"{mp:.0f} MP is over {label}'s limit of {rules['max_px'] // 1_000_000} MP.", True)
    mb = (info.get("bytes") or 0) / 1048576
    if rules.get("max_mb") and mb > rules["max_mb"]:
        need("smaller", f"{mb:.0f} MB is over {label}'s limit of {rules['max_mb']} MB.", True)
    color = info.get("color", "sRGB")
    if color == "CMYK":
        need("srgb", "saved as CMYK; stock sites need RGB.", True)
    elif rules.get("srgb") is True and color not in ("sRGB", "untagged", "Grayscale"):
        need("srgb", f"colour profile is {color}; {label} asks for sRGB, or colours can shift.")
    elif rules.get("srgb") == "pond5" and color not in ("sRGB", "untagged", "Adobe RGB", "Grayscale"):
        need("srgb", f"colour profile is {color}; Pond5 takes sRGB or Adobe RGB.")
    if rules.get("min_quality") and (info.get("quality") or 100) < rules["min_quality"]:
        out["warn"].append(f"saved at about JPEG quality {info['quality']}; {label} asks for best quality. "
                           "Re-export at 100% (saving it again can't bring the detail back).")
    if rules.get("baseline") and info.get("progressive"):
        need("baseline", f"a progressive JPEG; {label} asks for standard (baseline) JPEGs.")

    # your resize-for-upload setting: copies only, never below the site's minimum
    if o["copies"] and o["resize_mode"] in ("long_side", "megapixels") and px:
        try:
            val = float(o["resize_value"] or 0)
        except (TypeError, ValueError):
            val = 0
        if val > 0:
            k = val / max(w, h) if o["resize_mode"] == "long_side" else math.sqrt(val * 1e6 / px)
            floor = rules.get("min_px", 0) * 1.02
            if rules.get("min_side"):
                floor = max(floor, (rules["min_side"] / min(w, h)) ** 2 * px)
            kept = ""
            if k < 1 and px * k * k < floor:
                k, kept = math.sqrt(floor / px), f" (kept above {label}'s minimum)"
            if k * k < 0.9:                       # not worth re-saving the photo to shave off under 10%
                out["ops"].add("downscale")
                out["scale"] = k
                out["fixes"].append(f"resized to {round(w * k)} × {round(h * k)} px{kept}, from your setting.")
    if out["refused"] and not o["send_anyway"]:
        out["send"] = False
    return out


# ---------- adjusted copies ------------------------------------------------------------------------------------
def _meta_segments(raw: bytes) -> bytes:
    """The EXIF/XMP, IPTC and comment segments of a JPEG, to carry over to a new copy unchanged."""
    segs, i = [], 2
    while i + 4 <= len(raw) and raw[i] == 0xFF:
        marker = raw[i + 1]
        if marker in (0xDA, 0xD9):
            break
        ln = int.from_bytes(raw[i + 2:i + 4], "big")
        if marker in (0xE1, 0xED, 0xFE):
            segs.append(raw[i:i + 2 + ln])
        i += 2 + ln
    return b"".join(segs)


def _insert_segments(jpeg: bytes, segs: bytes) -> bytes:
    i = 2
    if jpeg[2:4] == b"\xff\xe0":       # keep JFIF first
        i = 4 + int.from_bytes(jpeg[4:6], "big")
    return jpeg[:i] + segs + jpeg[i:]


def make_copy(src: str, dst: str, ops, site: str = "", scale: float = 1.0):
    """An adjusted copy for one site: as faithful as possible (the same colour detail, quality 95 or the
    original's own if higher, sRGB the way Photoshop converts by default, black and white kept), with all the
    original's metadata. The original isn't touched."""
    from PIL import Image, JpegImagePlugin
    rules = RULES.get(site, {})
    with open(src, "rb") as fh:
        raw = fh.read()
    im = Image.open(BytesIO(raw))
    icc = im.info.get("icc_profile")
    try:
        sampling = JpegImagePlugin.get_sampling(im)
    except Exception:
        sampling = -1
    sampling = sampling if sampling in (0, 1, 2) else 0
    q_tables = getattr(im, "quantization", None) or {}
    orig_q = quality_from_table(list(q_tables[0])) if 0 in q_tables and len(q_tables[0]) == 64 else 95
    best_q = min(100, max(95, orig_q))
    if "srgb" in ops or im.mode not in ("RGB", "L"):
        from PIL import ImageCms
        srgb = ImageCms.createProfile("sRGB")
        if icc and im.mode in ("RGB", "CMYK", "L"):
            intent = getattr(getattr(ImageCms, "Intent", None), "RELATIVE_COLORIMETRIC", 1)
            flags = getattr(getattr(ImageCms, "Flags", None), "BLACKPOINTCOMPENSATION", 0x2000)
            try:
                im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(BytesIO(icc)), srgb,
                                               renderingIntent=intent, outputMode="RGB", flags=flags)
            except Exception:
                im = im.convert("RGB")
        else:
            im = im.convert("RGB")
        out_icc = ImageCms.ImageCmsProfile(srgb).tobytes()
    else:
        out_icc = icc
    if "downscale" in ops and scale < 1:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    if "resize" in ops and rules.get("max_px") and im.width * im.height > rules["max_px"]:
        k = math.sqrt(rules["max_px"] / (im.width * im.height)) * 0.995
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    segs = _meta_segments(raw)

    def encode(img, quality, sub):
        buf = BytesIO()
        kw = {"quality": quality, "progressive": False, "optimize": True}
        if img.mode == "RGB":
            kw["subsampling"] = sub
        if out_icc:
            kw["icc_profile"] = out_icc
        img.save(buf, "JPEG", **kw)
        return _insert_segments(buf.getvalue(), segs)

    data = encode(im, best_q, sampling)
    if "smaller" in ops:                    # only for a site's file-size limit: step down gently
        limit = rules.get("max_mb", 10_000) * 1048576
        steps = [(q, sampling) for q in (93, 90, 87, 84) if q < best_q] + \
                [(q, 2) for q in (90, 85, 80) if sampling != 2]
        for q, sub in steps:
            if len(data) <= limit:
                break
            data = encode(im, q, sub)
        while len(data) > limit:
            im = im.resize((int(im.width * 0.9), int(im.height * 0.9)), Image.LANCZOS)
            data = encode(im, 85, 2)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dst)
    return dst


def copies_dir(config_dir: str, site: str) -> str:
    return os.path.join(config_dir, "upload_copies", site)


# ---------- the upload order -----------------------------------------------------------------------------------
def picture_hash(path: str) -> str:
    """A 64-bit 'what it looks like' hash, so look-alikes and re-exports match."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.draft("L", (128, 128))
            small = im.convert("L").resize((9, 8), Image.BILINEAR)
        px = list(small.tobytes())
        bits = 0
        for r in range(8):
            for c in range(8):
                bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
        return "%016x" % bits
    except Exception:
        return ""


def _bits_apart(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _name_number(f: str):
    m = re.search(r"(\d+)(?!.*\d)", os.path.splitext(os.path.basename(f))[0])
    return int(m.group(1)) if m else None


def spread_order(files, looks: dict, seed) -> list:
    """Shuffle, keeping photos that look alike (or were shot one after another) apart, so a site's reviewers
    don't see a run of near-identical frames. Each site gets its own seed, so its own order."""
    files = list(files)
    rnd = random.Random(seed)
    parent = {f: f for f in files}

    def find(f):
        while parent[f] != f:
            parent[f] = parent[parent[f]]
            f = parent[f]
        return f

    for i, a in enumerate(files):
        for b in files[i + 1:]:
            la, lb = looks.get(a), looks.get(b)
            if la and lb and _bits_apart(la, lb) <= 12:
                parent[find(a)] = find(b)
    if not any(looks.get(f) for f in files):     # no picture hashes: frames 1-3 apart are a series
        nums = sorted((n, f) for f in files if (n := _name_number(f)) is not None)
        for (n1, a), (n2, b) in zip(nums, nums[1:]):
            if n2 - n1 <= 3:
                parent[find(a)] = find(b)
    groups = {}
    for f in files:
        groups.setdefault(find(f), []).append(f)
    pools = list(groups.values())
    for g in pools:
        rnd.shuffle(g)
    rnd.shuffle(pools)
    order, recent = [], []
    while any(pools):
        live = [g for g in pools if g]
        far = ([g for g in live if id(g) not in recent]             # not one of the last two series,
               or [g for g in live if not recent or id(g) != recent[-1]]   # else at least not the last one
               or live)
        most = max(len(g) for g in far)
        pick = rnd.choice([g for g in far if len(g) == most])     # biggest series first
        order.append(pick.pop())
        recent = (recent + [id(pick)])[-2:]                          # not the same series within the next two
    return order


# ---------- iStock ---------------------------------------------------------------------------------------------
def prepare_istock(items, dest: str) -> dict:
    """iStock has no FTP: its upload page takes the files you drag in, Creative and Editorial in separate
    batches. items: [(path, editorial, ops, scale)]. Makes dest/Creative and dest/Editorial with real copies
    (adjusted ones where ops say so). Returns {"creative": n, "editorial": n, "folder": dest}."""
    counts = {"creative": 0, "editorial": 0, "folder": dest}
    for path, editorial, ops, scale in items:
        kind = "editorial" if editorial else "creative"
        target = os.path.join(dest, kind.capitalize(), os.path.basename(path))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if ops & COPY_OPS:
            make_copy(path, target, ops, "istock", scale)
        else:
            tmp = target + ".part"
            shutil.copy2(path, tmp)
            os.replace(tmp, target)
        counts[kind] += 1
    return counts
