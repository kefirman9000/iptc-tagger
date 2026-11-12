"""
IPTC Tagger core: everything except the window.

  * finds JPEGs in dropped files and folders
  * reads and writes title + keywords with ExifTool (IPTC and XMP)
  * proves the image itself is never changed: the compressed image data is
    fingerprinted before and after every write, and the original file is put
    back if the two ever differ
  * asks a free AI vision model running on this computer (through Ollama) for a
    title and keywords for each photo. Nothing is sent over the internet.

Uses only the Python standard library plus Pillow (for the small preview copy
shown to the AI model, which is made in memory and never saved).
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import unicodedata
import subprocess
import sys
import tempfile
import http.client
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

APP_NAME = "IPTC Tagger"
# Where things are. As a standalone program (built with PyInstaller) the bundled files are unpacked to
# sys._MEIPASS, while the program's own folder (where ExifTool can be put) is the one holding the .exe.
FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
APP_VERSION = "2026.11.12.1"
JPEG_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif"}
# camera RAW files: titles and keywords go into the Lightroom sidecar next to them (photo.xmp), never the RAW.
# DNG is left out: Lightroom ignores sidecars for DNG and keeps its metadata inside the file itself.
RAW_EXTS = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2", ".rwl",
            ".pef", ".srw", ".x3f", ".3fr", ".fff", ".iiq", ".erf", ".mef", ".mos", ".mrw", ".kdc", ".dcr"}

# Free vision models (run locally with Ollama) offered for one-click download:
# (Ollama model name, label, download size, memory guidance)
RECOMMENDED_MODELS = [
    ("gemma3:4b", "Gemma 3 4B (good, fast)", "3.3 GB", "runs on most computers with 8 GB of memory"),
    ("qwen3-vl:8b", "Qwen3-VL 8B (more detailed)", "6.1 GB", "best with 16 GB of memory or more"),
]
DEFAULT_MODEL = RECOMMENDED_MODELS[0][0]
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_KEYWORD_MIN = 30   # keywords per photo: at least…
DEFAULT_KEYWORD_MAX = 40   # …and at most
MAX_WORD_REPEATS = 3       # one word may appear in at most this many keywords (no keyword stuffing)

# IPTC (the older standard) has byte limits; XMP does not. Over-long values are
# trimmed for IPTC only, so XMP always keeps the full text.
IPTC_TITLE_MAX = 64      # IPTC:ObjectName
IPTC_KEYWORD_MAX = 64    # IPTC:Keywords (each)

PREVIEW_MAX_EDGE = 1024  # long edge of the in-memory copy shown to the AI model


# --------------------------------------------------------------------------- files

def is_jpeg(path: str) -> bool:
    name = os.path.basename(path)
    return (not name.startswith(".")) and os.path.splitext(name)[1].lower() in JPEG_EXTS


def is_raw(path: str) -> bool:
    name = os.path.basename(path)
    return (not name.startswith(".")) and os.path.splitext(name)[1].lower() in RAW_EXTS


def sidecar_path(path: str) -> str:
    """Lightroom's sidecar for a RAW file: photo.CR3 -> photo.xmp, in the same folder."""
    return os.path.splitext(path)[0] + ".xmp"


def metadata_target(path: str) -> str:
    """The file titles and keywords are read from and written to: the photo itself for a JPEG, the .xmp sidecar
    for a RAW file (the RAW is never written to)."""
    return sidecar_path(path) if is_raw(path) else path


def collect_photos(paths, raws: bool = True) -> tuple[list[str], int]:
    """Expand dropped files/folders (folders searched including subfolders).
    Returns (sorted unique JPEG and, with raws=True, camera RAW paths, number of other files skipped).
    Lightroom .xmp sidecars are not counted as skipped: they belong to a RAW file."""
    found, skipped, seen = [], 0, set()

    def add(p):
        nonlocal skipped
        if is_jpeg(p) or (raws and is_raw(p)):
            key = os.path.normcase(os.path.abspath(p))
            if key not in seen:
                seen.add(key)
                found.append(os.path.abspath(p))
        elif not os.path.basename(p).startswith(".") and not p.lower().endswith(".xmp"):
            skipped += 1

    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    add(os.path.join(root, f))
        elif os.path.isfile(p):
            add(p)
    return found, skipped


def collect_jpegs(paths) -> tuple[list[str], int]:
    """JPEGs only (older callers)."""
    return collect_photos(paths, raws=False)


def file_fingerprint(path: str) -> tuple:
    """Size, modified time and SHA-256 of a whole file: used to prove a RAW file is never changed."""
    st = os.stat(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return st.st_size, st.st_mtime_ns, h.hexdigest()


# the embedded JPEGs a RAW file carries, largest first (ExifTool tag names)
RAW_PREVIEW_TAGS = ["JpgFromRaw", "PreviewImage", "OtherImage", "ThumbnailImage"]
_preview_et = {"et": None}


def set_preview_exiftool(et) -> None:
    """The ExifTool used to pull the embedded preview out of RAW files (for thumbnails and the AI)."""
    _preview_et["et"] = et


def raw_preview_jpeg(path: str, et=None) -> tuple[bytes, int]:
    """(the biggest embedded JPEG in a RAW file, the RAW's EXIF orientation 1-8). Read-only."""
    et = et or _preview_et["et"]
    if et is None:
        raise ExifToolError("ExifTool is needed to show RAW files")
    best = b""
    for tag in RAW_PREVIEW_TAGS:
        data = et.binary(tag, path)
        if data[:2] == b"\xff\xd8" and len(data) > len(best):
            best = data
            if tag != "ThumbnailImage" and len(best) > 200_000:
                break                                   # a full-size preview: good enough
    if not best:
        raise ExifToolError("this RAW file has no embedded preview")
    try:
        orient = int((et._run(["-n", "-s3", "-Orientation", path], timeout=30).strip() or "1").split()[0])
    except (ValueError, ExifToolError):
        orient = 1
    return best, orient


def open_image(path: str):
    """A Pillow image for any supported photo, already turned upright. RAW files use their embedded JPEG."""
    from PIL import Image, ImageOps
    if not is_raw(path):
        im = Image.open(path)
        return im, True
    data, orient = raw_preview_jpeg(path)
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.getexif().get(0x0112, 1) != 1:
        return im, True                                  # the preview carries its own orientation
    turn = {3: Image.Transpose.ROTATE_180, 6: Image.Transpose.ROTATE_270, 8: Image.Transpose.ROTATE_90,
            2: Image.Transpose.FLIP_LEFT_RIGHT, 4: Image.Transpose.FLIP_TOP_BOTTOM}.get(orient)
    if turn is not None:
        im = im.transpose(turn)
    return im, False


def jpeg_image_data_hash(path: str) -> str:
    """SHA-256 of everything in a JPEG that makes up the picture: quantisation and
    Huffman tables, frame header and the compressed scan data. Metadata segments
    (APP0-APP15, comments) are skipped, so this only changes if the image changes."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG file")
    h = hashlib.sha256()
    i, n = 2, len(data)
    while i < n:
        if data[i] != 0xFF:
            raise ValueError("damaged JPEG structure")
        while i < n and data[i] == 0xFF:          # skip fill bytes
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:   # markers without a length
            continue
        if marker == 0xD9:                                     # end of image
            break
        seg_len = int.from_bytes(data[i:i + 2], "big")
        seg = data[i:i + seg_len]
        if marker == 0xDA:                                     # start of scan: picture data runs to the end
            end = data.rfind(b"\xff\xd9")
            h.update(b"\xff\xda")
            h.update(data[i:end if end > i else n])
            return h.hexdigest()
        if not (0xE0 <= marker <= 0xEF or marker == 0xFE):     # keep everything except metadata segments
            h.update(bytes([0xFF, marker]))
            h.update(seg)
        i += seg_len
    return h.hexdigest()


# --------------------------------------------------------------------------- text helpers

def truncate_utf8(text: str, max_bytes: int) -> str:
    """Cut text to fit max_bytes of UTF-8, preferring a word boundary."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    out = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    if " " in out[len(out) // 2:]:
        out = out[: out.rfind(" ")]
    return out.rstrip(" ,;-")


def single_line(text: str) -> str:
    return " ".join(str(text).split())


_QUOTES = "\"'“”‘’„«»"


# The end of a title where the AI's answer ran on: `…Equipment”, “Keywords` (cut off before the colon), or a
# field name with nothing after it. Everything from the quote on goes.
_LEAKED_END = re.compile(r"\s*[" + _QUOTES + r"]\s*[,;:]?\s*[" + _QUOTES + r"]?\s*(?:keywords?|tags|description|"
                         r"caption|title|categories)\b\s*[" + _QUOTES + r"]?\s*[:=]?\s*$", re.I)
_DOUBLE_QUOTES = "\"“”„«»"


def fix_stray_quotes(text: str) -> str:
    """Take out double quotes that don't pair up (`Real Broth”, Six Boxes` → `Real Broth, Six Boxes`) and the
    leftover end of an answer that ran on into the title. Quotes that pair up (`“Fresh”`) are kept."""
    t = _LEAKED_END.sub("", text or "")
    opens = sum(t.count(q) for q in "“„«")
    closes = sum(t.count(q) for q in "”»")
    plain = t.count('"')
    if opens != closes or plain % 2:
        t = re.sub(r"\s*[" + _DOUBLE_QUOTES + r"]\s*(?=[,;:])", "", t)          # `Broth”, Six` → `Broth, Six`
        t = re.sub(r"[" + _DOUBLE_QUOTES + r"]", "", t)
    return " ".join(t.split())


def clean_title(text: str) -> str:
    t = fix_stray_quotes(single_line(text)).strip().strip(_QUOTES).strip().rstrip(",;:- ")
    return t[:-1] if t.endswith(".") and not t.endswith("..") else t


def norm_description(text: str) -> str:
    """A description as it is saved: one line, single spaces (its full stop stays)."""
    return " ".join(single_line(text or "").split())


# A model now and then writes its whole answer inside the title, with curly quotes, e.g.
#   Shelf Display”, “Keywords”: “car care”, “wheel cleaner”, … 16
_LEAKED = re.compile(r"[\s,;]*[" + _QUOTES + r"]?\s*[,;]?\s*[" + _QUOTES + r"]?\s*\b(?:keywords?|tags)\b\s*["
                     + _QUOTES + r"]?\s*[:=：]", re.I)


def split_leaked_keywords(title: str) -> tuple[str, list[str]]:
    """Cut a keyword list (and anything after it) out of a title. Returns (title, the keywords found)."""
    if not title:
        return title, []
    m = _LEAKED.search(title)
    if not m:
        return title, []
    head, tail = title[:m.start()], title[m.end():]
    parts = [p.strip(" \t" + _QUOTES + "[](){}") for p in re.split(r"[" + _QUOTES + r"]?\s*,\s*[" + _QUOTES + r"]?|[“”\"]\s+[“\"]", tail)]
    words = [p for p in parts if p and not re.fullmatch(r"[\d\s.]+", p) and len(p.split()) <= 6]
    # a stray number after the list (a count the model added) isn't a keyword
    words = [re.sub(r"[" + _QUOTES + r"]\s*\d+\s*$", "", w).strip(" " + _QUOTES) for w in words]
    return head.strip(" ,;:-–—" + _QUOTES), [w for w in words if w]


# Symbols in the AI's titles and keywords ------------------------------------------------------
SYMBOL_MODES = [("convert", "Convert to words (& → and, + → plus, @ → at, % → percent)"),
                ("safe", "Stock-site safe: symbols to words, é → e, no other special characters"),
                ("remove", "Remove them"),
                ("keep", "Keep as written")]
SYMBOL_WORDS = [("&", "and"), ("+", "plus"), ("@", "at"), ("%", "percent"), ("°", "degrees")]
SYMBOL_DROP = "™®©#*~^|<>{}[]\\=_`•"          # never useful in a title or keyword


# "Stock-site safe": what's left is plain English letters, numbers and this basic punctuation (Shutterstock asks
# for English titles without special characters, and doesn't say which ones it means)
SAFE_PUNCTUATION = " ,.-':;()/!?$"
SAFE_WORDS = [("€", "euro"), ("£", "pound"), ("¥", "yen")]
_PLAIN_LETTERS = {"ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L",
                  "đ": "d", "Đ": "D", "ð": "d", "þ": "th", "Þ": "Th", "ı": "i"}
_TYPOGRAPHY = {"‘": "'", "’": "'", "‚": "'", "′": "'", "“": "", "”": "", "„": "", '"': "", "«": "", "»": "",
               "–": " - ", "—": " - ", "‐": "-", "‑": "-", "…": " ", "·": " ", "×": " x ",
               "™": "", "®": "", "©": "", "℠": ""}


def special_characters(text: str) -> list[str]:
    """The characters a stock site may reject: anything but plain letters, numbers and basic punctuation."""
    seen = []
    for ch in text or "":
        if not (ch.isascii() and (ch.isalnum() or ch in SAFE_PUNCTUATION)) and ch not in seen and not ch.isspace():
            seen.append(ch)
    return seen


def plain_text(text: str) -> str:
    """Stock-site safe: symbols to words, accents to plain letters (Café -> Cafe), curly quotes and long dashes to
    plain ones, and anything else that isn't a plain letter, number or basic punctuation taken out."""
    import unicodedata
    t = text or ""
    for a, b in list(_TYPOGRAPHY.items()) + list(_PLAIN_LETTERS.items()):
        t = t.replace(a, b)
    for sym, word in SYMBOL_WORDS + SAFE_WORDS:
        t = t.replace(sym, f" {word} ")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if ch.isascii() and (ch.isalnum() or ch in SAFE_PUNCTUATION or ch.isspace()))
    t = re.sub(r"\s*-\s*-+\s*", " - ", t)                 # "a - - b" left by removed characters
    t = " ".join(t.split())
    t = re.sub(r"\s+([,.;:!?)])", r"\1", t).replace("( ", "(").replace("()", "")
    return t.strip(" ,;:-")


def tidy_symbols(text: str, mode: str = "convert") -> str:
    """Convert symbols to words ('Salt & Pepper' -> 'Salt and Pepper'), remove them, or keep them. "safe" also
    makes the text plain (see plain_text). Normal punctuation (, . - ' : ! ? / ( ) and currency signs) is
    otherwise left alone."""
    if mode == "safe":
        return plain_text(text) if text else text
    if mode not in ("convert", "remove") or not text:
        return text
    t = text
    for sym, word in SYMBOL_WORDS:
        t = t.replace(sym, f" {word} " if mode == "convert" else " ")
    for ch in "™®©":
        t = t.replace(ch, "")
    for ch in SYMBOL_DROP:
        t = t.replace(ch, " ")
    t = " ".join(t.split())
    return re.sub(r"\s+([,.;:!?)])", r"\1", t).replace("( ", "(")


def clean_keywords(words, limit: int | None = None) -> list[str]:
    """Trim, drop empties, replace separators, de-duplicate (case-insensitive, first wins)."""
    out, seen = [], set()
    for w in words or []:
        w = single_line(w).replace(",", " ").replace(";", " ").strip().strip("#").strip()
        w = " ".join(w.split())
        if not w:
            continue
        key = w.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if limit and len(out) >= limit:
            break
    return out


def parse_keywords_text(text: str) -> list[str]:
    """Keywords as typed in the table: separated by commas, semicolons or new lines."""
    parts = text.replace(";", ",").replace("\n", ",").split(",")
    return clean_keywords(parts)


def keywords_to_text(words) -> str:
    return ", ".join(words)


def merge_keywords(existing, new) -> list[str]:
    return clean_keywords(list(existing or []) + list(new or []))


# --------------------------------------------------------------------------- settings

def config_dir() -> str:
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


DEFAULT_SETTINGS = {
    "ollama_url": DEFAULT_OLLAMA_URL,
    "model": DEFAULT_MODEL,
    "text_model": "",         # a second model for the steps that only write text (Reword, checks, rewrites);
                              # "" = the photo model does everything
    "keyword_min": DEFAULT_KEYWORD_MIN,
    "keyword_max": DEFAULT_KEYWORD_MAX,
    "exiftool_path": "",
    "keep_existing_titles": True,
    "keep_existing_keywords": True,
    "context": "",       # "About these photos": clarification for the whole batch
    "direction": "",     # "Title & keyword direction": how titles/keywords should be written
    "default_type": "auto",   # photo type used for photos that don't have their own
    "custom_types": "",       # your own photo types, one per line: "Name: what the photo shows"
    "lead_keywords": "",      # keywords always put first (location, date…); {year} {month} {date} allowed
    "title_prefix": "",       # text put before every suggested title; {year} {month} {date} allowed
    "title_date_pos": "off",  # date taken in the title: off / start / before_title / end
    "title_date_format": "long",
    "title_date_sep": "colon",  # "Tokyo, Japan - October 26, 2026: Title" (the editorial style)
    "title_location": "",     # location put just before the date in titles (editorial style)
    "editorial_on": False,    # Editorial: include the location and date taken in titles and keywords
    "editorial_pos": "before",  # where they go: before / after the AI's title and keywords
    "title_loc_sep": "dash",  # what goes between the location and the date
    "custom_date_on": False,  # use custom_date instead of each photo's own date taken
    "custom_date": "",        # YYYY-MM-DD
    "editorial_keywords": True,  # Editorial also adds the place and the month/year to the keywords
    "location_for_ai": True,  # also tell the AI the location, so it uses it in keywords
    "location_history": [],   # locations used before, most recent first (the dropdown in Location in title)
    "revise": True,           # double-check each draft against your instructions (one extra, text-only pass)
    "xmp2iptc_on_save": False, # also copy every other XMP field to IPTC with xmp2iptc.args (the title and keywords
                               # are already written in full to IPTC directly)
    "iptc_direct": True,
    "instructions_merged": True,  # (one "Instructions for the AI" box; "context" and "direction" hold the same text)      # (marks settings from after the switch to one-step saving)
    "xmp2iptc_args": "",      # path to xmp2iptc.args; blank = find it next to ExifTool, else the bundled copy
    "meta_fields": None,      # which metadata fields are written (None = the usual set, see METADATA_FIELDS)
    "symbols": "convert",     # symbols in the AI's titles and keywords: convert (& -> and) / remove / keep
    "title_length": "detailed",  # short / standard / detailed (see TITLE_LENGTHS)
    "ai_time_limit": 30,      # seconds per photo before it's stopped (0 = no limit); first photo gets extra
    "ai_retries": 1,          # how many times a photo that went over the limit is tried again
    "debug_log": False,       # write a detailed log of every AI request to debug_log.txt
    "check_updates": True,    # look for a newer release on GitHub when the app starts
    "avoid_on": False,        # show the per-photo Avoid column and use it
    "strip_develop": True,    # saving a JPEG takes out Lightroom's develop settings (XMP-crs), AI masks included
    "dreamstime_csv_auto": True,
    "dreamstime_tidy_csv": True,   # Dreamstime's fixes go into its CSV only; the photos keep the full text   # after uploading to Dreamstime, send its CSV by itself once the photos are in
    "shutterstock_text": "title",  # Shutterstock's one text column gets: title / description
    "stockup_path": "",       # StockUp.app / StockUp.exe / stockup.py ("" = look in the usual places)
    "stockup_after_save": False,  # after Save Metadata, open the saved photos in StockUp
    "upload_sites": [],           # the sites ticked in the Upload window ([] = every site with a login)
    "upload_encrypted_only": False,   # skip an agency that only offers plain FTP
    "upload_shuffle": True,       # each site gets its own order, look-alike photos kept apart
    "upload_copies": False,       # make adjusted copies (sRGB, smaller, baseline, resize) where a site needs them
    "upload_resize_mode": "off",  # adjusted copies' size: off / long_side / megapixels
    "upload_resize_value": 6000,  # px for long_side, MP for megapixels
    "detect_style": [],       # "Every Detect" checklist: keys from STYLE_CHOICES
    "text_checks": ["numbers", "repeats", "list", "desc_copy"],   # automatic checks (DEFAULT_TEXT_CHECKS) on the AI's titles and descriptions
    "profiles": {},           # saved profiles: name -> {context, detect_style, default_type, lead_keywords, …}
    "profile": "",            # the profile last chosen
    "title_caps": "title",    # title (Title Case: small words lowercase) / every (Capitalise Every Word)
    "direct_titles": True,    # titles start with the subject: no "A Picture of…" in front
    "never_use": "",          # words the AI must never use (comma or line separated); removed from its keywords
    "never_use_titles": True, # ... and taken out of its titles too
    "finish_alert": "both",   # when a long batch finishes: both (sound + notification) / sound / notify / off
    "preload_model": True,    # load the AI model into memory when the app starts
    "keep_model_ready": True, # keep it loaded while the app is open (else Ollama unloads it after 15 min)
    "last_seen_version": "",  # the version whose "What's new" was last shown
    "skipped_update": "",     # a version the user chose to skip
    "confirm_save": True,     # ask before saving with Save metadata
    "read_label": True,       # read the text on packaging and signs first (one quick extra step per photo)
    "tidy_keywords": True,    # remove filler keywords and put the title's words first
    "keyword_order": True,    # the first ten keywords: the title's subject and brand, specific before general
    "brand_spellings": None,  # None = the built-in list (DEFAULT_BRAND_SPELLINGS); your own edits otherwise
    "brand_alternates": True, # add the other ways buyers search for a brand (bjs, coca cola) to the keywords
    "ai_description": True,   # also write a separate description (caption): Alamy, Dreamstime, Shutterstock
    "concept": True,          # titles and descriptions also say what it's for, who buys it, the theme around it
    "fix_common": True,       # fix the AI's usual mistakes (stray quotes, other stores, prices and codes as keywords,
                              # categories that don't fit) after Detect and Reword, and in the Shutterstock CSV
    "shutterstock_categories": [],  # the 1-2 categories put in the Shutterstock upload CSV (last used)
    "reword_fields": ["title", "description", "keywords"],  # Reword / Double-check: what it works on (last used)
    "reword_options": ["check"],    # ... the clean-up options ticked last time (keys from REWORD_OPTIONS)
    "reword_text": "",              # ... your own rewording instructions last time
    "reword_context": True,         # ... tell the AI the location and date in front of the title (never changed)
    "auto_download": False,   # download new versions without asking first (installed on restart)
    "stock_sites": None,      # sites whose title and keyword rules are checked (None = Shutterstock + Adobe Stock)
    "stock_sites_from_stockup": True,  # use the sites ticked in StockUp, when StockUp is set up on this computer
    "watch_folder": "",       # folder watched for new photos (e.g. Lightroom's export folder)
    "watch_on": False,        # watching it now (resumed when the app starts)
    "watch_detect": True,     # run Detect on new photos that have no title and keywords yet
}


# Debug log -------------------------------------------------------------------------------------
_debug = {"on": False, "lock": threading.Lock()}
DEBUG_LOG_MAX = 3_000_000   # bytes; the log is cut down to its newer half beyond this


def debug_log_path() -> str:
    return os.path.join(config_dir(), "debug_log.txt")


def set_debug(on: bool):
    was = _debug["on"]
    _debug["on"] = bool(on)
    if on and not was:
        import platform
        debug(f"===== debug mode on: {APP_NAME} {APP_VERSION}, Python {platform.python_version()}, "
              f"{platform.platform()} =====")


def debugging() -> bool:
    return _debug["on"]


def debug(msg: str, always: bool = False):
    """One line (or block) in the log (debug_log.txt), with the time and thread. Never raises.
    Only while Debug mode is on, except always=True (unexpected errors are always kept)."""
    if not (_debug["on"] or always):
        return
    try:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        who = threading.current_thread().name
        who = "window" if who == "MainThread" else "worker"
        path = debug_log_path()
        with _debug["lock"]:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > DEBUG_LOG_MAX:
                with open(path, "rb") as f:
                    f.seek(-DEBUG_LOG_MAX // 2, os.SEEK_END)
                    tail = f.read()
                with open(path, "wb") as f:
                    f.write(b"[... older lines removed to keep the file small ...]\n" + tail)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{stamp} [{who}] {msg}\n")
    except Exception:
        pass


def _clip(text, n=400) -> str:
    text = str(text).replace("\r", " ").replace("\n", "\\n")
    return text if len(text) <= n else text[:n] + f"… [{len(text) - n} more characters]"


# CSV export ---------------------------------------------------------------------------------------
CSV_FORMATS = [("shutterstock", "Shutterstock upload CSV"), ("adobe", "Adobe Stock upload CSV"),
               ("full", "All details (for Excel or your own records)")]

# The category names Shutterstock accepts in its upload CSV (1 or 2 per file; the column is required)
SHUTTERSTOCK_CATEGORIES = [
    "Abstract", "Animals/Wildlife", "Arts", "Backgrounds/Textures", "Beauty/Fashion", "Buildings/Landmarks",
    "Business/Finance", "Celebrities", "Education", "Food and drink", "Healthcare/Medical", "Holidays",
    "Industrial", "Interiors", "Miscellaneous", "Nature", "Objects", "Parks/Outdoor", "People", "Religion",
    "Science", "Signs/Symbols", "Sports/Recreation", "Technology", "Transportation", "Vintage",
]


def csv_stamp() -> str:
    """Today's date and the time, for CSV names: 2026-11-10_08-18 (sorts in order; no colons for Windows)."""
    return time.strftime("%Y-%m-%d_%H-%M")


def csv_file_name(fmt: str, day: str = "") -> str:
    """The suggested name for an exported CSV, with the date and time: shutterstock_2026-11-10_08-18.csv, so two
    exports on the same day never get mixed up or replace each other."""
    day = day or csv_stamp()
    return f"{'metadata' if fmt == 'full' else fmt}_{day}.csv"


def unused_path(path: str) -> str:
    """path, or 'name (2).csv', 'name (3).csv'… when it's taken: an earlier export is never replaced."""
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path, n = f"{base} ({n}){ext}", n + 1
    return path


def adobe_title(title: str, bare: str = "") -> str:
    """Adobe Stock's CSV title: plain text, no commas, at most 70 characters (cut at a word), and without an
    editorial location and date in front (Adobe asks for no editorial-style captions)."""
    text = plain_text(single_line(bare or title or ""))
    text = " ".join(text.replace(",", " ").replace(";", " ").split())
    if len(text) > 70:
        text = text[:71].rsplit(" ", 1)[0].rstrip(" -:–") if " " in text[:71] else text[:70]
    return text


def export_csv(path: str, rows: list[dict], fmt: str = "shutterstock", categories=(), ss_text: str = "title") -> int:
    """Write the photos to a CSV file. rows: dicts with file, folder, title, description, keywords,
    editorial (bool), and for 'full' also type, note, taken, saved. Returns how many photos were written.
    categories: the 1-2 Shutterstock categories for photos without their own r["categories"].
    ss_text: what goes in Shutterstock's one text column ("Description", shown on the site as the title):
    "title" (the default: the title as you see it in the list) or "description" (the longer caption, if any).
    The upload formats match what Shutterstock and Adobe Stock accept: one row per file, the keywords
    comma-separated in one cell. Columns they need that the app doesn't know (categories) are left
    empty to fill in on the site."""
    import csv
    # one keyword may never contain a comma: on upload it would split into two keywords
    rows = [dict(r, keywords=[" ".join(str(k).replace(",", " ").split()) for k in r["keywords"]]) for r in rows]
    desc = lambda r: single_line(r.get("description") or "") or r["title"]
    if fmt == "shutterstock":
        # exactly Shutterstock's sample file: these headers in this order, Yes/No capitalised, and 1-2
        # categories (an empty Categories column gets the whole CSV rejected)
        default = shutterstock_categories(list(categories or []))
        cats = lambda r: ",".join(shutterstock_categories(r.get("categories")) or default)
        head = ["Filename", "Description", "Keywords", "Categories", "Illustration", "Mature Content", "Editorial"]
        ss = (lambda r: single_line(r["title"]) or desc(r)) if ss_text != "description" else desc
        # Shutterstock asks for no special characters: curly quotes, long dashes and accents are always made
        # plain here, whatever the Symbols setting
        body = [[r["file"], plain_text(ss(r)), ",".join(k for k in (plain_text(x) for x in r["keywords"]) if k),
                 cats(r), "No", "No", "Yes" if r.get("editorial") else "No"] for r in rows]
        enc = "utf-8"
    elif fmt == "adobe":
        head = ["Filename", "Title", "Keywords", "Category", "Releases"]
        body = [[r["file"], adobe_title(r["title"], r.get("bare_title", "")),
                 ",".join(k for k in (plain_text(x) for x in r["keywords"]) if k), r.get("adobe_category") or "", ""]
                for r in rows]
        enc = "utf-8"
    else:
        head = ["File", "Folder", "Title", "Title length", "Description", "Description length", "Keywords",
                "Keyword count", "Photo type", "Photo notes", "Date taken", "Editorial", "Saved"]
        body = [[r["file"], r["folder"], r["title"], len(r["title"]), desc(r), len(desc(r)),
                 ", ".join(r["keywords"]), len(r["keywords"]),
                 r.get("type", ""), r.get("note", ""), r.get("taken", ""), "yes" if r.get("editorial") else "no",
                 "yes" if r.get("saved") else "no"] for r in rows]
        enc = "utf-8-sig"                            # so Excel shows accents and other characters correctly
    with open(path, "w", newline="", encoding=enc) as f:   # (the save dialog already asked before replacing)
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(body)
    return len(rows)


# Update check ------------------------------------------------------------------------------------
UPDATE_REPO = "kefirman9000/iptc-tagger"
UPDATE_API = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{UPDATE_REPO}/releases/latest"


def version_tuple(v: str) -> tuple:
    """'v2026.10.27.4' -> (2026, 10, 27, 4). Anything that isn't a number counts as 0."""
    parts = re.findall(r"\d+", str(v or ""))
    return tuple(int(p) for p in parts) if parts else (0,)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return version_tuple(latest) > version_tuple(current)


def check_for_update(current: str = APP_VERSION, opener=None, timeout: float = 6,
                     status: dict | None = None) -> dict | None:
    """Ask GitHub for the latest release. Returns {'version', 'url', 'notes'} if it's newer than
    `current`, else None. Only the public release info is fetched; nothing is sent about you or your
    photos. Any problem (offline, GitHub unreachable, no releases) just means 'no update'.
    `status`, if given, gets 'reached' (could GitHub be asked?) and 'latest' (its newest version)."""
    if status is not None:
        status.update(reached=False, latest="")
    req = urllib.request.Request(UPDATE_API, headers={"User-Agent": f"{APP_NAME}/{current}",
                                                     "Accept": "application/vnd.github+json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        debug(f"update check: couldn't reach GitHub ({e.__class__.__name__}: {e})")
        return None
    tag = str(info.get("tag_name") or "")
    version = tag.lstrip("vV")
    debug(f"update check: running {current}, latest release {version or '?'}")
    if status is not None:
        status.update(reached=True, latest=version)
    if not version or info.get("draft") or info.get("prerelease") or not is_newer(version, current):
        return None
    assets = {a.get("name", ""): a.get("browser_download_url", "") for a in info.get("assets") or []
              if a.get("name") and a.get("browser_download_url")}
    return {"version": version, "url": info.get("html_url") or RELEASES_PAGE, "notes": info.get("body") or "",
            "assets": assets}


# Automatic updates ---------------------------------------------------------------------------------
def update_asset(assets: dict, windows: bool = os.name == "nt", mac: bool = sys.platform == "darwin") -> str:
    """The download for this computer from a release's files ('IPTC.Tagger.Windows.zip' / '...Mac.zip')."""
    want = "windows" if windows else "mac" if mac else None
    if not want:
        return ""
    return next((url for name, url in (assets or {}).items()
                 if want in name.casefold() and name.casefold().endswith(".zip")), "")


def program_root() -> str:
    """The installed program to replace: the folder holding the .exe (Windows) or the .app bundle (Mac).
    '' when running from the source code (nothing to replace)."""
    if not FROZEN:
        return ""
    if sys.platform == "darwin":
        d = APP_DIR
        while d and not d.endswith(".app") and os.path.dirname(d) != d:
            d = os.path.dirname(d)
        return d if d.endswith(".app") else ""
    return APP_DIR


def can_self_update() -> bool:
    root = program_root()
    return bool(root) and os.access(os.path.dirname(root) if root.endswith(".app") else root, os.W_OK)


def download_update(url: str, dest: str, progress=None, cancel=None, opener=None) -> str:
    """Download the release zip into the folder `dest`. progress(fraction or None, text) while it goes."""
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, "update.zip")
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with (opener or urllib.request.urlopen)(req, timeout=60) as resp, open(path, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0) if hasattr(resp, "headers") else 0
        done = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise ApiError("Download stopped.")
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done / total if total else None, f"{done / 1048576:.0f} MB")
    debug(f"update: downloaded {done} bytes from {url}")
    return path


def unpack_update(zip_path: str, dest: str, windows: bool = os.name == "nt") -> str:
    """Unzip the download and return the new program inside it (the folder with IPTC Tagger.exe, or the
    IPTC Tagger.app bundle). Raises ApiError if it doesn't look right, so nothing is installed."""
    out = os.path.join(dest, "new")
    if not windows and sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", zip_path, out], check=True)   # keeps the app bundle intact
    else:
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                raise ApiError(f"the download is damaged ({bad})")
            z.extractall(out)
    for root, dirs, files in os.walk(out):
        if windows and f"{APP_NAME}.exe" in files:
            return root
        if not windows and f"{APP_NAME}.app" in dirs:
            return os.path.join(root, f"{APP_NAME}.app")
    raise ApiError("the download doesn't contain the program")


def updater_script(new_root: str, app_root: str, work: str, pid: int, relaunch: bool,
                   windows: bool = os.name == "nt") -> str:
    """A small script that waits for this app to close, puts the new version in place of the old one and
    (optionally) starts it again. Runs on its own after the app has quit."""
    if windows:
        exe = app_root.rstrip("\\/") + "\\" + f"{APP_NAME}.exe"     # always a Windows path
        return "\r\n".join([
            "@echo off", "chcp 65001 >nul",
            ":wait",                                         # until the app has closed
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
            "if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto wait )",
            f'robocopy "{new_root}" "{app_root}" /E /R:5 /W:1 /NFL /NDL /NJH /NJS /NP >nul',
            f'if errorlevel 8 ( echo The update could not be copied into "{app_root}". > "{work}.failed.txt" )',
            *([f'start "" "{exe}"'] if relaunch else []),
            f'rd /s /q "{work}"',
            '(goto) 2>nul & del "%~f0"', ""])
    q = lambda p: "'" + p.replace("'", "'\\''") + "'"
    return "\n".join([
        "#!/bin/bash",
        f"while kill -0 {pid} 2>/dev/null; do sleep 1; done",
        f"rm -rf {q(app_root + '.old')}",
        f"if mv {q(app_root)} {q(app_root + '.old')} && ditto {q(new_root)} {q(app_root)}; then",
        f"  rm -rf {q(app_root + '.old')}",
        "else",
        f"  rm -rf {q(app_root)}; mv {q(app_root + '.old')} {q(app_root)}",   # put the old one back
        "fi",
        f"xattr -dr com.apple.quarantine {q(app_root)} 2>/dev/null",
        *([f"open {q(app_root)}"] if relaunch else []),
        f"rm -rf {q(work)}",
        'rm -- "$0"', ""])


def start_updater(script_text: str, work: str, windows: bool = os.name == "nt") -> str:
    """Write the updater script next to the download and start it, detached from this app."""
    path = os.path.join(os.path.dirname(work), f"iptc_tagger_update.{'cmd' if windows else 'sh'}")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(script_text)
    if windows:
        subprocess.Popen(["cmd", "/c", path], creationflags=0x08000000 | 0x00000200,   # no window, own group
                         close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        os.chmod(path, 0o755)
        subprocess.Popen(["/bin/bash", path], start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    debug(f"update: started the updater {path}")
    return path


# What's new ------------------------------------------------------------------------------------------
CHANGELOG = os.path.join(RESOURCE_DIR, "CHANGELOG.md")


def changelog_sections(text: str) -> list[tuple[str, str]]:
    """[(version, markdown)] from CHANGELOG.md, newest first ('## 2026.10.27.6' headings)."""
    out, version, lines = [], None, []
    for line in (text or "").splitlines():
        m = re.match(r"^##\s+v?(\d[\w.]*)\s*$", line)
        if m:
            if version:
                out.append((version, "\n".join(lines).strip()))
            version, lines = m.group(1), []
        elif version:
            lines.append(line)
    if version:
        out.append((version, "\n".join(lines).strip()))
    return out


def whats_new(since: str = "", current: str = APP_VERSION, full: bool = False, text: str | None = None) -> str:
    """Markdown for the versions after `since` up to `current` (all of them with full=True)."""
    if text is None:
        try:
            with open(CHANGELOG, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return ""
    parts = []
    for version, body in changelog_sections(text):
        if version_tuple(version) > version_tuple(current):
            continue
        if not full and since and version_tuple(version) <= version_tuple(since):
            continue
        if not full and not since and parts:
            break                                    # first run: just the current version
        parts.append(f"### {version}\n\n{body}")
    return "\n\n".join(parts)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    raw = {}
    try:
        with open(os.path.join(config_dir(), "settings.json"), encoding="utf-8") as f:
            raw = json.load(f)
            s.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError, AttributeError):
        pass
    if isinstance(raw, dict) and raw.get("title_as_description") is False and not isinstance(raw.get("meta_fields"), list):
        # an older "don't save the title as the description" choice becomes a metadata field choice
        s["meta_fields"] = [k for k in DEFAULT_META_FIELDS if k != "description"]
    if s.get("default_type") == "general":    # "General photo" was merged into "Automatic"
        s["default_type"] = "auto"
    if isinstance(raw, dict) and raw and "instructions_merged" not in raw:
        # "About these photos" and "Title & keyword direction" are now one box: join what was in them
        parts = [str(raw.get(k) or "").strip() for k in ("context", "direction")]
        joined = "\n".join(p for p in parts if p)
        s["context"] = s["direction"] = joined
    if isinstance(raw, dict) and raw and "iptc_direct" not in raw:
        # saving is one step now: the full title and keywords go straight into IPTC, so the second
        # xmp2iptc pass is no longer needed for that (it stays available in Settings)
        s["xmp2iptc_on_save"] = False
    if isinstance(raw, dict) and raw and "editorial_on" not in raw:
        # settings from before the Editorial checkbox: it's on if a location or date was being added
        s["editorial_on"] = s.get("title_date_pos", "off") != "off" or bool(str(s.get("title_location") or "").strip())
        s["editorial_pos"] = "after" if s.get("title_date_pos") == "end" else "before"
    if s.get("editorial_pos") not in ("before", "after"):
        s["editorial_pos"] = "before"
    if not str(s.get("model") or "").strip():
        s["model"] = DEFAULT_MODEL
    if not str(s.get("ollama_url") or "").strip():
        s["ollama_url"] = DEFAULT_OLLAMA_URL
    s["keyword_min"], s["keyword_max"] = keyword_range(s.get("keyword_min"), s.get("keyword_max"))
    if s.get("title_length") not in {k for k, _, _, _ in TITLE_LENGTHS}:
        s["title_length"] = "detailed"
    if s.get("symbols") not in {k for k, _ in SYMBOL_MODES}:
        s["symbols"] = DEFAULT_SETTINGS["symbols"]
    if not isinstance(s.get("location_history"), list):
        s["location_history"] = []
    s["detect_style"] = style_keys(s.get("detect_style") if isinstance(s.get("detect_style"), list) else [])
    s["text_checks"] = text_checks_keys(s.get("text_checks") if isinstance(s.get("text_checks"), list)
                                        else DEFAULT_TEXT_CHECKS)
    if not isinstance(s.get("profiles"), dict):
        s["profiles"] = {}
    s["profiles"] = {str(k): v for k, v in s["profiles"].items() if isinstance(v, dict) and str(k).strip()}
    if s.get("title_caps") not in ("title", "every"):
        s["title_caps"] = "title"
    try:
        s["ai_time_limit"] = max(0, min(900, int(s.get("ai_time_limit", 30))))
        s["ai_retries"] = max(0, min(2, int(s.get("ai_retries", 1))))
    except (TypeError, ValueError):
        s["ai_time_limit"], s["ai_retries"] = 30, 1
    if not format_date(str(s.get("custom_date") or ""), "iso"):
        s["custom_date"], s["custom_date_on"] = "", False
    for key, options in (("title_date_pos", DATE_POSITIONS), ("title_date_format", DATE_FORMATS),
                         ("title_date_sep", DATE_SEPARATORS), ("title_loc_sep", LOCATION_SEPARATORS)):
        if s.get(key) not in {k for k, _ in options}:
            s[key] = DEFAULT_SETTINGS[key]
    return s


def keyword_range(lo, hi) -> tuple[int, int]:
    """A sane keyword range: 5..50, with min <= max."""
    try:
        lo = int(lo)
    except (TypeError, ValueError):
        lo = DEFAULT_KEYWORD_MIN
    try:
        hi = int(hi)
    except (TypeError, ValueError):
        hi = DEFAULT_KEYWORD_MAX
    lo, hi = max(5, min(lo, 50)), max(5, min(hi, 50))
    return min(lo, hi), max(lo, hi)


def save_settings(s: dict) -> None:
    d = config_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "settings.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: s.get(k, v) for k, v in DEFAULT_SETTINGS.items()}, f, indent=2)
    try:
        os.chmod(tmp, 0o600)   # your settings: readable by you only
    except OSError:
        pass
    os.replace(tmp, path)



# --------------------------------------------------------------------------- ExifTool

# --------------------------------------------------------------------------- AI markers
# Lightroom / Camera Raw develop settings live in XMP-crs (and XMP-crss for snapshots). In a JPEG they say
# nothing to a buyer, but they do record AI tools: "Mask/Image" corrections are Select Subject / Sky /
# People masks, and Enhance* settings are AI Denoise, Super Resolution and Raw Details.
STRIP_DEVELOP_ARGS = ["-XMP-crs:all=", "-XMP-crss:all="]

AI_MARKER_TAGS = ["-XMP-iptcExt:DigitalSourceType", "-JUMBF:all", "-XMP-crs:all",
                  "-EXIF:Software", "-XMP-xmp:CreatorTool", "-XMP-xmpMM:HistorySoftwareAgent"]

AI_SOFTWARE = re.compile(r"firefly|generative|midjourney|dall[\s·-]?e|stable diffusion|topaz photo ai|gigapixel|"
                         r"luminar|imagen|chatgpt|openai", re.I)


def ai_markers(row: dict) -> list[str]:
    """What in a file's metadata (an ExifTool -j -G1 row) says AI made or edited it. Stock sites read
    these, so a photo carrying one can be refused as AI generated."""
    found = []

    def add(text):
        if text not in found:
            found.append(text)

    def values(v):
        return [str(x) for x in v] if isinstance(v, list) else ([] if v is None else [str(v)])

    for key, v in row.items():
        g, _, tag = key.partition(":")
        low = " ".join(values(v)).lower()
        if tag == "DigitalSourceType":
            flat = low.replace(" ", "")
            if "algorithmicmedia" in flat or "trained" in flat:
                add("Marked as made or edited with AI (Digital Source Type: " + values(v)[0].rsplit("/", 1)[-1] + ")")
        elif g == "JUMBF" or "c2pa" in key.lower():
            add("Has Content Credentials (C2PA), which can record AI tools")
        elif g in ("XMP-crs", "XMP-crss"):
            if "What" in tag and "mask/image" in low:
                add("Lightroom AI mask (Select Subject, Sky or People) in its develop settings")
            elif tag.startswith("Enhance") and low not in ("", "0", "false", "none"):
                add("Lightroom AI Enhance (Denoise, Super Resolution or Raw Details) in its develop settings")
        elif tag in ("Software", "CreatorTool", "HistorySoftwareAgent"):
            m = AI_SOFTWARE.search(low)
            if m:
                add("Made or edited with " + m.group(0).title())
    return found


class ExifToolError(Exception):
    pass


def find_exiftool(custom: str = "") -> str | None:
    """Locate ExifTool. Apps opened from the Finder don't see Homebrew's PATH,
    so the usual install folders are checked too."""
    cands = []
    if custom:
        cands.append(custom)
    here = APP_DIR
    if os.name == "nt":
        for d in (here, os.path.join(here, "exiftool"), RESOURCE_DIR):    # next to the app, or bundled with it
            cands += [os.path.join(d, n) for n in ("exiftool.exe", "exiftool(-k).exe")]
        cands += [shutil.which("exiftool") or "", shutil.which("exiftool.exe") or ""]
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", ""),
                     os.environ.get("ProgramFiles(x86)", "")):
            if base:
                cands += [os.path.join(base, "ExifTool", "exiftool.exe")]
        cands += [r"C:\Windows\exiftool.exe"]
    else:
        cands += [shutil.which("exiftool") or "", "/opt/homebrew/bin/exiftool",
                  "/usr/local/bin/exiftool", "/usr/bin/exiftool", os.path.join(here, "exiftool"),
                  os.path.join(here, "exiftool", "exiftool")]
    import glob
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    names = ("exiftool.exe", "exiftool(-k).exe") if os.name == "nt" else ("exiftool",)
    for folder in sorted(glob.glob(os.path.join(downloads, "*xiftool*")), reverse=True):   # newest version first
        cands += [os.path.join(folder, n) for n in names]
    for c in cands:
        if c and os.path.isfile(c) and (os.name == "nt" or os.access(c, os.X_OK)):
            return c
    return None


BUNDLED_XMP2IPTC = os.path.join(RESOURCE_DIR, "arg_files", "xmp2iptc.args")
APP_ICON = os.path.join(RESOURCE_DIR, "icon.png")


def find_xmp2iptc_args(exiftool_exe: str | None, custom: str = "") -> str | None:
    """Locate ExifTool's xmp2iptc.args: your chosen file, next to ExifTool,
    in ExifTool's own arg_files folder, or the copy bundled with this app."""
    cands = [custom] if custom else []
    if exiftool_exe:
        real = os.path.realpath(exiftool_exe)
        for base in {os.path.dirname(exiftool_exe), os.path.dirname(real)}:
            d = base
            for _ in range(4):                       # walk up a few levels (Homebrew keeps it in libexec)
                cands += [os.path.join(d, "xmp2iptc.args"), os.path.join(d, "arg_files", "xmp2iptc.args"),
                          os.path.join(d, "exiftool_files", "arg_files", "xmp2iptc.args"),
                          os.path.join(d, "libexec", "arg_files", "xmp2iptc.args")]
                d = os.path.dirname(d)
    cands.append(BUNDLED_XMP2IPTC)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


# Metadata fields ---------------------------------------------------------------------------------
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def us_state(word: str) -> str:
    """'TX' or 'Texas' -> 'Texas'; anything else -> ''."""
    w = (word or "").strip().rstrip(".")
    if w.upper() in US_STATES and len(w) == 2:
        return US_STATES[w.upper()]
    return next((s for s in US_STATES.values() if s.lower() == w.lower()), "")


# Colour labels, as in Lightroom (keys 6-9) ---------------------------------------------------------------------
LABELS = [("red", "Red", "#e5484d", "6"), ("yellow", "Yellow", "#e8b500", "7"), ("green", "Green", "#30a46c", "8"),
          ("blue", "Blue", "#3b82f6", "9"), ("purple", "Purple", "#8e4ec6", "")]
LABEL_NAMES = {k: name for k, name, _c, _key in LABELS}
LABEL_COLORS = {k: c for k, _n, c, _key in LABELS}


def color_label(value) -> str:
    """The XMP Label as saved by Lightroom ('Red', 'Yellow'…) -> 'red'; anything else (a custom label set) -> ''."""
    if isinstance(value, list):
        value = value[0] if value else ""
    v = str(value or "").strip().lower()
    return v if v in LABEL_NAMES else ""


def parse_location(location: str) -> dict:
    """'City, State, Country' -> parts. 'Tokyo, Japan' -> city + country; one part -> city. A US state as the
    second part ('Austin, Texas' or 'Austin, TX') is the state, and the country is United States."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        return {"city": "", "state": "", "country": ""}
    if len(parts) == 1:
        return {"city": parts[0], "state": "", "country": ""}
    if len(parts) == 2:
        if us_state(parts[1]) and parts[1].lower() != "georgia":      # (Tbilisi, Georgia is the country)
            return {"city": parts[0], "state": parts[1], "country": "United States"}
        return {"city": parts[0], "state": "", "country": parts[1]}
    return {"city": parts[0], "state": ", ".join(parts[1:-1]), "country": parts[-1]}


def _set(tag: str, value: str) -> list[str]:
    return [f"-{tag}={value}"]


def _kw_args(tag: str, keywords, limit: int | None = None) -> list[str]:
    if not keywords:
        return [f"-{tag}="]
    ks = clean_keywords(truncate_utf8(k, limit) for k in keywords) if limit else keywords
    return [f"-{tag}={k}" for k in ks]


def _location_args(location: str) -> list[str]:
    loc = parse_location(location)
    if not any(loc.values()):
        return []                                   # no location set: leave the photo's own fields alone
    args = []
    for xmp, iptc, value in (("XMP-photoshop:City", "IPTC:City", loc["city"]),
                             ("XMP-photoshop:State", "IPTC:Province-State", loc["state"]),
                             ("XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName", loc["country"])):
        if value:
            args += _set(xmp, value) + _set(iptc, truncate_utf8(value, 64 if "Country" not in iptc else 64))
    return args


# Each field: what it is, where it's written, and whether it's on unless you change it in Settings.
METADATA_FIELDS = [
    {"key": "title_xmp", "group": "Title", "label": "XMP Title (dc:title)", "default": True,
     "tip": "The standard title field. Read by Lightroom, Bridge, Adobe Stock, Windows and most stock sites.",
     "args": lambda t, k, d, l: _set("XMP-dc:Title", t)},
    {"key": "title_iptc", "group": "Title", "label": "IPTC Object Name", "default": True,
     "tip": "The older IPTC title field. Still read by many sites and tools. The full title is written,\n"
            "even past the official 64-byte limit, as stock sites expect.",
     "args": lambda t, k, d, l: _set("IPTC:ObjectName", t)},
    {"key": "headline", "group": "Title", "label": "Headline (XMP and IPTC)", "default": True,
     "tip": "A short headline. Editorial agencies and Photo Mechanic use it as the title.",
     "args": lambda t, k, d, l: _set("XMP-photoshop:Headline", t) + _set("IPTC:Headline", t)},
    {"key": "title_windows", "group": "Title", "label": "Windows Title (XPTitle)", "default": False,
     "tip": "The title Windows File Explorer shows in some older versions. Usually not needed.",
     "args": lambda t, k, d, l: _set("EXIF:XPTitle", t)},
    {"key": "description", "group": "Description", "label": "Description / Caption (XMP and IPTC)", "default": True,
     "tip": "The photo's description (caption). Alamy, Dreamstime and Shutterstock read it from here.\n"
            "When a photo has no description of its own, its title is saved here instead.",
     "args": lambda t, k, d, l: _set("XMP-dc:Description", d) + _set("IPTC:Caption-Abstract", truncate_utf8(d, 2000))},
    {"key": "description_exif", "group": "Description", "label": "EXIF Image Description", "default": False,
     "tip": "The camera-style description field. Some older programs read only this one.",
     "args": lambda t, k, d, l: _set("EXIF:ImageDescription", d)},
    {"key": "keywords_xmp", "group": "Keywords", "label": "XMP Keywords (dc:subject)", "default": True,
     "tip": "The standard keyword field. Read by Lightroom, Bridge, Adobe Stock, Windows and most stock sites.",
     "args": lambda t, k, d, l: _kw_args("XMP-dc:Subject", k)},
    {"key": "keywords_iptc", "group": "Keywords", "label": "IPTC Keywords", "default": True,
     "tip": "The older IPTC keyword field. Still read by many sites and tools. Long keywords are written\n"
            "in full, even past the official 64-byte limit.",
     "args": lambda t, k, d, l: _kw_args("IPTC:Keywords", k)},
    {"key": "keywords_windows", "group": "Keywords", "label": "Windows Tags (XPKeywords)", "default": False,
     "tip": "The tags Windows File Explorer shows in some older versions. Usually not needed.",
     "args": lambda t, k, d, l: _set("EXIF:XPKeywords", "; ".join(k))},
    {"key": "location", "group": "Location", "label": "City, State and Country (XMP and IPTC)", "default": True,
     "tip": "From 'Location in title', read as City, State, Country (e.g. Tokyo, Japan). Editorial reviewers\n"
            "check these. Only written when a location is set; the photo's own fields are left alone otherwise.",
     "args": lambda t, k, d, l: _location_args(l)},
]
DEFAULT_META_FIELDS = [f["key"] for f in METADATA_FIELDS if f["default"]]


def meta_fields(settings: dict) -> list[str]:
    """The fields to write, from Settings (older settings files are converted)."""
    chosen = settings.get("meta_fields")
    if not isinstance(chosen, list):
        chosen = list(DEFAULT_META_FIELDS)
    known = {f["key"] for f in METADATA_FIELDS}
    return [k for k in chosen if k in known]


def xmp_only(args: list[str]) -> list[str]:
    """Drop the IPTC and EXIF writes from an ExifTool argument list: a .xmp sidecar only holds XMP."""
    keep = []
    for a in args:
        if a.startswith(("-IPTC:", "-EXIF:")) or a in ("-charset", "iptc=UTF8"):
            continue
        keep.append(a)
    return keep


def wrote_one(out: str) -> bool:
    """ExifTool's report says it updated (or created, for a new sidecar) the one file."""
    return any(f"1 image files {w}" in out for w in ("updated", "unchanged", "created"))


class _NotOpen(Exception):
    """The ExifTool kept running in the background is gone: run this command the one-off way."""


class ExifTool:
    """Runs ExifTool so any file name or Unicode text is safe. One ExifTool is kept running in the background
    (-stay_open) and given one command after another, which saves starting it for every read and save (slow on
    Windows). If that ever goes wrong, the command runs the one-off way with an argument file instead."""

    def __init__(self, exe: str, persistent: bool = True):
        self.exe = exe
        # "exiftool(-k).exe" waits for a key press when it ends: only ever run that one the one-off way
        self.persistent = persistent and "(-k)" not in os.path.basename(exe or "")
        self._proc = None
        self._lock = threading.Lock()
        self._seq = 0
        self._out = self._err = None
        self._failures = 0

    # ----- kept running
    def _start(self):
        import atexit
        import queue
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        self._proc = subprocess.Popen([self.exe, "-stay_open", "True", "-@", "-"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
        self._out, self._err = queue.Queue(), queue.Queue()

        def pump(stream, q):
            for line in iter(stream.readline, b""):
                q.put(line)
            q.put(None)                                    # it ended
        for stream, q in ((self._proc.stdout, self._out), (self._proc.stderr, self._err)):
            threading.Thread(target=pump, args=(stream, q), daemon=True).start()
        atexit.register(self.close)
        debug(f"ExifTool kept running in the background (pid {self._proc.pid})")

    def _collect(self, q, marker: bytes, deadline: float) -> str:
        import queue
        lines = []
        while True:
            try:
                line = q.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                raise ExifToolError("ExifTool took too long and was stopped")
            if line is None:
                raise _NotOpen()
            if line.rstrip(b"\r\n") == marker:
                return b"".join(lines).decode("utf-8", "replace")
            lines.append(line)

    def _run_open(self, args: list[str], timeout: int) -> str:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            self._seq += 1
            marker = f"{{ready{self._seq}}}"
            lines = ["-charset", "filename=utf8"] + args + ["-echo4", marker, f"-execute{self._seq}"]
            try:
                self._proc.stdin.write(("\n".join(lines) + "\n").encode("utf-8"))
                self._proc.stdin.flush()
            except OSError:
                raise _NotOpen()
            deadline = time.monotonic() + timeout
            try:
                out = self._collect(self._out, marker.encode(), deadline)
                err = self._collect(self._err, marker.encode(), deadline).strip()
            except ExifToolError:
                self._kill()                               # stuck: start a fresh one next time
                raise
        errors = [ln for ln in err.splitlines() if ln.startswith("Error")]
        if errors:
            raise ExifToolError(errors[0].replace("Error: ", "", 1))
        return out

    def _kill(self):
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def close(self):
        """Let the background ExifTool finish (when the app closes), after the command it's on."""
        got = self._lock.acquire(timeout=30)
        try:
            proc, self._proc = self._proc, None
        finally:
            if got:
                self._lock.release()
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(b"-stay_open\nFalse\n")
            proc.stdin.flush()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass

    def _run(self, args: list[str], timeout: int = 180) -> str:
        for a in args:
            if "\n" in a or "\r" in a:
                raise ExifToolError("line breaks are not allowed in metadata values")
        if self.persistent:
            try:
                return self._run_open(args, timeout)
            except _NotOpen:
                self._kill()
                self._failures += 1
                debug(f"ExifTool in the background stopped answering ({self._failures}x): running it the one-off way")
                if self._failures >= 3:
                    self.persistent = False                # keeps happening: stay with the one-off way
        return self._run_once(args, timeout)

    def _run_once(self, args: list[str], timeout: int = 180) -> str:
        fd, argfile = tempfile.mkstemp(suffix=".args", prefix="iptc_tagger_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                for a in ["-charset", "filename=utf8"] + args:
                    f.write(a + "\n")
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000   # CREATE_NO_WINDOW: no console flashing up
            # stdin=DEVNULL: a windowed program has no console input, which can stop it starting on Windows
            proc = subprocess.run([self.exe, "-@", argfile], capture_output=True, stdin=subprocess.DEVNULL,
                                  timeout=timeout, **kw)
        except subprocess.TimeoutExpired:
            raise ExifToolError("ExifTool took too long and was stopped")
        except OSError as e:
            raise ExifToolError(f"could not start ExifTool: {e}")
        finally:
            try:
                os.remove(argfile)
            except OSError:
                pass
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace").strip()
        errors = [ln for ln in err.splitlines() if ln.startswith("Error")]
        if errors:
            raise ExifToolError(errors[0].replace("Error: ", "", 1))
        if proc.returncode not in (0, 1):
            raise ExifToolError(err or f"ExifTool failed (code {proc.returncode})")
        return out

    def version(self) -> str:
        return self._run(["-ver"], timeout=30).strip()

    def binary(self, tag: str, path: str) -> bytes:
        """One binary tag (an embedded preview) read straight from a file. Read-only."""
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        try:
            proc = subprocess.run([self.exe, "-charset", "filename=utf8", "-b", "-" + tag, path],
                                  capture_output=True, stdin=subprocess.DEVNULL, timeout=60, **kw)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise ExifToolError(f"couldn't read the preview: {e}")
        return proc.stdout or b""

    def read(self, paths: list[str]) -> dict[str, dict]:
        """Current title and keywords for each file, keyed by the path given. For a RAW file they come from its
        .xmp sidecar when it has one (the date taken still comes from the RAW itself, read-only)."""
        raws = [p for p in paths if is_raw(p)]
        if not raws:
            return self._read_files(paths)
        result = self._read_files(paths)
        cars = {p: sidecar_path(p) for p in raws if os.path.isfile(sidecar_path(p))}
        if cars:
            from_cars = self._read_files(list(cars.values()))
            for raw, car in cars.items():
                meta = from_cars.get(car) or {}
                result[raw] = {"title": meta.get("title", ""), "description": meta.get("description", ""),
                               "keywords": list(meta.get("keywords", [])), "label": meta.get("label", ""),
                               "taken": result.get(raw, {}).get("taken", "") or meta.get("taken", "")}
        for raw in raws:
            if raw not in cars:                  # no sidecar yet: nothing of ours, only the date taken
                taken = result.get(raw, {}).get("taken", "")
                result[raw] = {"title": "", "description": "", "keywords": [], "taken": taken}
        return result

    def _read_files(self, paths: list[str]) -> dict[str, dict]:
        result = {}
        for start in range(0, len(paths), 200):
            chunk = paths[start:start + 200]
            out = self._run(["-j", "-G1", "-a", "-api", "struct=0",
                             "-XMP-dc:Title", "-IPTC:ObjectName",
                             "-XMP-dc:Description", "-IPTC:Caption-Abstract",
                             "-XMP-dc:Subject", "-IPTC:Keywords", "-XMP-xmp:Label",
                             "-EXIF:DateTimeOriginal", "-XMP-exif:DateTimeOriginal", "-EXIF:CreateDate"]
                            + AI_MARKER_TAGS + chunk)
            by_norm = {_norm(p): p for p in chunk}
            try:
                rows = json.loads(out) if out.strip() else []
            except ValueError:
                rows = []
            for row in rows:
                src = by_norm.get(_norm(row.get("SourceFile", "")))
                if not src:
                    continue

                def as_list(v):
                    if v is None:
                        return []
                    return [str(x) for x in v] if isinstance(v, list) else [str(v)]

                title = row.get("XMP-dc:Title") or row.get("IPTC:ObjectName") or ""
                if isinstance(title, list):
                    title = title[0] if title else ""
                desc = row.get("XMP-dc:Description") or row.get("IPTC:Caption-Abstract") or ""
                if isinstance(desc, list):
                    desc = desc[0] if desc else ""
                xmp_kws = as_list(row.get("XMP-dc:Subject"))
                # an IPTC keyword that is just the 64-byte-trimmed copy of a longer XMP keyword is the same keyword
                iptc_kws = [k for k in as_list(row.get("IPTC:Keywords"))
                            if not (len(k.encode("utf-8")) >= IPTC_KEYWORD_MAX - 4 and
                                    any(x.casefold() != k.casefold() and x.casefold().startswith(k.casefold())
                                        for x in xmp_kws))]
                kws = merge_keywords(xmp_kws, iptc_kws)
                taken = ""
                for key in ("ExifIFD:DateTimeOriginal", "XMP-exif:DateTimeOriginal", "ExifIFD:CreateDate"):
                    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})", str(row.get(key, "")))
                    if m and m.group(1) != "0000":
                        taken = "-".join(m.groups())
                        break
                result[src] = {"title": str(title), "description": str(desc), "keywords": kws, "taken": taken,
                               "label": color_label(row.get("XMP-xmp:Label")), "ai_markers": ai_markers(row)}
            for p in chunk:
                result.setdefault(p, {"title": "", "description": "", "keywords": [], "taken": ""})
        return result

    def xmp_to_iptc(self, path: str, args_file: str) -> None:
        """Copy XMP to IPTC with ExifTool's xmp2iptc.args: -overwrite_original -P -m
        -charset iptc=utf8 -codedcharacterset=utf8 -tagsFromFile @ -@ xmp2iptc.args"""
        out = self._run(["-overwrite_original", "-P", "-m", "-charset", "iptc=utf8", "-codedcharacterset=utf8",
                         "-tagsFromFile", "@", "-@", args_file, path])
        if "1 image files updated" not in out and "1 image files unchanged" not in out:
            raise ExifToolError("the XMP to IPTC conversion didn't update the file: " + (out.strip() or "no output"))

    def write(self, path: str, title: str, keywords: list[str], description: str | None = None,
              fields=None, location: str = "", label: str | None = None, strip_develop: bool = False) -> None:
        """Replace title and keywords (and the other chosen fields, see METADATA_FIELDS). Only metadata
        segments are rewritten; the image data is copied unchanged. -P keeps the file's modified date, and
        -overwrite_original stops ExifTool leaving a '_original' copy behind.
        fields=None keeps the original behaviour: title + keywords in XMP and IPTC, plus the description
        only if one is given."""
        title = clean_title(title)
        keywords = clean_keywords(keywords)
        if fields is None:
            fields = {"title_xmp", "title_iptc", "keywords_xmp", "keywords_iptc"} | (
                {"description"} if description is not None else set())
        fields = set(fields)
        # an own description keeps its full stop (clean_title would take it off); without one, the title is used
        desc = norm_description(description) if (description or "").strip() else title
        # -m lets IPTC values go past the official length limits (64 bytes for the title and each keyword), so
        # IPTC gets the full title in this one step, the same as xmp2iptc.args used to give it in a second pass
        args = ["-m", "-P", "-overwrite_original", "-charset", "iptc=UTF8", "-IPTC:CodedCharacterSet=UTF8"]
        for f in METADATA_FIELDS:
            if f["key"] in fields:
                args += f["args"](title, keywords, desc, location)
        if label is not None:                        # the colour label, as Lightroom and Bridge keep it
            args += [f"-XMP-xmp:Label={LABEL_NAMES.get(label, '')}"]
        if path.lower().endswith(".xmp"):
            args = xmp_only(args)
        elif strip_develop:                          # Lightroom/Camera Raw develop settings, AI masks included
            args += STRIP_DEVELOP_ARGS
        out = self._run(args + [path])
        if not wrote_one(out):
            raise ExifToolError(out.strip() or "ExifTool did not update the file")


# Undo a save: every tag a save can touch (as ExifTool reports it with -G1 -> the name to write it back)
UNDO_TAGS = [("XMP-xmp:Label", "XMP-xmp:Label"), ("XMP-dc:Title", "XMP-dc:Title"), ("IPTC:ObjectName", "IPTC:ObjectName"),
             ("XMP-photoshop:Headline", "XMP-photoshop:Headline"), ("IPTC:Headline", "IPTC:Headline"),
             ("IFD0:XPTitle", "EXIF:XPTitle"),
             ("XMP-dc:Description", "XMP-dc:Description"), ("IPTC:Caption-Abstract", "IPTC:Caption-Abstract"),
             ("IFD0:ImageDescription", "EXIF:ImageDescription"),
             ("XMP-dc:Subject", "XMP-dc:Subject"), ("IPTC:Keywords", "IPTC:Keywords"),
             ("IFD0:XPKeywords", "EXIF:XPKeywords"),
             ("XMP-photoshop:City", "XMP-photoshop:City"), ("XMP-photoshop:State", "XMP-photoshop:State"),
             ("XMP-photoshop:Country", "XMP-photoshop:Country"), ("IPTC:City", "IPTC:City"),
             ("IPTC:Province-State", "IPTC:Province-State"),
             ("IPTC:Country-PrimaryLocationName", "IPTC:Country-PrimaryLocationName")]
UNDO_LIST_TAGS = {"XMP-dc:Subject", "IPTC:Keywords"}
UNDO_PER_PHOTO = 5          # saves you can step back through, per photo
UNDO_PHOTOS = 3000          # photos remembered in the undo file


def snapshot_metadata(et: "ExifTool", path: str) -> dict:
    """Every field a save can change, exactly as it is in the file now ({write name: value or list})."""
    target = metadata_target(path)
    if is_raw(path) and not os.path.isfile(target):
        return {}                                     # no sidecar yet: undo empties the fields again
    out = et._run(["-j", "-G1", "-a", "-api", "struct=0", "-charset", "iptc=UTF8"]
                  + ["-" + t.split(":", 1)[1] for t, _ in UNDO_TAGS] + [target])
    try:
        rows = json.loads(out) if out.strip() else []
    except ValueError:
        rows = []
    row = rows[0] if rows else {}
    snap = {}
    for read_name, write_name in UNDO_TAGS:
        v = row.get(read_name)
        if v is None or v == "":
            continue
        if write_name in UNDO_LIST_TAGS:
            snap[write_name] = [str(x) for x in v] if isinstance(v, list) else [str(v)]
        else:
            snap[write_name] = str(v[0] if isinstance(v, list) and v else v)
    return snap


def restore_metadata(et: "ExifTool", path: str, snap: dict) -> None:
    """Put back the fields from snapshot_metadata(): fields that were empty then are emptied again.
    The picture itself is verified unchanged, as with every save."""
    args = ["-m", "-P", "-overwrite_original", "-charset", "iptc=UTF8", "-IPTC:CodedCharacterSet=UTF8"]
    for _, tag in UNDO_TAGS:
        v = (snap or {}).get(tag)
        args.append(f"-{tag}=")                       # clear first (also removes every keyword)
        if isinstance(v, list):
            args += [f"-{tag}={single_line(x)}" for x in v]
        elif v:
            args.append(f"-{tag}={single_line(v)}")

    def run():
        target = metadata_target(path)
        if is_raw(path) and not os.path.isfile(target):
            return                                    # no sidecar: nothing to put back
        out = et._run((xmp_only(args) if target.lower().endswith(".xmp") else args) + [target])
        if not wrote_one(out):
            raise ExifToolError(out.strip() or "ExifTool did not update the file")
    _guarded(path, run)


_undo_lock = threading.Lock()


# StockUp (github.com/kefirman9000/stockup): checks the photos and uploads them to the stock sites ---------
STOCKUP_DOWNLOAD = "https://github.com/kefirman9000/stockup/releases/latest"
WINDOWS_ARG_LIMIT = 30000   # Windows can't start a program with a longer command line (32,767 characters)


def _usable_stockup(c: str) -> bool:
    if not c:
        return False
    low = c.replace("\\", "/").lower()
    if "/stockup-update/" in low or "/previous/" in low:    # StockUp's own update backup: never the one to use
        return False
    if c.endswith(".app"):
        return os.path.isdir(c)
    return os.path.isfile(c) and (c.lower().endswith((".exe", ".py")) or os.access(c, os.X_OK))


def _built_at(c: str) -> float:
    """When this copy of StockUp was made (the program file's date: unzipping a download keeps it)."""
    exe = os.path.join(c, "Contents", "MacOS", "StockUp") if c.endswith(".app") else c
    try:
        return os.path.getmtime(exe if os.path.exists(exe) else c)
    except OSError:
        return 0.0


def stockup_candidates(custom: str = "") -> list[str]:
    """Every copy of StockUp found: the one chosen in Settings, and the usual places (StockUp.app on a Mac; on
    Windows it's a folder you unzip anywhere, so the usual folders, OneDrive and Downloads are searched)."""
    import glob
    home = os.path.expanduser("~")
    cands = [custom] if custom else []
    if sys.platform == "darwin":
        cands += ["/Applications/StockUp.app", os.path.join(home, "Applications", "StockUp.app")]
    elif os.name == "nt":
        one = os.environ.get("OneDrive", "")
        roots = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""),
                 os.path.join(home, "Desktop"), os.path.join(home, "Documents"), os.path.join(home, "Downloads"),
                 os.path.join(one, "Desktop") if one else "", os.path.join(one, "Documents") if one else "",
                 os.path.dirname(program_root())]                          # next to IPTC Tagger's own folder
        for r in dict.fromkeys(filter(None, roots)):
            for pat in ("StockUp*.exe", "StockUp*/StockUp.exe", "StockUp*/StockUp*/StockUp.exe",
                        "Programs/StockUp*/StockUp.exe"):
                cands += glob.glob(os.path.join(r, pat))
    out, seen = [], set()
    for c in cands:
        key = os.path.normcase(os.path.abspath(c)) if c else ""
        if key and key not in seen and _usable_stockup(c):
            seen.add(key)
            out.append(c)
    return out


def find_stockup(custom: str = "") -> str | None:
    """The StockUp to open: the newest copy found (by when it was built), so a newer download or update is
    used even when an older copy is still around or was chosen in Settings before. (A folder name like
    "StockUp-Windows (1)" says nothing about which is newer.)"""
    cands = stockup_candidates(custom)
    if not cands:
        return None
    best = max(cands, key=lambda c: (_built_at(c), c == custom))
    if len(cands) > 1:
        debug("StockUp copies found: " + "; ".join(f"{c} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(_built_at(c)))})"
                                                  for c in cands) + f" -> using {best}")
    return best


def _python_for_script() -> list[str] | None:
    """A Python to run stockup.py with (this app's own Python only when it isn't the packaged program)."""
    if not getattr(sys, "frozen", False):
        return [sys.executable]
    if os.name == "nt":
        if shutil.which("py"):
            return [shutil.which("py"), "-3"]
        return [shutil.which("python")] if shutil.which("python") else None
    for c in (shutil.which("python3"), "/opt/homebrew/bin/python3", "/usr/local/bin/python3",
              "/Library/Frameworks/Python.framework/Versions/Current/bin/python3", "/usr/bin/python3"):
        if c and os.path.isfile(c):
            return [c]
    return None


def stockup_running(app_path: str) -> bool:
    """Is StockUp open already? (It only takes photos when it starts.) Unknown for stockup.py."""
    if not app_path or app_path.lower().endswith(".py"):
        return False
    try:
        if os.name == "nt":
            name = os.path.basename(app_path)
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH", "/FO", "CSV"],
                                 capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
                                 creationflags=0x08000000).stdout
            return f'"{name.lower()}"' in out.lower()
        name = os.path.splitext(os.path.basename(app_path.rstrip("/")))[0]
        return subprocess.run(["pgrep", "-x", name], capture_output=True, timeout=10,
                              stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def stockup_command(app_path: str, paths: list[str], new_window: bool = False) -> tuple[list[str], str]:
    """The command that opens these photos in StockUp, and a note when the folder is opened instead.
    StockUp opens photos from one folder right where they are (only those photos); photos from several
    folders are copied into a new batch in Pictures › StockUp Batches."""
    note = ""
    if os.name == "nt" and sum(len(p) + 3 for p in paths) > WINDOWS_ARG_LIMIT:
        folders = {os.path.dirname(p) for p in paths}
        if len(folders) != 1:
            raise ValueError("too many photos from different folders to hand to StockUp at once. "
                             "Open fewer at a time, or put them in one folder.")
        paths, note = [folders.pop()], "too many photos to list one by one, so their whole folder was opened"
    if app_path.endswith(".app"):
        return ["open"] + (["-n"] if new_window else []) + ["-a", app_path] + paths, note
    if app_path.lower().endswith(".py"):
        py = _python_for_script()
        if not py:
            raise ValueError("Python wasn't found to run stockup.py. Install Python, or choose StockUp.exe / "
                             "StockUp.app in Settings → Saving.")
        return py + [app_path] + paths, note
    return [app_path] + paths, note


def open_in_stockup(app_path: str, paths: list[str], new_window: bool = False) -> str:
    """Start StockUp with these photos. Returns a note ('' normally). Raises ValueError / OSError."""
    cmd, note = stockup_command(app_path, paths, new_window)
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
              cwd=os.path.dirname(app_path.rstrip("/\\")) or None)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200       # detached, own group: stays open when this app closes
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)
    debug(f"opened {len(paths)} photo(s) in StockUp: {cmd[:4]}{' …' if len(cmd) > 4 else ''}")
    return note


# The photo list, kept as you work so nothing is lost if the app closes unexpectedly ----------------
# Stock-site text rules, the same ones StockUp checks (from each site's contributor help pages) ---------------
STOCK_SITES = {
    "shutterstock": {"label": "Shutterstock", "kw_min": 7, "kw_max": 50, "desc_max": 200, "desc_min_words": 5,
                     "plain": True},   # "Exclude special characters" (its help on rejected titles)
    "adobe": {"label": "Adobe Stock", "kw_min": 5, "kw_max": 49, "title_max": 70},
    "dreamstime": {"label": "Dreamstime", "own_desc": True, "kw_min": 7, "kw_max": 50},   # title: dreamstime_checks
    "alamy": {"label": "Alamy", "own_desc": True},
    "istock": {"label": "iStock"},
    "depositphotos": {"label": "Depositphotos", "kw_min": 8, "kw_max": 50, "desc_max": 250},
    "123rf": {"label": "123RF", "kw_min": 7, "desc_max": 180},
    "pond5": {"label": "Pond5", "kw_min": 5, "kw_max": 50, "title_max": 80},
}
DEFAULT_STOCK_SITES = ["shutterstock", "adobe"]


def stockup_options_path() -> str:
    """Where StockUp keeps its options (including the sites you sell on)."""
    if sys.platform == "darwin":
        d = os.path.expanduser("~/Library/Application Support/StockUp")
    elif os.name == "nt":
        d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "StockUp")
    else:
        d = os.path.join(os.path.expanduser("~"), ".config", "stockup")
    return os.path.join(d, "stockup_options.json")


def stockup_sites() -> list[str] | None:
    """The sites ticked in StockUp's Settings, or None if StockUp hasn't been set up on this computer."""
    try:
        with open(stockup_options_path(), encoding="utf-8") as f:
            sites = json.load(f).get("my_sites")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(sites, list):
        return None
    return [k for k in STOCK_SITES if k in sites] or None


def stock_sites(settings: dict) -> list[str]:
    """The sites whose rules titles and keywords are checked against."""
    if settings.get("stock_sites_from_stockup", True):
        found = stockup_sites()
        if found:
            return found
    chosen = settings.get("stock_sites")
    if not isinstance(chosen, list):
        chosen = DEFAULT_STOCK_SITES
    return [k for k in STOCK_SITES if k in chosen]


def site_problems(title: str, keywords, sites, description: str = "", ss_text: str = "title") -> list[str]:
    """What a stock site would reject or cut in this title and these keywords, one short line each.
    A photo without its own description has its title saved as the description, so then the
    description limits apply to the title."""
    title = (title or "").strip()
    desc = (description or "").strip() or title
    n = len(keywords or [])
    out = []
    if not title and not n:
        return out                                  # nothing written yet: "No title" says it already
    for key in sites:
        r = STOCK_SITES.get(key)
        if not r:
            continue
        bits = []
        d = (title or desc) if key == "shutterstock" and ss_text != "description" else desc   # what that site gets
        if r.get("title_max") and len(title) > r["title_max"]:
            bits.append(f"title {len(title)} characters (most {r['title_max']})")
        what = "title" if key == "shutterstock" and ss_text != "description" and title else "description"
        if r.get("desc_max") and len(d) > r["desc_max"]:
            bits.append(f"{what} {len(d)} characters (most {r['desc_max']})")
        if r.get("desc_min_words") and d and len(d.split()) < r["desc_min_words"]:
            bits.append(f"{what} under {r['desc_min_words']} words")
        if r.get("own_desc") and title and not (description or "").strip():
            bits.append("no description of its own (the title is used)")
        if r.get("kw_min") and n < r["kw_min"]:
            bits.append(f"{n} keyword{'s' if n != 1 else ''} (at least {r['kw_min']})")
        if r.get("kw_max") and n > r["kw_max"]:
            bits.append(f"{n} keywords (most {r['kw_max']})")
        if r.get("plain"):
            odd = special_characters(" ".join([title, d] + [str(k) for k in keywords or []]))
            if odd:
                bits.append("special characters " + " ".join(odd[:8]) + (" …" if len(odd) > 8 else ""))
        if bits:
            out.append(f"{r['label']}: " + ", ".join(bits))
    return out


# --------------------------------------------------------------------------- Dreamstime, strictly
# Dreamstime refuses more for its text than Shutterstock does: titles stuffed with phrases, CAPS, a description
# that only repeats the title or stops half-way, the same keyword in several spellings. These are the extra checks
# (Check Before Upload, when you sell on Dreamstime), each with a fix where one can be made safely.
DT_TITLE_LONG = 130          # Dreamstime refuses longer titles: "The maximum number of characters in the title is 130"
_DT_DATELINE = re.compile(r"^[^:]{2,90}?\d{4}:\s+")
_CAPS_RUN = re.compile(r"\b[A-Z][A-Z'’&]+(?:\s+[A-Z][A-Z'’&]+)*\b")
_ACRONYMS = {"USA", "BBQ", "NASA", "NYC", "LED", "HDMI", "USB", "ATM", "DIY", "SUV", "NFL", "NBA", "MLB", "NHL",
             "UPS", "IKEA", "LEGO", "ASUS", "CVS", "KFC", "IHOP", "GNC", "AT&T", "NCAA", "WWII", "YMCA", "COVID"}


def _dateline_split(title: str) -> tuple[str, str]:
    m = _DT_DATELINE.match(title or "")
    return (title[:m.end()], title[m.end():]) if m else ("", title or "")


def shorten_title(title: str, most: int = DT_TITLE_LONG) -> str:
    """Cut a long title back to its main phrase so it fits `most` characters, keeping an editorial place-and-date
    in front: at a comma or before 'for …' / 'with …' when it can ('…Cake on Grocery Shelf, Lemon Sugar Flavor
    Variety, with Orange…' -> '…Cake on Grocery Shelf'), else before 'on …' / 'in …', else after a whole word
    (never ending on 'and', 'of', 'the'…). Always fits."""
    title = title or ""
    if len(title) <= most:
        return title
    head, body = _dateline_split(title)
    if most - len(head) < 25:                       # (a very long place line: cut the whole title by words)
        head, body = "", title
    room = most - len(head)
    for pattern in (r",\s+|\s+(?:for|with|featuring|perfect for|ideal for|great for)\s+",
                    r"\s+(?:on|in|at|from|near|inside|by)\s+"):
        cuts = [m for m in re.finditer(pattern, body) if 20 <= m.start() <= room]
        if cuts:
            cut = cuts[-1].start()
            if len(cuts) > 1 and len(body[cuts[-2].end():cut].split()) <= 2:   # "…Vegetables, Gravy": not "Gravy"
                cut = cuts[-2].start()
            return head + body[:cut].rstrip(" ,;:-")
    words = body[:room + 1].split()
    if len(body) > room and len(words) > 1:
        words.pop()                                  # the last word may be cut in half
    while len(words) > 1 and words[-1].lower().strip(",;:-") in SMALL_WORDS | _DANGLING:
        words.pop()
    return (head + " ".join(words).rstrip(" ,;:-"))[:most].rstrip(" ,;:-")


def _caps_runs(text: str) -> list[str]:
    """Words written in capitals ('THE EYE OPENER', 'INTERVIEWS'), leaving out acronyms (USA, BBQ, a lone 'TV')."""
    out = []
    for m in _CAPS_RUN.finditer(text or ""):
        run = m.group(0)
        ws = [w for w in run.split() if w not in _ACRONYMS]
        if ws and (len(ws) > 1 and sum(len(w) for w in ws) >= 5 or any(len(w) >= 4 for w in ws)):
            out.append(run)
    return out


def _decap(text: str) -> str:
    for run in _caps_runs(text):
        fixed = " ".join(w if w in _ACRONYMS else w[0] + w[1:].lower() for w in run.split())
        text = text.replace(run, fixed)
    return text


def _kw_key(k: str) -> str:
    """'Hershey's' / 'hersheys', 'hungry man' / 'hungryman', 'cookie' / 'cookies' -> the same key."""
    def one(w):
        return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")) else w
    return "".join(one(w) for w in re.findall(r"[a-z0-9]+", k.casefold().replace("'", "").replace("’", "")))


def keyword_variants(keywords) -> list[tuple[str, str]]:
    """Keywords that are the same word written another way ('hungry man' / 'hungryman', 'cookie' / 'cookies'):
    (the one to keep, the repeat). Dreamstime counts these as keyword spam."""
    seen, out = {}, []
    for k in keywords or []:
        key = _kw_key(k)
        if not key:
            continue
        if key in seen:
            out.append((seen[key], k))
        else:
            seen[key] = k
    return out


def dreamstime_checks(title: str, description: str, keywords) -> list[dict]:
    """[{'what', 'detail', 'fixable'}] for Dreamstime's stricter text review."""
    out = []
    title, description = (title or "").strip(), (description or "").strip()

    def add(what, detail, fixable=False):
        out.append({"what": what, "detail": detail, "fixable": fixable})
    words = re.findall(r"[\w'-]+", _dateline_split(title)[1])
    if title and (len(title) < 5 or len(words) < 2):
        add("Title too short", "Dreamstime refuses one-word titles: describe the subject in a short phrase.")
    if len(title) > DT_TITLE_LONG:
        add("Long title", f"{len(title)} characters; Dreamstime refuses titles over {DT_TITLE_LONG}. Fix cuts it to: "
            f"\"{shorten_title(title)}\"", fixable=True)
    caps = _caps_runs(title + " . " + description)
    if caps:
        add("Capital letters", "Words in capitals (" + ", ".join(dict.fromkeys(caps[:5])) + "): Dreamstime refuses "
            "text written in caps. Fix writes them in normal case.", fixable=True)
    if description and title:
        a, b = _title_tokens(_dateline_split(title)[1]), _title_tokens(_dateline_split(description)[1])
        if a and b and len(a & b) >= 0.85 * len(a | b):
            add("Description repeats title", "Dreamstime refuses a description that only repeats the title: Reword "
                "it to say what the title leaves out.")
    if description:
        ended = _drop_unfinished_tail(description)
        if len(ended) < len(description.rstrip(" .")):
            add("Description cut off", "It stops half-way (\"…" + description[-40:].lstrip() + "\"). Fix ends it at "
                "the last whole phrase.", fixable=True)
    twins = keyword_variants(keywords)
    if twins:
        add("Keyword repeats", "The same keyword twice: " + "; ".join(f"{a} / {b}" for a, b in twins[:4])
            + (" …" if len(twins) > 4 else "") + ". Dreamstime counts these as spam; Fix keeps the first of each.",
            fixable=True)
    return out


def dreamstime_title(title: str, bare: str | None = None) -> str:
    """The title the Dreamstime CSV gets: without the editorial place and date (the description and the
    Editorial column keep it editorial), cut to Dreamstime's 130 characters."""
    t = (bare if (bare or "").strip() else _dateline_split(title or "")[1]).strip() or (title or "")
    return shorten_title(t)


def dreamstime_fix(title: str, description: str, keywords) -> dict:
    """Make the fixes dreamstime_checks offers. {'title', 'description', 'keywords', 'fixes': [what was done]}."""
    fixes = []
    t, d, kws = title or "", description or "", list(keywords or [])
    new = shorten_title(t)
    if new != t:
        t = new
        fixes.append("title shortened")
    new_t, new_d = _decap(t), _decap(d)
    if (new_t, new_d) != (t, d):
        t, d = new_t, new_d
        fixes.append("capitals")
    if d:
        ended = _drop_unfinished_tail(d)
        if len(ended) < len(d.rstrip(" .")):
            d = ended + "."
            fixes.append("description ending")
    if keyword_variants(kws):
        seen, kept = set(), []
        for k in kws:
            key = _kw_key(k) or k
            if key not in seen:
                seen.add(key)
                kept.append(k)
        kws = kept
        fixes.append("keyword repeats")
    return {"title": t, "description": d, "keywords": kws, "fixes": fixes}


SUGGESTIONS_PER_PHOTO = 5   # earlier AI suggestions remembered per photo


def session_path() -> str:
    return os.path.join(config_dir(), "session.json")


def save_session(entries: list[dict]) -> None:
    """Write the current list (written to a temporary file first, so a crash mid-write can't spoil it)."""
    path = session_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"app": APP_VERSION, "saved": time.time(), "photos": entries}, f, ensure_ascii=False)
    os.replace(tmp, path)


def load_session() -> list[dict]:
    """The list from a session that didn't close properly: photos that still exist, else []."""
    try:
        with open(session_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for e in data.get("photos") or [] if isinstance(data, dict) else []:
        if isinstance(e, dict) and isinstance(e.get("path"), str) and os.path.isfile(e["path"]):
            out.append(e)
    return out


def clear_session() -> None:
    try:
        os.remove(session_path())
    except OSError:
        pass


def undo_path() -> str:
    return os.path.join(config_dir(), "undo_history.json")


def _undo_load() -> dict:
    try:
        with open(undo_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _undo_save(data: dict) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    tmp = undo_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, undo_path())


def remember_for_undo(path: str, snap: dict) -> None:
    """Keep what was in the file before a save, so the save can be undone (even after a restart)."""
    key = os.path.normcase(os.path.abspath(path))
    with _undo_lock:
        data = _undo_load()
        steps = data.pop(key, [])
        steps.append({"when": time.strftime("%Y-%m-%d %H:%M"), "meta": snap})
        data[key] = steps[-UNDO_PER_PHOTO:]           # (re-inserted: the most recently saved photos last)
        while len(data) > UNDO_PHOTOS:
            data.pop(next(iter(data)))
        _undo_save(data)


def undoable(paths) -> set:
    """Which of these photos have a save that can be undone."""
    with _undo_lock:
        data = _undo_load()
    return {p for p in paths if data.get(os.path.normcase(os.path.abspath(p)))}


def can_undo(path: str) -> bool:
    with _undo_lock:
        return bool(_undo_load().get(os.path.normcase(os.path.abspath(path))))


def undo_last_save(et: "ExifTool", path: str) -> None:
    """Restore the metadata from before the most recent save of this photo."""
    key = os.path.normcase(os.path.abspath(path))
    with _undo_lock:
        data = _undo_load()
        steps = data.get(key) or []
        if not steps:
            raise ExifToolError("there's no earlier save of this photo to undo")
        step = steps[-1]
    restore_metadata(et, path, step.get("meta") or {})
    with _undo_lock:
        data = _undo_load()
        steps = data.get(key) or []
        if steps and steps[-1] == step:
            steps.pop()
            if steps:
                data[key] = steps
            else:
                data.pop(key, None)
            _undo_save(data)


def _guarded_raw(path: str, action) -> None:
    """Run a sidecar change for a RAW file, then prove the RAW itself is byte-for-byte the same. Nothing is ever
    written to the RAW, so this only checks; the sidecar is the only file that changes."""
    if not is_raw(path):
        raise ExifToolError("not a RAW file")
    target = sidecar_path(path)
    if is_raw(target) or is_jpeg(target):            # never let a photo file be the write target
        raise ExifToolError("refused: the sidecar name looks like a photo file")
    before = file_fingerprint(path)
    action()
    if file_fingerprint(path) != before:
        raise ExifToolError("the RAW file changed while saving, which should never happen. Please report this")


def _guarded(path: str, action) -> None:
    """Run a metadata change, then prove the picture itself is untouched. If the image data ever
    differs, the original file is restored exactly and an error raised. RAW files: see _guarded_raw."""
    if is_raw(path):
        return _guarded_raw(path, action)
    before = jpeg_image_data_hash(path)
    st = os.stat(path)
    with open(path, "rb") as f:
        original = f.read()

    def restore(reason: str):
        with open(path, "wb") as f:
            f.write(original)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        raise ExifToolError(reason + " (original file restored)")

    try:
        action()
    except ExifToolError:
        if os.path.exists(path) and os.path.getsize(path) == 0:
            restore("ExifTool left an empty file")
        raise
    try:
        after = jpeg_image_data_hash(path)
    except (OSError, ValueError):
        restore("the file could not be read back after writing")
    if after != before:
        restore("the image data changed, which should never happen")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def safe_write(et: "ExifTool", path: str, title: str, keywords: list[str], description: str | None = None,
               xmp2iptc_args: str | None = None, fields=None, location: str = "", label: str | None = None,
               strip_develop: bool = False) -> None:
    """Write metadata, then prove the picture itself is untouched. If the image
    data ever differs, the original file is restored exactly and an error raised.
    RAW files: the title and keywords go into the .xmp sidecar only; the RAW is verified unchanged."""
    if is_raw(path):
        def to_sidecar():
            target = sidecar_path(path)
            extra = {} if label is None else {"label": label}
            if fields is None:
                et.write(target, title, keywords, description, **extra)
            else:
                et.write(target, title, keywords, description, fields=fields, location=location, **extra)
        return _guarded_raw(path, to_sidecar)
    before = jpeg_image_data_hash(path)
    st = os.stat(path)
    with open(path, "rb") as f:
        original = f.read()

    def restore(reason: str):
        with open(path, "wb") as f:
            f.write(original)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        raise ExifToolError(reason + " (original file restored)")

    try:
        extra = {} if label is None else {"label": label}
        if strip_develop:                       # JPEGs only: a RAW's sidecar is where Lightroom keeps its edit
            extra["strip_develop"] = True
        if fields is None:
            et.write(path, title, keywords, description, **extra)
        else:
            et.write(path, title, keywords, description, fields=fields, location=location, **extra)
        if xmp2iptc_args:
            et.xmp_to_iptc(path, xmp2iptc_args)   # make IPTC match XMP
    except ExifToolError:
        if os.path.exists(path) and os.path.getsize(path) == 0:
            restore("ExifTool left an empty file")
        raise
    try:
        after = jpeg_image_data_hash(path)
    except (OSError, ValueError):
        restore("the file could not be read back after writing")
    if after != before:
        restore("the image data changed, which should never happen")
    # keep the file's original dates exactly (ExifTool's -P only keeps whole seconds)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


# --------------------------------------------------------------------------- SEO title + keyword rules

SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor", "of", "on",
               "onto", "or", "over", "per", "the", "to", "up", "via", "vs", "with", "yet", "so"}


def title_case(text: str, every_word: bool = False) -> str:
    """SEO Title Case: capitalise each word except small words in the middle; leave words
    that already have their own capitals (iPhone, NASA, McDonald's) exactly as they are.
    every_word: capitalise the small words too ('Magazines On A Shelf For Reading')."""
    words = clean_title(text).split()
    out = []
    for i, word in enumerate(words):
        prev = out[-1] if out else ""
        starts = i == 0 or prev.endswith((":", "–", "—", "-", "|"))
        last = i == len(words) - 1

        def fix(part: str, edge: bool) -> str:
            letters = [c for c in part if c.isalpha()]
            if not letters:
                return part
            if any(c.isupper() for c in letters[1:]):          # iPhone, NASA, eBay, McDonald's
                return part
            bare = part.lower().strip("()[]\"'“”‘’,;")
            if not edge and not every_word and bare in SMALL_WORDS:
                return part.lower()
            j = next(k for k, c in enumerate(part) if c.isalpha())
            return part[:j] + part[j].upper() + part[j + 1:]

        parts = word.split("-")
        out.append("-".join(fix(pt, starts or last or k > 0) for k, pt in enumerate(parts)))
    return " ".join(out)


# "A Picture of …", "An Image Showing …", "This Is a Photo of …": filler in front of the subject. A
# describing word that tells you something ("Aerial Photo of", "Close-Up Photo of") is left alone.
_FILLER_OPENING = re.compile(
    r"^\s*(?:(?:this\s+is|here\s+is|here's|there\s+is|this)\s+)?(?:(?:a|an|the)\s+)?"
    r"(?:(?:beautiful|stunning|nice|great|lovely|stock|high[\s-]quality|royalty[\s-]free|digital|real)\s+)*"
    r"(?:picture|photo|photograph|image|snapshot|shot|pic|depiction|capture|stock\s+photo)s?\s+"
    r"(?:of|showing|featuring|depicting|with|that\s+shows|which\s+shows|capturing)\s+(?:(?:a|an)\s+)?", re.I)
_LEADING_ARTICLE = re.compile(r"^\s*(?:a|an)\s+(?=\w)", re.I)   # not "The": The Hague, The Shard


def direct_title(text: str) -> str:
    """Start the title with the subject: 'A Picture of Magazines on a Shelf' -> 'Magazines on a Shelf',
    'A Stack of Books' -> 'Stack of Books'. Nothing is changed if too little would be left."""
    t = (text or "").strip()

    def first_capital(s):                            # "cats" -> "Cats", but never "iPhone" -> "IPhone"
        w = s.split()[0]
        return s[0].upper() + s[1:] if w == w.lower() else s
    new = _FILLER_OPENING.sub("", t, count=1).strip()
    if new != t and new:
        return first_capital(new)                    # "A photo of cats" -> "Cats"
    new = _LEADING_ARTICLE.sub("", t, count=1).strip()
    return first_capital(new) if new != t and len(new.split()) >= 2 else t


def style_title(text: str, direction: str = "", caps: str = "title", direct: bool = True) -> str:
    """The finishing step for every title the AI writes: start with the subject (direct), then the
    capitals you chose: 'title' (Title Case) or 'every' (Capitalise Every Word). Your directions win
    when they ask for other capitals (sentence case…)."""
    t = direct_title(text) if direct else (text or "")
    if title_case_overridden(direction):
        return clean_title(t)
    return title_case(t, every_word=caps == "every")


DIRECT_TITLE_RULE = ("Start the title directly with the main subject, the way stock titles are written: 'Magazines on "
                     "a Store Shelf for Reading', not 'A Picture of Magazines on a Shelf'. Never begin with 'A photo "
                     "of', 'A picture of', 'An image of', 'This is', 'There is', or with 'A' or 'An'.")


_TITLE_CASE_OVERRIDE = re.compile(
    r"sentence[\s-]*case|lower[\s-]*case\s+titles?|titles?\s+(in\s+)?lower[\s-]*case|all[\s-]*caps|"
    r"upper[\s-]*case\s+titles?|titles?\s+(in\s+)?upper[\s-]*case|no\s+title[\s-]*case|don'?t\s+(use\s+)?title[\s-]*case",
    re.I)
_KEYWORD_CASE_OVERRIDE = re.compile(
    r"keywords?[^.\n]{0,60}(capital|upper[\s-]*case|title[\s-]*case|proper[\s-]*(noun|case))|"
    r"(capitali[sz]e|keep\s+(the\s+)?capitals?\s+(of|for|in))[^.\n]{0,40}keywords?", re.I)


def title_case_overridden(direction: str) -> bool:
    """True if your directions ask for a different title capitalisation than Title Case."""
    return bool(_TITLE_CASE_OVERRIDE.search(direction or ""))


def keyword_case_overridden(direction: str) -> bool:
    """True if your directions ask for capitals in keywords."""
    return bool(_KEYWORD_CASE_OVERRIDE.search(direction or ""))


def _title_tokens(title: str) -> set[str]:
    """The words that carry meaning in a title, singular and lowercase (no 'a', 'on', 'with'…)."""
    words = re.findall(r"[\w']+", (title or "").casefold().replace("'", ""))
    return {_singular(w) for w in words if w not in SMALL_WORDS}


# Automatic checks on what the AI wrote. When one finds a problem, the AI is asked once (text only) to fix it,
# and the fix is only kept if it really has fewer problems. (key, label, what it does)
TEXT_CHECKS = [
    ("numbers", "Too many numbers in a title",
     "Three or more numbers (or numbers in a short title): reworded with less emphasis on them, keeping a number "
     "only when it defines the subject, like a model number or an important year."),
    ("repeats", "The same word twice in a title",
     "'Coffee Cup with Coffee Beans and a Coffee Grinder': reworded so each word is used once."),
    ("list", "A title that reads like a list of keywords",
     "Four or more commas: reworded as one natural, descriptive phrase."),
    ("desc_copy", "A description that only repeats the title",
     "Rewritten to add what the title leaves out: what's happening, the setting, details, the mood or use."),
    ("fit", "Too long for my stock sites: shorten to fit",
     "Shortened to fit the title and description limits of the sites you sell on (for example Adobe Stock's "
     "70-character title), counting the location, date and text before titles. Off by default, because it makes "
     "detailed titles shorter."),
]
DEFAULT_TEXT_CHECKS = ["numbers", "repeats", "list", "desc_copy"]
_NUMBER = re.compile(r"(?<![\w])\d[\d,.:/]*(?:[a-z]{0,3})?(?![\w])", re.I)


def text_checks_keys(keys) -> list[str]:
    want = set(keys or [])
    return [k for k, _l, _h in TEXT_CHECKS if k in want]


def text_problems(title: str, description: str = "", checks=(), limits=None) -> tuple[list[str], list[str]]:
    """What the chosen checks find in a title and description, as instructions for the AI to fix them:
    (title problems, description problems)."""
    checks, limits = set(checks or []), limits or {}
    title, description = (title or "").strip(), (description or "").strip()
    tp, dp = [], []
    if not title:
        return tp, dp
    if "numbers" in checks:
        nums = _NUMBER.findall(title)
        if len(nums) >= 3 or (len(nums) >= 2 and len(nums) * 4 > len(title.split())):
            tp.append(f"The title has too many numbers ({', '.join(nums)}): give them less emphasis. Keep a number only "
                      "when it really defines the subject (a model number, an important year, a quantity that is the "
                      "point of the photo) and describe the rest in words.")
    if "repeats" in checks:
        seen, twice = {}, []
        for w in re.findall(r"[\w'-]+", title.casefold().replace("'", "")):
            if len(w) > 2 and w not in SMALL_WORDS and not w.isdigit():
                k = _singular(w)
                seen[k] = seen.get(k, 0) + 1
                if seen[k] == 2:
                    twice.append(w)
        if twice:
            tp.append(f"The title uses {', '.join(twice)} more than once: use each word only once, choosing a "
                      "different word or leaving the repeat out.")
    if "list" in checks and title.count(",") >= 4:
        tp.append("The title reads like a list of keywords: rewrite it as one natural, descriptive phrase with at "
                  "most two commas.")
    if "desc_copy" in checks and description:
        a, b = _title_tokens(title), _title_tokens(description)
        if a and b and len(a & b) >= 0.8 * len(a | b):
            dp.append("The description only repeats the title: rewrite it as one or two full sentences that add what "
                      "the title leaves out (what is happening, the setting, details, the mood or the use), without "
                      "repeating the title word for word.")
    if "fit" in checks:
        tmax, dmax = int(limits.get("title_max") or 0), int(limits.get("desc_max") or 0)
        if tmax and len(title) > tmax:
            tp.append(f"The title is {len(title)} characters long: shorten it to at most {tmax} characters, keeping "
                      "the main subject and the most important details.")
        if dmax and description and len(description) > dmax:
            dp.append(f"The description is {len(description)} characters long: shorten it to at most {dmax} "
                      "characters.")
    return tp, dp


SIMILAR_TITLES = 0.75   # share of meaningful words two titles have in common to count as near-identical


def similar_title_groups(titles, threshold: float = SIMILAR_TITLES) -> list[list[int]]:
    """Groups (by position) of titles that are the same or nearly the same, e.g. 'Red Barn in a Snowy Field'
    and 'Red Barn in Snowy Fields'. Titles of fewer than 3 meaningful words are left out."""
    toks = [_title_tokens(t) for t in titles]
    parent = list(range(len(toks)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    idx = [i for i, t in enumerate(toks) if len(t) >= 3]
    for a, i in enumerate(idx):
        ti = toks[i]
        for j in idx[a + 1:]:
            tj = toks[j]
            small, big = sorted((len(ti), len(tj)))
            if small < threshold * big:                # can't reach the threshold: skip the set work
                continue
            if len(ti & tj) >= threshold * len(ti | tj):
                parent[root(j)] = root(i)
    groups: dict[int, list[int]] = {}
    for i in idx:
        groups.setdefault(root(i), []).append(i)
    return sorted((g for g in groups.values() if len(g) > 1), key=lambda g: g[0])


def _singular(word: str) -> str:
    """Rough singular form, used only to spot duplicates like 'chart' / 'charts'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def seo_keyword(text: str, lower: bool = True) -> str:
    """One keyword: letters/numbers/spaces only, lowercase unless your directions say otherwise."""
    k = str(text).lower() if lower else str(text)
    k = re.sub(r"\b(\w{1,2})\s*&\s*(\w{1,2})\b", r"\1\2", k, flags=re.I)   # s&p -> sp, at&t -> att
    k = k.replace("&", " and ")
    k = re.sub(r"['’‘`´]", "", k)               # mcdonald's -> mcdonalds
    k = re.sub(r"[^\w\s]|_", " ", k)            # every other symbol becomes a space
    return " ".join(k.split())


def seo_keywords(words, limit: int | None = None, keep=None, lower: bool = True, protect: str = "") -> list[str]:
    """Clean SEO keywords: no special characters, no duplicates or plural duplicates, and no word
    used in more than MAX_WORD_REPEATS keywords. Lowercase unless lower=False.
    Keywords in `keep` (already chosen) count towards duplicates and repeats.
    A keyword that appears in `protect` (your own instructions) is never dropped for repetition."""
    out, seen, counts = [], set(), {}
    protect = (protect or "").casefold()

    def key(k):
        return " ".join(_singular(w) for w in k.casefold().split())

    def note(k):
        seen.add(key(k))
        for w in set(k.casefold().split()):
            if w not in SMALL_WORDS:
                counts[_singular(w)] = counts.get(_singular(w), 0) + 1

    for k in keep or []:
        note(seo_keyword(k, lower))
    for raw in words or []:
        k = seo_keyword(raw, lower)
        if not k or k.casefold() in SMALL_WORDS:
            continue
        if key(k) in seen:
            continue
        mine = bool(protect) and k.casefold() in protect          # a keyword you asked for yourself
        if not mine and any(counts.get(_singular(w), 0) >= MAX_WORD_REPEATS
                            for w in set(k.casefold().split()) if w not in SMALL_WORDS):
            continue                                # excessive repetition of one word
        note(k)
        out.append(k)
        if limit and len(out) >= limit:
            break
    return out


# words that describe the file, not the photo: buyers never search for them
FILLER_KEYWORDS = {"photo", "photos", "photograph", "photographs", "photography", "image", "images", "picture",
                   "pictures", "pic", "pics", "stock", "stock photo", "stock image", "stock photography",
                   "jpeg", "jpg", "hd", "4k", "high quality", "high resolution", "royalty free", "no people"}
MAX_KEYWORD_WORDS = 4


def tidy_keywords(keywords, title: str = "", protect: str = "") -> list[str]:
    """Remove filler words ('photo', 'image', 'stock'…) and long phrases, then move the keywords that
    name what's in the title to the front (stock sites weigh the first ten the most). Keywords you
    asked for in your own instructions are never removed."""
    protect = (protect or "").casefold()
    title_words = {_singular(w) for w in re.findall(r"[\w']+", (title or "").casefold().replace("'", ""))
                   if w not in SMALL_WORDS}
    kept = []
    for k in keywords or []:
        mine = bool(protect) and k.casefold() in protect
        if not mine and (k.casefold() in FILLER_KEYWORDS or len(k.split()) > MAX_KEYWORD_WORDS):
            continue
        kept.append(k)

    def in_title(k):
        ws = [_singular(w) for w in k.casefold().split() if w not in SMALL_WORDS]
        return bool(ws) and all(w in title_words for w in ws)
    return [k for k in kept if in_title(k)] + [k for k in kept if not in_title(k)]


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def fill_tokens(text: str, taken: str = "") -> str | None:
    """Fill {year} {month} {date} from a capture date (YYYY-MM-DD). None if a token can't be filled."""
    if "{" not in text:
        return text
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", taken or "")
    if not m:
        return None
    y, mo, _ = m.groups()
    out = text.replace("{year}", y).replace("{month}", MONTHS[int(mo) - 1]).replace("{date}", taken)
    return None if re.search(r"\{\w*\}", out) else out


def expand_title_prefix(text: str, taken: str = "") -> str:
    """The 'text before titles' for one photo. Tokens that can't be filled (no capture date)
    are dropped, along with any separator left dangling."""
    text = single_line(text or "")
    filled = fill_tokens(text, taken)
    if filled is None:
        filled = re.sub(r"\{\w*\}", "", text)
        filled = re.sub(r"\(\s*\)", "", filled)                            # "Photo ({year})" -> "Photo"
        filled = re.sub(r"[,;:|–—-]\s*(?=[,;:|–—-])", "", filled)           # keep the last separator in a run
        filled = re.sub(r"\s+([,;:])", r"\1", " ".join(filled.split()))   # "Tokyo :" -> "Tokyo:"
        filled = filled.lstrip(",;:|–—- ")
        if not any(ch.isalnum() for ch in filled):
            filled = ""
    return " ".join(filled.split())


# Date taken in the title -------------------------------------------------------------
DATE_POSITIONS = [
    ("off", "Don't add the date"),
    ("start", "At the very start (before your text)"),
    ("before_title", "Just before the AI's title"),
    ("end", "After the AI's title"),
]
DATE_FORMATS = [   # (key, strftime-like pattern, built without locale so it's the same on every computer)
    ("long", "{Month} {d}, {Y}"),            # October 26, 2026
    ("short", "{Mon} {d}, {Y}"),             # Oct 26, 2026
    ("long_dmy", "{d} {Month} {Y}"),         # 26 October 2026
    ("iso", "{Y}-{m}-{dd}"),                 # 2026-10-26
    ("us", "{m}/{dd}/{Y}"),                  # 10/26/2026
    ("eu", "{dd}/{m}/{Y}"),                  # 26/10/2026
    ("dots", "{dd}.{m}.{Y}"),                # 26.10.2026
    ("month_year", "{Month} {Y}"),           # October 2026
    ("year", "{Y}"),                         # 2026
    ("weekday", "{Weekday}, {Month} {d}, {Y}"),  # Monday, October 26, 2026
]
DATE_SEPARATORS = [("colon", "Colon   :"), ("dash", "Dash   –"), ("comma", "Comma   ,"), ("bar", "Bar   |"),
                   ("space", "Space only"), ("paren", "Parentheses   ( )")]
# what goes between the location and the date ("Tokyo, Japan - October 26, 2026")
LOCATION_SEPARATORS = [("dash", "Hyphen   -"), ("endash", "Dash   –"), ("comma", "Comma   ,"),
                       ("bar", "Bar   |"), ("space", "Space only")]
_LOC_JOIN = {"dash": " - ", "endash": " – ", "comma": ", ", "bar": " | ", "space": " "}
SAMPLE_DATE = "2026-10-26"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def format_date(taken: str, fmt: str) -> str:
    """A capture date (YYYY-MM-DD) in one of DATE_FORMATS; '' if there is no valid date."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", taken or "")
    if not m:
        return ""
    y, mo, d = (int(x) for x in m.groups())
    try:
        wd = datetime.date(y, mo, d).weekday()
    except ValueError:
        return ""
    pattern = dict(DATE_FORMATS).get(fmt, DATE_FORMATS[0][1])
    return (pattern.replace("{Weekday}", WEEKDAYS[wd]).replace("{Month}", MONTHS[mo - 1])
            .replace("{Mon}", MONTHS[mo - 1][:3]).replace("{Y}", f"{y:04d}").replace("{m}", f"{mo:02d}")
            .replace("{dd}", f"{d:02d}").replace("{d}", str(d)))


def _date_before(date: str, text: str, sep: str) -> str:
    if sep == "paren":
        return f"({date}) {text}"
    return date + {"space": " ", "comma": ", ", "dash": " – ", "bar": " | ", "colon": ": "}.get(sep, " ") + text


def _date_after(text: str, date: str, sep: str) -> str:
    if sep == "paren":
        return f"{text} ({date})"
    return text + {"space": " ", "comma": ", ", "dash": " – ", "bar": " | ", "colon": ", "}.get(sep, " ") + date


LOCATION_DATE_JOIN = _LOC_JOIN["dash"]   # editorial dateline: "Tokyo, Japan - October 26, 2026"


def title_dateline(location: str = "", date: str = "", loc_sep: str = "dash") -> str:
    """Location and date as they go into the title: 'Location - Date', or whichever one is given."""
    return _LOC_JOIN.get(loc_sep, LOCATION_DATE_JOIN).join(
        x for x in ((location or "").strip(), (date or "").strip()) if x)


def compose_title(title: str, prefix: str = "", date: str = "", pos: str = "off", sep: str = "dash",
                  location: str = "", loc_sep: str = "dash") -> str:
    """Build the final title from the suggested title, your text before titles, the location and the
    date taken. The location always goes just before the date (editorial style); with the date off it
    goes before the title. Running it again on its own result changes nothing (no doubled text)."""
    t = (title or "").strip()
    if not t:
        return t
    prefix, location = (prefix or "").strip(), (location or "").strip()
    if pos not in ("start", "before_title", "end"):
        date, pos = "", ("before_title" if location else "off")
    date = title_dateline(location, date, loc_sep)  # from here on, "date" is the whole dateline
    use_date = bool(date) and date.casefold() not in t.casefold()
    has_prefix = bool(prefix) and (t.casefold().startswith(prefix.casefold()) or
                                   (bool(date) and t.casefold().startswith(_date_before(date, prefix, sep).casefold())))
    if use_date and pos == "before_title":
        t = _date_before(date, t, sep)
    if use_date and pos == "end":
        t = _date_after(t, date, sep)
    if prefix and not has_prefix:
        t = apply_title_prefix(prefix, t)
    if use_date and pos == "start":
        t = _date_before(date, t, sep)
    return t


def title_pieces(title: str, prefix: str = "", date: str = "", pos: str = "off", sep: str = "dash",
                 location: str = "", loc_sep: str = "dash") -> list[tuple[str, str]]:
    """The composed title split into labelled pieces for the preview: ('prefix'|'location'|'date'|
    'title'|'sep', text). Joined together they're exactly compose_title's result."""
    marks = {"prefix": "\x01P\x02", "location": "\x01L\x02", "date": "\x01D\x02", "title": "\x01T\x02"}
    real = {"prefix": (prefix or "").strip(), "location": (location or "").strip(), "date": date, "title": title}
    shape = compose_title(marks["title"], marks["prefix"] if real["prefix"] else "", marks["date"] if date else "",
                          pos, sep, marks["location"] if real["location"] else "", loc_sep)
    pieces = []
    for part in re.split(r"(\x01[PLDT]\x02)", shape):
        if not part:
            continue
        kind = next((k for k, m in marks.items() if m == part), None)
        pieces.append((kind, real[kind]) if kind else ("sep", part))
    full = compose_title(title, prefix, date, pos, sep, location, loc_sep)
    return pieces if "".join(t for _, t in pieces) == full else [("title", full)]


# the location in an editorial dateline: anything up to 80 characters that isn't a spaced dash (so it never
# swallows part of the title) and has no colon or brackets ("Saint-Tropez, France" is fine)
_DL_LOC = r"(?:(?!\s+[|–—-]\s)[^:()\[\]\n]){1,80}?"


def _dateline_res(date: str):
    """Patterns for 'Any Location - <date>' at the start of a title, and '- Any Location - <date>' or
    '(Any Location - <date>)' at the end. Group 1 is the location."""
    d = re.escape(date)
    start = re.compile(r"^\s*[\(\[]?\s*(" + _DL_LOC + r")\s*[,;|–—-]?\s*" + d + r"\s*[\)\]]?\s*[:,;|–—-]*\s*(?=\S)",
                       re.I)                                          # (a title must follow)
    end = re.compile(r"\s+(?:[|–—-]+\s*|[\(\[]\s*)(" + _DL_LOC + r")\s*[,;|–—-]?\s*" + d + r"\s*[\)\]]?\s*$",
                     re.I)
    return start, end


def dateline_location(title: str, prefix: str = "", date: str = "") -> str:
    """The location written in front of (or after) the date in an editorial title, whatever it is:
    'Kyoto, Japan - October 26, 2026: Temple Gate' -> 'Kyoto, Japan'. '' when there's none."""
    t = (title or "").strip()
    if not date or not t:
        return ""
    start, end = _dateline_res(date)
    for x in (t, t[len(prefix.strip()):].strip() if prefix.strip() and t.casefold().startswith(
            prefix.strip().casefold()) else ""):
        m = start.search(x) if x else None
        if m:
            return m.group(1).strip(" ,;|–—-")
    m = end.search(t)
    return m.group(1).strip(" ,;|–—-") if m else ""


def strip_title_extras(title: str, prefix: str = "", date: str = "", location: str = "",
                       any_location: bool = False) -> str:
    """The bare title: remove your text before titles, the location and the date taken (in any of the
    positions and separators compose_title uses), so only the description itself is left.
    any_location: also remove a location that isn't `location` when it sits right next to the date
    (a title from an earlier session, before the location was changed)."""
    t = (title or "").strip()
    generic = _dateline_res(date) if (any_location and date) else None
    seps = r"[\s,;:|–—-]*"
    location = (location or "").strip()
    pats = []
    if location and date:                                        # "Location - Date" (any joiner)
        pats.append(re.escape(location) + r"\s*[,;|–—-]?\s*" + re.escape(date))
    if date:
        pats.append(re.escape(date))

    def drop_start(x):
        if generic:
            x = generic[0].sub("", x, count=1)
        for p in pats:
            x = re.sub(r"^\s*[\(\[]?\s*" + p + r"\s*[\)\]]?" + seps, "", x, flags=re.I)
        if location:   # the location on its own only counts when punctuation follows it ("Tokyo:" not "Tokyo Tower")
            x = re.sub(r"^\s*" + re.escape(location) + r"\s*[,;:|–—-]+\s*", "", x, flags=re.I)
        return x

    t = drop_start(t)                                            # location/date at the very start
    if prefix and t.casefold().startswith(prefix.strip().casefold()):
        t = t[len(prefix.strip()):].strip()                     # your text before titles
    t = drop_start(t)                                            # location/date just before the title
    if generic:
        t = generic[1].sub("", t, count=1)
    for p in pats:                                               # location/date after the title
        t = re.sub(r"[\s,;|–—-]*[\(\[]?\s*" + p + r"\s*[\)\]]?\s*$", "", t, flags=re.I)
    if location:
        t = re.sub(r"\s*[,;|–—-]+\s*" + re.escape(location) + r"\s*$", "", t, flags=re.I)
    return t.strip(" ,;:|–—-")


def drop_trailing_location(title: str, location: str) -> str:
    """'Pier at Sunset in Tokyo, Japan' -> 'Pier at Sunset' when that location is added
    to the title separately (so the place isn't in the title twice)."""
    location = (location or "").strip()
    if not location or not title:
        return title
    parts = [re.escape(location)]
    first = location.split(",")[0].strip()
    if first and first != location:
        parts.append(re.escape(first) + r"(?:\s*,[^,]{1,40}){0,2}")        # "in Tokyo, Japan" with a shorter location form
    for p in parts:
        new = re.sub(r"\s*,?\s+(?:in|at|near|on|of|from)\s+(?:the\s+)?" + p + r"\s*$", "", title, flags=re.I)
        if new != title and new.strip():
            return new.strip()
    return title


def apply_title_prefix(prefix: str, title: str) -> str:
    """Put the prefix in front of the title (exactly as typed), unless it's already there."""
    prefix, title = (prefix or "").strip(), (title or "").strip()
    if not prefix:
        return title
    if not title:
        return title
    if title.casefold().startswith(prefix.casefold()):
        return title
    joiner = "" if prefix.endswith(("(", "/", "#")) else " "
    return prefix + joiner + title


def parse_pasted_text(text: str) -> tuple[str | None, list[str] | None]:
    """Read pasted text as (title, keywords). The usual layout is the title on the first line and
    the keywords (comma separated) after it. 'Title:' / 'Keywords:' labels are understood.
    A single line with several commas is keywords only; any other single line is a title."""
    lines = [ln.strip() for ln in (text or "").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return None, None
    label = re.compile(r"^\s*(title|headline|keywords?|tags)\s*[:\-–]\s*", re.I)
    title, kw_lines = None, []
    for ln in lines:
        m = label.match(ln)
        if m:
            body = ln[m.end():].strip()
            if m.group(1).lower() in ("title", "headline"):
                title = body
            else:
                kw_lines.append(body)
            continue
        if title is None and not kw_lines and (len(lines) > 1 or ln.count(",") < 2):
            title = ln
        else:
            kw_lines.append(ln)
    keywords = parse_keywords_text(", ".join(kw_lines)) if kw_lines else None
    return (clean_title(title) if title else None), (keywords if keywords else None)


def editorial_keywords(location: str = "", taken: str = "") -> list[str]:
    """Keywords for Editorial: the location's parts and the month and year taken,
    e.g. 'Tokyo, Japan' + 2026-10-26 -> ['tokyo', 'japan', 'october 2026', '2026']."""
    out = [p.strip().casefold() for p in (location or "").split(",") if p.strip()]
    m = re.match(r"(\d{4})-(\d{2})-\d{2}$", taken or "")
    if m:
        y, mo = m.group(1), int(m.group(2))
        if 1 <= mo <= 12:
            out += [f"{MONTHS[mo - 1].casefold()} {y}", y]
    return clean_keywords(out)


# With Editorial (location) or the date turned off, the AI mustn't see or use them --------------------------
_MONTH_ALT = "|".join(MONTHS) + "|" + "|".join(m[:3] for m in MONTHS) + "|Sept"
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_FULL_DATE_RES = [re.compile(p, re.I) for p in (
    rf"(?:(?:{'|'.join(WEEKDAYS)}),?\s+)?(?:{_MONTH_ALT})\.?\s+{_DAY},?\s+\d{{4}}\b",   # (Monday,) October 26, 2026
    rf"\b{_DAY}\s+(?:{_MONTH_ALT})\.?,?\s+\d{{4}}\b",                                  # 26 October 2026
    r"\b\d{4}-\d{2}-\d{2}\b",                                                          # 2026-10-26
    r"\b\d{1,2}[/.]\d{1,2}[/.]\d{4}\b",                                                # 10/26/2026, 26.10.2026
    rf"\b(?:{_MONTH_ALT})\.?,?\s+\d{{4}}\b",                                           # October 2026
)]
_LEAD_WORDS = r"(?:\s*,?\s+(?:on|in|during|from|taken|dated)\s+)?"


def _year_of(dates) -> set[str]:
    return {m.group(1) for d in dates or () for m in [re.match(r"(\d{4})-\d{2}-\d{2}$", d or "")] if m}


def is_date_keyword(keyword: str, years=()) -> bool:
    """A keyword that is a date: a full date, a month and year ('october 2026'), or one of these years."""
    k = " ".join(str(keyword or "").split())
    if not k:
        return False
    if k in set(years or ()):
        return True
    return any(r.fullmatch(k) for r in _FULL_DATE_RES)


def _place_terms(locations) -> set[str]:
    """Every keyword form of these locations: the whole thing and each part ('tokyo', 'japan', 'tokyo japan')."""
    out = set()
    for loc in locations or ():
        parts = [" ".join(p.split()).casefold() for p in str(loc or "").split(",") if p.strip()]
        if not parts:
            continue
        out.update(parts)
        out.add(" ".join(parts))
        for p in parts[1:]:
            out.add(f"{parts[0]} {p}")
    return {t for t in out if t}


def _mentioned(term: str, text: str) -> bool:
    return bool(term) and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text or "", re.I) is not None


def strip_dates(text: str, years=(), edges: bool = False) -> str:
    """Take full dates and month-and-year dates out of a title or description (with an 'on' / 'in' before
    them). edges: also a bare year of the photo at the very start or end ('... - 2026')."""
    t = text or ""
    for r in _FULL_DATE_RES:
        t = re.sub(_LEAD_WORDS + r"[\(\[]?" + r.pattern + r"[\)\]]?", "", t, flags=re.I)
    if edges:
        for y in years or ():
            t = re.sub(r"^\s*" + y + r"\b[\s,;:|–—-]*", "", t)
            t = re.sub(r"[\s,;:|–—-]*\(?\b" + y + r"\)?\s*(?=\.?$)", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    return t.strip(" ,;:|–—-") + ("." if (text or "").rstrip().endswith(".") and not t.rstrip().endswith(".") else "")


def strip_place(text: str, location: str) -> str:
    """Take a location out of a title or description: at the start or end (any separator), or as 'in <place>'."""
    loc = " ".join(str(location or "").split())
    if not loc or not text:
        return text
    dot = text.rstrip().endswith(".")
    t = strip_title_extras(text.rstrip().rstrip("."), location=loc)
    t = drop_trailing_location(t, loc)
    first = loc.split(",")[0].strip()
    for p in dict.fromkeys([re.escape(loc), re.escape(first) + r"(?:\s*,\s*[^,.]{1,30}){0,2}" if first else ""]):
        if p:
            t = re.sub(r"\s*,?\s+(?:in|at|near|from)\s+(?:the\s+)?" + p + r"(?=[\s,.;:!?)]|$)", "", t, flags=re.I)
    t = t.strip(" ,;:|–—-") or text
    return t + ("." if dot and not t.endswith(".") else "")


def scrub_place_date(title: str, description: str, keywords, locations=(), dates=(), drop_place: bool = True,
                     drop_date_keywords: bool = True, edge_years: bool = False, keep_text: str = ""):
    """What the AI is given and what comes back, with the location and/or date taken out (Editorial or the
    date turned off). Anything the photographer wrote themselves (keep_text: instructions, photo note, keywords
    to put first) stays. Returns (title, description, keywords)."""
    years = {y for y in _year_of(dates) if not _mentioned(y, keep_text)}
    locs = [loc for loc in dict.fromkeys(" ".join(str(x).split()) for x in (locations or ()) if str(x).strip())
            if not _mentioned(loc.split(",")[0].strip(), keep_text)] if drop_place else []
    terms = {t for t in _place_terms(locs) if not _mentioned(t, keep_text)}
    t, d = title or "", description or ""
    for loc in sorted(locs, key=len, reverse=True):
        t, d = strip_place(t, loc), strip_place(d, loc)
    t, d = strip_dates(t, years, edges=edge_years), strip_dates(d, years) if d else d
    kws = []
    for k in keywords or ():
        low = " ".join(str(k).split()).casefold()
        if drop_place and low in terms:
            continue
        if drop_date_keywords and is_date_keyword(low, years) and not _mentioned(low, keep_text):
            continue
        kws.append(k)
    return (t or title or ""), d, kws


NO_DATE_RULE = ("Don't put a date, month or year in the title, description or keywords unless the photographer's "
                "instructions or notes give one.")


def place_editorial_keywords(keywords, extra, pos: str = "before", after: int = 0, title: str = "") -> list[str]:
    """Add the Editorial keywords where they're relevant instead of always first: after your keywords to put
    first (the first `after`) and the keywords that name what's in the title, the place; the month and year
    at the end. (Before/after only moves the place and date in the title.) Ones already in the list aren't
    added twice; a suggestion like "tokyo" joins the place keywords."""
    keywords = clean_keywords(keywords)
    head = keywords[:after]
    taken = {k.casefold() for k in head}
    extra = [k for k in clean_keywords(extra) if k.casefold() not in taken]
    dates = [k for k in extra if re.fullmatch(r"(?:[a-z]+ )?\d{4}", k.casefold())]
    places = [k for k in extra if k not in dates]
    moved = {k.casefold() for k in extra}
    rest = [k for k in keywords[after:] if k.casefold() not in moved]
    if not extra:
        return head + rest                            # nothing to add: the order is left exactly as it was
    words = {_singular(w) for w in re.findall(r"[\w']+", (title or "").casefold().replace("'", ""))
             if w not in SMALL_WORDS}

    def names_title(k):
        ws = [_singular(w) for w in k.casefold().split() if w not in SMALL_WORDS]
        return bool(words) and bool(ws) and all(w in words for w in ws)
    # the place goes right after the last of the top keywords that name what's in the title
    # (or after the top three when none do); the rest keep their order
    hits = [i for i, k in enumerate(rest[:10]) if names_title(k)]
    at = hits[-1] + 1 if hits else min(3, len(rest))
    return head + rest[:at] + places + rest[at:] + dates


def expand_lead_keywords(text: str, taken: str = "") -> list[str]:
    """Keywords to put first, exactly as typed, with {year} {month} {date} filled in from the
    photo's capture date (YYYY-MM-DD). A keyword whose date isn't known is left out."""
    out = []
    for k in parse_keywords_text(text or ""):
        filled = fill_tokens(k, taken)
        if filled:                      # unknown token or no capture date: leave it out
            out.append(filled)
    return clean_keywords(out)


def combine_keywords(lead, existing, new, keyword_max: int) -> list[str]:
    """Final keyword order: your leading keywords, then the photo's existing keywords, then the
    AI's suggestions. Leading and existing keywords are always kept; suggestions fill up to
    keyword_max on top of the leading ones."""
    lead = clean_keywords(lead)
    base = merge_keywords(lead, existing)
    room = len(lead) + max(keyword_max, len(clean_keywords(existing)))
    suggestions = [k for k in seo_keywords(new, keep=base) if k]
    return merge_keywords(base, suggestions)[:max(room, len(base))]


# --------------------------------------------------------------------------- AI recognition (Ollama)

class ApiError(Exception):
    pass


SYSTEM_PROMPT = ("You write titles and keywords for stock photos so they rank well in search. "
                 "Reply with JSON only: the rewritten title and the keyword list, no explanations.")


def system_prompt(direction: str = "") -> str:
    """The system message. Your directions go here too, because models give it the most weight."""
    if not (direction or "").strip():
        return SYSTEM_PROMPT
    return (SYSTEM_PROMPT + "\n\nThe photographer's instructions below are your highest priority. Treat any facts "
            "in them as true, and follow each direction exactly: they override every default rule (title style, "
            "length, wording and keyword rules):\n"
            + direction.strip())

EMPTY_REPLY = "The AI model returned no title or keywords for this photo. Try again or try another model."
MAX_REPLY_TOKENS = 700   # a full title + 40 keywords is ~250 tokens

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "keywords"],
}

TITLE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}

DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"},
                   # the Shutterstock categories, chosen while the model is looking at the photo anyway
                   "categories": {"type": "array", "items": {"type": "string", "enum": SHUTTERSTOCK_CATEGORIES}},
                   "adobe_category": {"type": "integer", "enum": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
                                                                  15, 16, 17, 18, 19, 20, 21]}},
    "required": ["description", "categories", "adobe_category"],
}
DESCRIPTION_MAX = 200       # Shutterstock's limit for the description; Alamy and Dreamstime take this too


def shutterstock_categories(value) -> list[str]:
    """Up to two valid Shutterstock category names from whatever the model (or a saved list) gave,
    matched without caring about case or spacing."""
    known = {re.sub(r"[^a-z]", "", c.casefold()): c for c in SHUTTERSTOCK_CATEGORIES}
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    out = []
    for v in items:
        c = known.get(re.sub(r"[^a-z]", "", str(v).casefold()))
        if c and c not in out:
            out.append(c)
    return out[:2]


# ----- common problems in what the AI wrote, fixed without asking it again --------------------------------------
# Stores and their own brands. A photo taken in one of them shouldn't get another chain's name in its title or
# keywords (a BJ's photo with "costco", "kirkland" or "krogerpay"), unless that name is really on the label or in
# your notes.
STORE_CHAINS = {
    "walmart": ["walmart", "wal mart", "great value", "equate", "mainstays"],
    "costco": ["costco", "kirkland"],
    "bjs": ["bjs", "bj s", "bjs wholesale", "wellsley farms", "berkley jensen"],
    "sams club": ["sams club", "sam s club", "members mark"],
    "kroger": ["kroger", "krogerpay", "kroger pay"],
    "target": ["target store", "up and up", "good and gather"],
    "aldi": ["aldi"], "lidl": ["lidl"], "publix": ["publix"], "food lion": ["food lion"],
    "harris teeter": ["harris teeter"], "whole foods": ["whole foods"], "trader joes": ["trader joes", "trader joe s"],
    "wegmans": ["wegmans"], "safeway": ["safeway"], "meijer": ["meijer"], "heb": ["h e b"],
    "cvs": ["cvs"], "walgreens": ["walgreens"], "rite aid": ["rite aid"],
    "home depot": ["home depot", "husky tools", "hdx"], "lowes": ["lowes", "lowe s", "kobalt"],
    "ace hardware": ["ace hardware", "westlake ace", "clark kensington"], "menards": ["menards"],
    "true value": ["true value"], "harbor freight": ["harbor freight"], "tractor supply": ["tractor supply"],
    "sheetz": ["sheetz"], "wawa": ["wawa"],
}
STORE_DISPLAY = {   # how a store is written in a title
    "walmart": "Walmart", "costco": "Costco", "bjs": "BJ's Wholesale Club", "sams club": "Sam's Club",
    "kroger": "Kroger", "target": "Target", "aldi": "Aldi", "lidl": "Lidl", "publix": "Publix",
    "food lion": "Food Lion", "harris teeter": "Harris Teeter", "whole foods": "Whole Foods Market",
    "trader joes": "Trader Joe's", "wegmans": "Wegmans", "safeway": "Safeway", "meijer": "Meijer", "heb": "H-E-B",
    "cvs": "CVS", "walgreens": "Walgreens", "rite aid": "Rite Aid", "home depot": "The Home Depot",
    "lowes": "Lowe's", "ace hardware": "Ace Hardware", "menards": "Menards", "true value": "True Value",
    "harbor freight": "Harbor Freight", "tractor supply": "Tractor Supply", "sheetz": "Sheetz", "wawa": "Wawa",
}
# A store's own brands: they say whose products these are, not where the photo was taken
HOUSE_BRANDS = {"great value", "equate", "mainstays", "kirkland", "wellsley farms", "berkley jensen", "members mark",
                "up and up", "good and gather", "husky tools", "hdx", "kobalt", "clark kensington", "westlake ace"}
HARDWARE_STORES = {"home depot", "lowes", "ace hardware", "menards", "true value", "harbor freight", "tractor supply"}

# Keywords for things that have to be in the title or description to belong in the keywords (the AI adds them to
# almost every store photo): keyword -> words that show it's really there
NEEDS_EVIDENCE = {
    "shopping cart": ["cart"], "shopping carts": ["cart"], "customer service": ["service desk", "customer service"],
    "supply chain": ["supply chain"], "food distribution": ["distribution"], "clinical trial": ["clinical"],
    "clinical trial results": ["clinical"], "customer shopping": ["shopper", "customer", "people", "person"],
    "customer browsing": ["shopper", "customer", "people", "person"], "customers": ["shopper", "customer", "people"],
    "shoppers": ["shopper", "customer", "people"], "produce section": ["produce"], "bakery section": ["bakery"],
    "dairy section": ["dairy"], "cold storage": ["refrigerat", "cooler", "freezer"], "teamwork": ["team"],
    "warehouse": ["warehouse", "wholesale", "club"], "warehouse store": ["warehouse", "wholesale", "club"],
    "warehouse shelves": ["warehouse", "wholesale", "club"], "warehouse storage": ["warehouse"],
    "warehouse interior": ["warehouse", "wholesale", "club"], "storefront": ["storefront", "exterior", "facade",
                                                                           "entrance", "outside"],
    "machine vision system": ["machine vision"], "industrial automation": ["automation"],
}
_PACKAGE_COUNTS = (r"calories|pouches|servings|pancakes|pieces|count|ct|cookies|bars|packets|sticks|tablets|capsules|"
                   r"caplets|softgels|gummies|wipes|sheets|rolls|loads|cups|pods|bags|grams|mg")
_JUNK_KEYWORD = [
    (re.compile(r"^\$?\d+[ .,]\d{2}$|^\$\d|(?:^| )save \$?\d+|\d+ (?:off|percent off)$|^was \d"), "a price"),
    (re.compile(r"\b(?:date code|best before|best by|use by|lot (?:number|code)|batch code|exp(?:iration)? date|"
                r"upc|sku|item number|model number)\b"), "a date or product code"),
    # cs1301, ab12345, 0933 (model names like f150 or a350 are kept)
    (re.compile(r"\b(?=\w*[a-z])\w*\d{4,}\w*\b|\b0\d{2,}\b|\b[a-z]\d \d{3,}\b|\b[a-z]\d{3} \d+x\b"), "a product code"),
    (re.compile(r"^\d+ (?:" + _PACKAGE_COUNTS + r")$"), "a number from the package"),
    (re.compile(r"^\d+ in (?:\d+|minutes?)$|^\d+$"), "a stray number"),
]


def _norm_words(text: str) -> str:
    """'BJ’s Wholesale-Club' -> ' bjs wholesale club ' (for matching names in any spelling)."""
    t = plain_text(text or "").casefold().replace("'", "")
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", t).split()) + " "


_PLACE_WORDS = r"(?:store|stores|wholesale|club|market|supermarket|warehouse|interior|exterior|location|aisle|aisles|" \
               r"setting|hardware|pharmacy|storefront|entrance|parking|checkout|shop)"


def store_here(text: str) -> str:
    """The store a title says the photo was taken in. With two named (Costco Baking Nuts…, BJS Wholesale Store
    Setting), the one named as a place (followed by store, wholesale, club, interior…) wins over one used like a
    brand; otherwise the first."""
    found = stores_in(text, brands=False)
    if len(found) < 2:
        return found[0] if found else ""
    t = _norm_words(text)
    for chain in found:
        for n in STORE_CHAINS[chain]:
            if n not in HOUSE_BRANDS and re.search(r" " + re.escape(n) + r" (?:\w+ ){0,2}?" + _PLACE_WORDS + r" ", t):
                return chain
    return found[0]


def stores_in(text: str, brands: bool = True) -> list[str]:
    """The store chains named in this text, in the order they appear. brands=False: only by the store's own name
    (Kirkland on a bag doesn't mean the photo was taken at Costco)."""
    t = _norm_words(text)
    found = []
    for chain, names in STORE_CHAINS.items():
        at = [t.find(f" {n} ") for n in names if f" {n} " in t and (brands or n not in HOUSE_BRANDS)]
        if at:
            found.append((min(at), chain))
    return [c for _, c in sorted(found)]


def junk_keyword(keyword: str) -> str:
    """Why a keyword is no use to a buyer (a price, a date or product code, a number off the package), or ''."""
    k = _norm_words(keyword).strip()
    if re.fullmatch(r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* )?(?:19|20)\d\d", k):
        return ""                                      # a year, or month and year
    for pattern, why in _JUNK_KEYWORD:
        if pattern.search(k):
            return why
    return ""


# Big parent companies the AI likes to add to titles, often wrongly (BelVita "by General Mills"; it's Mondelez)
PARENT_COMPANIES = [
    "General Mills", "Kraft Heinz", "Kraft", "Heinz", "Mondelez", "Mondelez International", "Nestle", "PepsiCo",
    "Frito-Lay", "Coca-Cola Company", "Unilever", "Procter and Gamble", "Procter & Gamble", "P&G",
    "Johnson and Johnson", "Johnson & Johnson", "Kenvue", "Kellogg", "Kellogg's", "Kellanova", "WK Kellogg",
    "Conagra", "Conagra Brands", "Campbell", "Campbell's", "Campbell Soup Company", "Hormel", "Hormel Foods",
    "Del Monte", "Del Monte Foods", "Mars", "Mars Wrigley", "Hershey", "The Hershey Company", "Ferrero",
    "J.M. Smucker", "Smucker", "Smucker's", "Post Holdings", "Post Consumer Brands", "Danone", "Keurig Dr Pepper",
    "Colgate-Palmolive", "Clorox", "SC Johnson", "Reckitt", "Haleon", "Bayer", "Pfizer", "Kimberly-Clark",
    "Church and Dwight", "Church & Dwight", "Spectrum Brands", "Stanley Black and Decker", "Stanley Black & Decker",
    "Sherwin-Williams", "RPM International", "PPG", "Newell Brands", "Tyson", "Tyson Foods", "Smithfield",
    "Hostess Brands", "Flowers Foods", "Grupo Bimbo", "Bimbo Bakeries", "Lactalis", "Molson Coors",
    "Anheuser-Busch", "AB InBev", "Constellation Brands", "Diageo", "PIM Brands", "B&G Foods", "McCormick",
    "TreeHouse Foods", "Utz Brands", "Lamb Weston", "Simply Good Foods", "BellRing Brands", "Hain Celestial",
    "Edgewell", "Energizer Holdings", "Henkel", "L'Oreal", "Estee Lauder", "Coty", "Beiersdorf", "Mattel", "Hasbro",
]


def drop_unproven_parents(text: str, proof_n: str) -> str:
    """Take a parent company out of a title or description unless it's in proof_n (the normalised label text and
    notes): ", a General Mills Brand", "by General Mills", "from Kraft Heinz", "(General Mills)". A brand named on
    its own ("Hormel Chili with Beans") is never touched: only a claim about who owns or makes it."""
    if not text:
        return text
    names = sorted(PARENT_COMPANIES, key=len, reverse=True)
    any_name = "|".join(r"\s+".join(map(re.escape, n.split())) for n in names)
    claims = [  # (pattern, the group with the company)
        r",?\s*(?:an?|the)\s+(?P<p>[A-Z][\w&'’.\- ]{1,40}?)\s+(?:Brand|Product|Company)\b(?:\s*,(?=\s))?",
        # "…, BJ's Wholesale Club Brand" (a store or company claimed as the brand, with no "a")
        r",\s*(?P<p>[A-Z][\w&'’.\- ]{1,40}?)\s+(?:Brand|Product)\b(?=\s*(?:,|$))",
        r",?\s*(?:made|owned|produced|manufactured|distributed)?\s*(?:by|from)\s+(?P<p>" + any_name + r")\b",
        r"\s*\((?P<p>" + any_name + r")\)",
    ]
    if proof_n.strip():
        # "Campbell's StoveTop Stuffing": a parent company put in front of another brand, when the label was read
        # and doesn't show it (a brand on its own, "Hormel Chili", has no 's and is never touched)
        claims.append(r"(?:^|(?<=\s))(?P<p>" + any_name + r")['’]s\s+(?=[A-Z])")
    for rx in claims:
        def drop(m):
            p = m.group("p")
            ok = _norm_words(p) in proof_n or _norm_words(p + "s") in proof_n or _norm_words(p).strip() == ""
            return m.group(0) if ok else ""
        text = re.sub(rx, drop, text, flags=re.I if ("an?" not in rx and "Brand|Product)" not in rx) else 0)
    text = re.sub(r"\s+([,.;:])", r"\1", " ".join(text.split()))
    return text.strip(" ,;:-")


# Adobe Stock's 21 categories (the number goes in its CSV) and what each is for
ADOBE_CATEGORIES = {
    1: "Animals", 2: "Buildings and Architecture", 3: "Business", 4: "Drinks", 5: "The Environment",
    6: "States of Mind", 7: "Food", 8: "Graphic Resources", 9: "Hobbies and Leisure", 10: "Industry",
    11: "Landscapes", 12: "Lifestyle", 13: "People", 14: "Plants and Flowers", 15: "Culture and Religion",
    16: "Science", 17: "Social Issues", 18: "Sports", 19: "Technology", 20: "Transport", 21: "Travel",
}


def adobe_category(value) -> int | None:
    """A valid Adobe Stock category number from what the AI (or a saved list) gave, else None."""
    try:
        n = int(str(value).strip().split()[0].rstrip(".:"))
    except (ValueError, IndexError):
        low = str(value or "").strip().casefold()
        n = next((k for k, v in ADOBE_CATEGORIES.items() if v.casefold() == low), 0)
    return n if n in ADOBE_CATEGORIES else None


CATEGORY_MEANINGS = {   # what each Shutterstock category is for, so it isn't picked for the wrong thing
    "Abstract": "patterns, shapes and colours with no real subject", "Animals/Wildlife": "animals and pets",
    "Arts": "art, crafts and art supplies", "Backgrounds/Textures": "a surface or texture filling the frame",
    "Beauty/Fashion": "cosmetics, hair, clothing and style", "Buildings/Landmarks": "buildings, storefronts seen from "
    "outside, architecture and landmarks (not products on a shelf)", "Business/Finance": "business, money, shops "
    "and retail", "Celebrities": "famous people who are in the photo", "Education": "school and learning",
    "Food and drink": "food, drinks and groceries", "Healthcare/Medical": "medicine, health products and care",
    "Holidays": "Christmas, Halloween, Thanksgiving and other holidays", "Industrial": "industry, tools, machines, "
    "fuel and building supplies", "Interiors": "the inside of a room or a whole store", "Miscellaneous": "anything "
    "else", "Nature": "plants, trees, leaves, landscapes and weather", "Objects": "products and things, including "
    "products on a shelf", "Parks/Outdoor": "parks, gardens and outdoor places", "People": "people who are in the "
    "photo", "Religion": "religion", "Science": "science and labs", "Signs/Symbols": "signs, logos and symbols",
    "Sports/Recreation": "sport, fitness and hobbies", "Technology": "electronics and computers",
    "Transportation": "cars, roads, fuel stations and travel", "Vintage": "old or retro things",
}
# A category is only kept when the title, description or top keywords show it fits (the AI often adds People,
# Celebrities or Parks/Outdoor to a box on a shelf). Not listed = always allowed.
CATEGORY_EVIDENCE = {
    "People": ["people", "person", "man", "men", "woman", "women", "child", "children", "kid", "kids", "boy", "girl",
               "shopper", "shoppers", "customer", "customers", "crowd", "worker", "family", "couple", "portrait"],
    "Celebrities": ["celebrity", "actor", "actress", "singer", "musician", "athlete", "politician"],
    "Parks/Outdoor": ["park", "garden", "outdoor", "outdoors", "yard", "lawn", "trail", "beach", "playground",
                      "patio", "camping", "hiking", "picnic", "street", "outside"],
    "Sports/Recreation": ["sport", "sports", "fitness", "gym", "workout", "exercise", "protein", "athlete", "game",
                          "ball", "bike", "bicycle", "fishing", "camping", "hiking", "golf", "running", "toy", "toys"],
    "Buildings/Landmarks": ["building", "buildings", "storefront", "exterior", "facade", "architecture", "landmark",
                            "house", "church", "bridge", "tower", "skyline", "entrance", "street"],
    "Nature": ["nature", "leaf", "leaves", "tree", "trees", "forest", "flower", "flowers", "plant", "plants", "sky",
               "rain", "snow", "river", "lake", "mountain", "landscape", "autumn", "fall foliage", "garden", "water"],
    "Healthcare/Medical": ["medicine", "medical", "pharmacy", "health", "treatment", "drug", "hospital", "doctor",
                           "vitamin", "vitamins", "supplement", "pain", "relief", "first aid", "minoxidil", "regrowth",
                           "allergy", "cold", "flu", "bandage", "dental", "clinic", "healthcare", "pharmaceutical"],
    "Signs/Symbols": ["sign", "signs", "signage", "logo", "symbol", "emblem", "billboard", "banner", "label"],
    "Holidays": ["christmas", "halloween", "thanksgiving", "easter", "valentine", "hanukkah", "holiday", "holidays",
                 "new year", "fourth of july", "st patrick", "gingerbread", "festive", "candy cane", "santa"],
    "Interiors": ["interior", "inside", "room", "aisle", "kitchen", "bathroom", "bedroom", "store interior",
                  "living room", "office", "lobby", "display", "studio"],
    "Animals/Wildlife": ["animal", "animals", "dog", "cat", "bird", "pet", "pets", "wildlife", "horse", "fish"],
    "Transportation": ["car", "cars", "vehicle", "truck", "suv", "sedan", "road", "fuel", "gas", "pump", "train",
                       "bus", "airplane", "plane", "boat", "traffic", "parking", "bicycle", "automobile", "diesel"],
    "Technology": ["technology", "computer", "phone", "smartphone", "laptop", "electronic", "electronics", "tablet",
                   "camera", "headphones", "tv", "television", "software", "battery", "charger", "led", "smart"],
    "Education": ["school", "education", "book", "books", "classroom", "student", "learning", "study"],
    "Religion": ["church", "temple", "mosque", "religion", "religious", "prayer", "bible", "cross"],
    "Science": ["science", "laboratory", "lab", "microscope", "experiment", "research"],
    "Vintage": ["vintage", "retro", "antique", "old fashioned", "classic"],
    "Abstract": ["abstract", "pattern", "texture"],
    "Backgrounds/Textures": ["background", "texture", "pattern", "surface"],
}
_PRODUCT_WORDS = ["shelf", "shelves", "display", "package", "packaging", "box", "boxes", "can", "cans", "bottle",
                  "bottles", "jar", "jars", "bag", "bags", "pack", "product", "products", "retail", "carton"]


def fit_categories(categories, title: str = "", description: str = "", keywords=()) -> list[str]:
    """The Shutterstock categories that fit (at most two): ones the text gives no sign of are taken out, and a
    product photo that's left with none, or with only one, gets Objects."""
    text = _norm_words(" ".join([title or "", description or "", " ".join(list(keywords or [])[:12])]))
    # "Interior Exterior Paint" on a can says nothing about a room or a building
    text = re.sub(r" (?:interior(?: and)? exterior|exterior(?: and)? interior|interior|exterior)"
                  r"(?= (?:paint|primer|enamel|stain|finish|latex|acrylic|use|protection|sealer|wood|coating)\b)",
                  " ", text)
    out = []
    for c in shutterstock_categories(list(categories or [])):
        needs = CATEGORY_EVIDENCE.get(c)
        if needs and not any(f" {w} " in text for w in needs):
            continue
        out.append(c)
    product = any(f" {w} " in text for w in _PRODUCT_WORDS)
    if product and "Objects" not in out and len(out) < 2:
        out.append("Objects")
    if not out:
        out = ["Miscellaneous"]
    return out[:2]


def fix_common_problems(title: str, description: str, keywords, categories=(), known: str = "",
                        label: str = "", check_parents: bool = False) -> dict:
    """Fix the mistakes the AI often makes, without asking it again. known: text that is true for sure (your notes,
    instructions), so nothing in it is ever taken out. label: the text read off the product, which keeps brand and
    store names but not prices, codes or numbers copied off the package.
      - the end of an answer run on into the title (`…Shelf”, “Keywords`) and quotes that don't pair up
      - another store's name or house brand in the title of a photo from a named store (Costco at BJ's);
        related stores and brands are fine in the keywords
      - with check_parents: a parent company in the title (", a General Mills Brand", "by General Mills") that
        isn't printed on the package (label) or in your notes
      - "grocery" at a hardware store
      - prices, date and product codes and numbers off the package as keywords (13 79, l2 0933, 150 calories)
      - "frozen" keywords for canned or boxed food
      - keywords for things not in the photo that the AI adds to store photos (shopping cart, supply chain)
      - categories that don't fit (People or Celebrities with nobody in the photo, Parks/Outdoor for a box)
    Returns {'title', 'description', 'keywords', 'categories', 'fixes': [what was changed, one line each]}."""
    fixes = []
    t0, d0 = title or "", description or ""
    title, description = clean_title(t0), fix_stray_quotes(norm_description(d0))
    if title != single_line(t0).strip().strip(_QUOTES).strip():
        fixes.append("took leftover text or a stray quote out of the title")
    own_n = _norm_words(known)                         # yours: keeps anything
    known_n = _norm_words(" ".join([known or "", label or ""]))
    mine = stores_in(known or "", brands=False)        # a store in your notes or Instructions decides it
    store = mine[0] if mine else store_here(title)    # otherwise the store the title names as the place
    if not store:                                     # last: a store's own brand (Clark+Kensington is Ace's)
        store = next(iter(stores_in(title)), "")
    if store:
        # another chain's name or house brand comes out of the title, even when the label reader thought it saw
        # it, if your notes say where the photo was taken (Kirkland at BJ's); otherwise the label keeps it
        proof = own_n if mine else known_n
        others = [n for c, names in STORE_CHAINS.items() if c != store for n in names if f" {n} " not in proof]
        name_rx = [re.compile(r"(?<![\w])" + r"[\s\-]*".join(r"[’']?".join(map(re.escape, part)) for part in n.split())
                              + r"(?:[’']s)?(?![\w])", re.I) for n in sorted(others, key=len, reverse=True)]

        def strip_other(text):
            for rx in name_rx:
                text = rx.sub("", text)
            text = re.sub(r"\s+([,.;:])", r"\1", " ".join(text.split()))
            # a joining word left with nothing after it ("…Featuring" at the end, "with ," in the middle)
            text = re.sub(r"\b(?:Featuring|with|and|from|by|at|in|of)\s*(?=[,.;:]|$)", "", text, flags=re.I)
            text = re.sub(r"\s+([,.;:])", r"\1", " ".join(text.split()))
            return re.sub(r",\s*,", ",", text).strip(" ,;:-")
        nt, nd = strip_other(title), strip_other(description)
        if (nt, nd) != (title, description):
            fixes.append(f"took another store's name out (the photo is from {store})")
            title, description = nt, nd
        if mine and title and not stores_in(title, brands=False) and STORE_DISPLAY.get(store):
            # your note says where it was taken and the title names no store: say so
            title = f"{title} at {STORE_DISPLAY[store]}"
            fixes.append(f"added the store from your notes ({STORE_DISPLAY[store]})")
    if check_parents:
        nt, nd = drop_unproven_parents(title, known_n), drop_unproven_parents(description, known_n)
        if (nt, nd) != (title, description):
            fixes.append("took a parent company out of the title (it isn't printed on the package)")
            title, description = nt, nd
    if store in HARDWARE_STORES:
        for a, b in (("Grocery Store Shelf", "Store Shelf"), ("Grocery Shelves", "Store Shelves"),
                     ("Grocery Shelf", "Store Shelf"), ("Grocery Store", "Store"), ("grocery store", "store"),
                     ("grocery shelf", "store shelf")):
            if a in title or a in description:
                title, description = title.replace(a, b), description.replace(a, b)
                fixes.append("hardware store, not a grocery store")

    alltext = _norm_words(" ".join([title, description]))
    kw_text = _norm_words(" ".join(keywords or []))
    shelf_stable = any(f" {w} " in alltext + kw_text for w in ("can", "cans", "canned", "boxed", "stuffing mix",
                                                                "baking mix", "jar", "jars", "shelf stable"))
    frozen_ok = any(f" {w} " in alltext + known_n for w in ("frozen", "freezer", "freezer section", "ice cream"))
    kept, dropped = [], []
    for k in keywords or []:
        kn = _norm_words(k)
        why = ""
        if kn.strip() and f" {kn.strip()} " in own_n:
            kept.append(k)
            continue
        why = junk_keyword(k)
        if not why and kn.strip() and f" {kn.strip()} " in known_n:
            kept.append(k)
            continue
        if not why and store in HARDWARE_STORES and " grocery " in kn:
            why = "grocery at a hardware store"
        if not why and shelf_stable and not frozen_ok and " frozen " in kn:
            why = "frozen, for canned or boxed food"
        if not why:
            need = NEEDS_EVIDENCE.get(kn.strip())
            if need is not None and not any(w in alltext + known_n for w in need):
                why = "not in the photo"
        if why:
            dropped.append(k)
        else:
            kept.append(k)
    if dropped:
        fixes.append("removed keywords: " + ", ".join(dropped))
    cats = fit_categories(categories, title, description, kept) if categories else []
    if categories and cats != shutterstock_categories(list(categories)):
        fixes.append("categories: " + ", ".join(cats))
    return {"title": title, "description": description, "keywords": kept, "categories": cats, "fixes": fixes}


def build_description_prompt(title: str, keywords, context: str = "", photo_note: str = "",
                             direction: str = "", title_location: str = "", copyright_free: bool = False,
                             symbols: str = "keep", never_use=None, photo_avoid: str = "",
                             concept: bool = False) -> str:
    """Ask for the description (caption) of a photo whose title is already written."""
    lines = ["Write the description (caption) for this stock photo. Its title is: "
             + json.dumps(title, ensure_ascii=False) + ".",
             "",
             "The description is one or two complete, plain sentences of " + ("20 to 35" if concept else "12 to 30")
             + f" words (never more than {DESCRIPTION_MAX - 20} characters) that say what the photo shows: the "
             "subject, what is happening, the setting and the most useful visible details. Add information the title "
             "doesn't have instead of repeating it word for word. Normal sentence capitalisation with a full stop at "
             "the end, no keyword lists, no hashtags, no hype words such as stunning or beautiful."]
    if concept:
        lines += ["", CONCEPT_DESCRIPTION_RULE]
    if (context or "").strip():
        lines += ["", "Facts from the photographer (true; use them where they fit):", context.strip()]
    if (photo_note or "").strip():
        lines += ["", "About this photo (true):", photo_note.strip()]
    if (title_location or "").strip():
        lines += ["", f"The photo was taken in {title_location.strip()}. The photographer adds the place and date "
                  "in front of the description themselves, so don't put them in."]
    lines += ["", COPYRIGHT_FREE_RULES if copyright_free else BRAND_RULES]
    if never_use:
        lines += ["", "Never use these words or phrases: " + ", ".join(never_use) + "."]
    if (photo_avoid or "").strip():
        lines += ["", "Don't use these words, names or ideas: " + single_line(photo_avoid)]
    if symbols in ("convert", "remove"):
        lines += ["", "Write words instead of symbols (\"and\" instead of \"&\")."]
    if keywords:
        lines += ["", "Keywords already chosen, for reference only: " + ", ".join(list(keywords)[:20])]
    lines += ["", "Also choose the one or two Shutterstock categories that fit this photo best (the main subject "
              "first), exactly as written here. Each is followed by what it's for: "
              + "; ".join(f"{c} ({CATEGORY_MEANINGS[c]})" for c in SHUTTERSTOCK_CATEGORIES) + ". "
              "A product on a store shelf is Objects or Food and drink, not Buildings/Landmarks or Nature. Only choose "
              "People or Celebrities when a person is clearly in the photo.",
              "", "And the one Adobe Stock category that fits best, as its number: "
              + "; ".join(f"{n} {name}" for n, name in ADOBE_CATEGORIES.items()) + ". Food is 7 and drinks are 4 "
              "(also for products on a shelf); tools, paint and building supplies are 10 Industry; shops and "
              "products that are neither are 3 Business."]
    if (direction or "").strip() and direction.strip() != (context or "").strip():
        lines += ["", "PHOTOGRAPHER'S DIRECTIONS (follow them where they apply to a description):", direction.strip()]
    lines += ["", 'Output only JSON in exactly this form: {"description": "...", "categories": ["..."], '
              '"adobe_category": 0}']
    return "\n".join(lines)


def clean_description(text: str, symbols: str = "keep", never_use=None, max_len: int = DESCRIPTION_MAX) -> str:
    """Tidy the AI's description: one line, no quotes around it, ends with a full stop, and cut at the end of
    a sentence (or a word) so it fits DESCRIPTION_MAX."""
    t = single_line(text or "").strip().strip(_QUOTES).strip()
    t = tidy_symbols(t, symbols)
    if never_use:
        t = drop_never_from_title(t, never_use)
    t = " ".join(t.split())
    if len(t) > max_len:
        cut = t[:max_len - 1]                          # (one character kept for the full stop)
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        comma = cut.rfind(", ")
        t = (cut[:end + 1] if end >= 40 else cut[:comma] if comma >= 40      # a sentence, else a whole clause
             else cut[:cut.rfind(" ")].rstrip(",;:- "))
        if end < 40:                                   # not at a sentence: no list or phrase left half-way
            t = _drop_unfinished_tail(t)
            m = re.search(r"(?:,\s*|\s+)(?:including|such as|like|featuring)\s+[^,.]*$", t, re.I)
            if m and m.start() >= 0.5 * len(t):        # "…options, including prints" (the rest of the list cut)
                t = t[:m.start()].rstrip(" ,;:-")
    if t and t[-1] not in ".!?":
        t += "."
    return t[:1].upper() + t[1:]


# A description cut short to fit the length limit can stop half-way: on a word like "and", or in a list
# ("…gift options, including prints, calendars"). These say where the unfinished part starts.
_DANGLING = {"and", "or", "with", "of", "the", "a", "an", "to", "for", "in", "on", "at", "by", "from", "stocked",
             "including", "featuring", "alongside", "such", "as", "like", "showcasing", "displaying", "its", "their"}
_PHRASE_START = re.compile(r"(?:,\s*|\s+)(?:including|such as|like|featuring|alongside|surrounded by|stocked with|"
                           r"stocked|showcasing|displaying|with)\b", re.I)
_LIST_START = re.compile(r"(?:,\s*|\s+)(?:including|such as|like|featuring|showcasing|displaying)(?=\s+[^,.]+,)", re.I)


def _drop_unfinished_tail(text: str) -> str:
    """'…gift options, including prints, calendars' -> '…gift options'; '…shelf alongside Coast and' -> '…shelf'.
    Returns the text without a final full stop; unchanged (bar that) when it doesn't stop half-way. Keeps at least
    half of the text."""
    t = text.rstrip(" .,;:-")
    words = t.split()
    if words and words[-1].lower().strip(",;:") in _DANGLING:
        starts = [m.start() for m in _PHRASE_START.finditer(t) if m.start() >= 0.5 * len(t)]
        if starts:
            return t[:starts[-1]].rstrip(" ,;:-")
        while words and words[-1].lower().strip(",;:") in _DANGLING:
            words.pop()
        return " ".join(words).rstrip(" ,;:-")
    last_sentence = max(t.rfind(". "), t.rfind("! "), t.rfind("? ")) + 1
    lists = [m.start() for m in _LIST_START.finditer(t, last_sentence) if m.start() >= 0.5 * len(t)
             and not re.search(r"\b(?:and|or|etc)\b", t[m.end():])]      # a finished list ends "…, and posters"
    if lists:
        return t[:lists[-1]].rstrip(" ,;:-")
    return t


MORE_KEYWORDS_SCHEMA = {
    "type": "object",
    "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["keywords"],
}

# "Read the label first": a quick first look that only copies the text printed on the product or sign
LABEL_SCHEMA = {
    "type": "object",
    "properties": {"brand": {"type": "string"}, "product": {"type": "string"},
                   "variety": {"type": "string"},
                   "parent_company": {"type": "string"},
                   "other_text": {"type": "array", "items": {"type": "string"}}},
    "required": ["brand", "product", "variety", "parent_company", "other_text"],
}

LABEL_PROMPT = (
    "Look closely at this photo and copy the text you can actually read on the main product, packaging, sign, "
    "screen or storefront. Don't describe the photo. Fill in: brand (the brand or store name), product (the exact "
    "product name or product line), variety (flavour, scent, style, colour or edition printed on it), parent_company (the company that owns the brand, only if its name is "
    "printed on the package, otherwise empty) and other_text (up to 6 other short pieces of legible text, such as a slogan or a claim like "
    "'gluten free'; never the weight, volume or price). Spell everything exactly as printed. Leave a field empty if it can't be read; never guess. "
    'Output only JSON: {"brand": "", "product": "", "variety": "", "parent_company": "", '
    '"other_text": []}')


# weights and volumes are left out of titles and keywords: "9.25 oz", "500g", "16.9 fl oz", "2 liter"
_SIZE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*-?\s*(?:fl\.?\s*oz|fluid\s+ounces?|oz|ounces?|g|grams?|grammes?|"
                   r"kg|kilos?|kilograms?|mg|lbs?|pounds?|ml|millilit(?:er|re)s?|cl|l|lit(?:er|re)s?|gal(?:lons?)?)\.?(?!\w)",
                   re.I)
_SIZE_WORDS = {"oz", "ounce", "ounces", "fl oz", "g", "gram", "grams", "kg", "lb", "lbs", "pound", "pounds", "ml",
               "liter", "liters", "litre", "litres", "net weight", "net wt"}


def drop_sizes(text: str) -> str:
    """Remove weights and volumes ('9.25 oz', '500g', '16.9 fl oz') from a title or keyword."""
    if not text:
        return text
    t = _SIZE.sub(" ", text)
    t = re.sub(r"\(\s*\)|\[\s*\]", " ", t)
    t = re.sub(r"\s*,(\s*,)+", ",", t)
    t = " ".join(t.split())
    t = re.sub(r"\s+([,.;:)])", r"\1", t).strip(" ,;:-–")
    return t


def drop_size_keywords(keywords) -> list[str]:
    out = []
    for k in keywords or []:
        k2 = drop_sizes(str(k))
        if k2 and k2.casefold() not in _SIZE_WORDS:
            out.append(k2)
    return out


def label_note(label: dict) -> str:
    """The text read from the photo, as a line for the main prompt ('' if nothing was readable)."""
    if not isinstance(label, dict):
        return ""
    parts = []
    for key, name in (("brand", "brand"), ("product", "product"), ("variety", "variety or flavour"),
                      ("parent_company", "parent company")):
        v = single_line(str(label.get(key) or "")).strip().strip(".")
        if v and v.casefold() not in ("none", "n/a", "unknown", "not visible", "unreadable", "-"):
            parts.append(f"{name}: {v}")
    other = [single_line(str(x)).strip() for x in _as_list(label.get("other_text", []))][:6]
    other = [x for x in other if x and len(x) <= 60]
    if other:
        parts.append("other text: " + "; ".join(other))
    return "; ".join(parts)

# Photo types: tell the AI what kind of photo it is, so titles and keywords fit the situation.
# (key, label shown in the app, what the photo shows - given to the AI as a fact)
COPYRIGHT_FREE = "copyright_free"
PHOTO_TYPES = [
    ("auto", "Automatic (from the photo and your notes)", ""),
    (COPYRIGHT_FREE, "Copyright-free (no brands or names)",
     "a commercial stock photo that must be described only in generic terms: no brand, company, product, "
     "model or app names, no logos or trademarks, no copyrighted characters or titles and no names of people"),
    # editorial and brand situations
    ("logo_stock", "Company logo with stock chart",
     "a stock photo of a company's logo displayed on a smartphone screen, held in front of a computer "
     "screen showing a stock market or financial performance chart"),
    ("product_shelf", "Product on a store shelf",
     "a photo of a retail product sitting on a grocery or retail store shelf; name the brand, the exact product, "
     "its variety, flavour or scent from the packaging, and the brand's parent company only if it is printed on the "
     "package (not the weight or size)"),
    ("app_store", "App in the Play Store",
     "a photo of an app shown on a phone screen in the Google Play Store"),
    ("storefront", "Store or restaurant front",
     "an editorial photo of the outside of a store, restaurant or business, with its sign"),
    ("editorial_event", "Editorial: news or public event",
     "an editorial photo of a real news story or public event, such as a protest, parade, festival, "
     "concert, rally or ceremony; describe factually what is happening, with no opinion"),
    # subjects
    ("landscape", "Landscape",
     "a landscape photo of natural scenery; describe the terrain, sky, light, season, weather and mood"),
    ("seascape", "Beach, coast and seascape",
     "a photo of a beach, coastline, ocean or lake; describe the water, shore, sky, light and mood"),
    ("cityscape", "Cityscape and skyline",
     "a photo of a city skyline, street or urban scene; describe the buildings, streets, landmarks and activity"),
    ("architecture", "Architecture and buildings",
     "a photo of a building or structure; describe its architectural style, materials, features and setting"),
    ("interior", "Interior and rooms",
     "a photo of an indoor space such as a home, office, hotel or venue; describe the room, decor and style"),
    ("landmark", "Travel and landmarks",
     "a travel photo of a famous place, landmark or tourist destination; name the place if it's known"),
    ("aerial", "Aerial and drone",
     "an aerial or drone photo looking down on the scene; describe the view from above and the patterns"),
    ("night", "Night and astrophotography",
     "a night photo, such as city lights, the night sky, stars, the Milky Way or the moon"),
    ("wildlife", "Wildlife",
     "a photo of a wild animal or bird in nature; name the species if you can and describe its behaviour and habitat"),
    ("pets", "Pets and farm animals",
     "a photo of a pet or domestic animal; name the breed if you can and describe what it is doing"),
    ("flowers", "Flowers, plants and gardens",
     "a photo of flowers, plants, trees or a garden; name the species if you can and describe colours and season"),
    ("food", "Food and drink",
     "a food or drink photo; name the dish or drink, its ingredients, cuisine, presentation and setting"),
    ("people", "People and lifestyle",
     "a lifestyle photo of people in an everyday situation; describe what they are doing, their mood, "
     "age range and setting, without naming them"),
    ("portrait", "Portrait",
     "a portrait of a person; describe their expression, age range, clothing, pose and the background, "
     "without naming them"),
    ("business", "Business and office",
     "a business photo, such as an office, meeting, teamwork or work at a desk; include business concepts"),
    ("technology", "Technology and devices",
     "a photo of technology such as computers, phones, screens, electronics or gadgets; include technology concepts"),
    ("sports", "Sports and fitness",
     "a photo of a sport, exercise or outdoor activity; name the sport and describe the action"),
    ("vehicles", "Cars and transportation",
     "a photo of a vehicle or transport, such as a car, truck, train, plane, boat or bicycle"),
    ("industry", "Industry and construction",
     "a photo of industry, manufacturing, construction, machinery or workers at a work site"),
    ("agriculture", "Farming and agriculture",
     "a photo of farming or agriculture, such as fields, crops, harvest, farm buildings or farm machinery"),
    ("holiday", "Holidays and celebrations",
     "a photo of a holiday, season or celebration, such as Christmas, Halloween, a birthday or a wedding"),
    ("abstract", "Abstract, texture and background",
     "an abstract photo, texture or background; describe the colours, patterns, materials and possible uses "
     "as a background or copy space"),
]


def parse_custom_types(text: str) -> list[tuple[str, str, str]]:
    """Your own photo types from Settings: one per line, 'Name: what the photo shows'."""
    out, seen = [], {k for k, _, _ in PHOTO_TYPES}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        name, desc = (x.strip() for x in line.split(":", 1))
        key = "custom:" + name.casefold()
        if name and desc and key not in seen:
            seen.add(key)
            out.append((key, name, desc))
    return out


def photo_types(settings: dict) -> list[tuple[str, str, str]]:
    return PHOTO_TYPES + parse_custom_types(settings.get("custom_types", ""))


def type_description(settings: dict, key: str) -> str:
    """What the photo shows for this type ('' for Automatic or an unknown type)."""
    return next((d for k, _, d in photo_types(settings) if k == key), "")


INPUT_RULES = """How to read the photographer's input:
- If the input is only a company name, the photo is a stock photo of that company's logo displayed on a smartphone screen, held in front of a computer screen showing a stock market or financial performance chart.
- If the input is only a retail product name, the photo shows that product sitting on a grocery or retail store shelf; name its variety or flavour from the packaging, and its parent company only if it is printed on the package.
- If the input is a name followed by the word "app", the photo shows that app on a phone screen in the Google Play Store.
- Otherwise, treat the input as accurate information about the photo.
- If there is no input, describe what you see in the photo."""

# How long titles should be: (key, label, fewest words, most words)
TITLE_LENGTHS = [("short", "Short (6–10 words)", 6, 10),
                 ("standard", "Standard (10–15 words)", 10, 15),
                 ("detailed", "Detailed (14–22 words)", 14, 22)]


def title_words(length: str = "detailed") -> tuple[int, int]:
    return next(((lo, hi) for k, _, lo, hi in TITLE_LENGTHS if k == length), (14, 22))


# The context: not just "Protein Shakes on Shelf" but what they're for and who buys them
CONCEPT_TITLE_RULE = (
    "(5) Finish with the context, so the title isn't only a list of what's on the shelf: what the product or scene is "
    "for, who uses or buys it, or the lifestyle, theme or concept it belongs to, in a few words joined with 'for', "
    "'and' or a comma. For example: 'Protein Shakes on a Store Shelf for Health-Conscious Consumers, Nutrition and "
    "Supplementation'; 'Canned Beef Stew for Quick Comfort Food Meals and Pantry Stocking'; 'Christmas Lights Display "
    "for Holiday Decorating and Seasonal Shopping'; 'Pancake Mix for Easy Family Breakfasts and Home Baking'. This "
    "context is general and true for that kind of product (never an invented detail about this photo), and it stays "
    "within the word count.")
CONCEPT_DESCRIPTION_RULE = (
    "Make it two sentences. The first says what the photo shows. The second gives the context and the mood: what the "
    "product or scene is for, who uses or buys it, and the lifestyle, theme or concept it relates to (for protein "
    "shakes: nutrition, fitness and supplementation for health-conscious consumers; for Christmas lights: holiday "
    "decorating, festive homes and seasonal shopping; for canned stew: quick, hearty comfort food and stocking the "
    "pantry). Keep that general and true for that kind of product: never invent details about this photo.")


def title_rules(length: str = "detailed", concept: bool = False) -> str:
    lo, hi = title_words(length)
    return (f"Title: rewrite the input (or describe the photo) as one full, descriptive, SEO-friendly title in Title "
            f"Case of {lo} to {hi} words. Build it from these parts, in a natural order: (1) the main subject, named as "
            "specifically as possible; (2) what the subject is or is known for: for a company, brand, product, "
            "landmark or building say what it is (for example 'Major American Fast Food Chain', 'Japanese Automaker', "
            "'Gothic Cathedral'), and for a plant, animal, food or object give its kind or variety; (3) what is "
            "happening, or the setting; (4) descriptive details of the scene that can be seen, such as the style, "
            "materials, signs, surroundings, people or objects around it. Only mention the time of day, light or "
            "weather when it is a striking, defining part of the photo (a sunset sky, a city at night, snow); never "
            "add it just to fill out the title. Be specific, never vague: for a product, name the brand, the exact product "
            "or product line and its variety, flavour, scent or style as read from the packaging, but never the "
            "weight, volume or size such as grams, oz or ml (in the pattern '[Brand] [Product] [Flavour] [Kind of "
            "Product]' rather than 'Bag of Chips'); and for a brand, product, store, restaurant or company, you may name "
            "its parent company too (in the pattern '[Brand], a [Parent Company] Brand'), but only when the parent "
            "company's name is printed on the package or sign, or given in the photographer's notes; never from memory "
            "or a guess, since a wrong parent company makes the title inaccurate. The words in square brackets are "
            "placeholders: never put a brand, company or product name in the title or keywords unless that brand is "
            "really in this photo or in the photographer's notes. "
            "Join the parts naturally with commas or words such as with, on and in. "
            "The title must be complete and stand on its own, describing only what is in this photo. Not short and "
            "blunt: for example, instead of 'Coffee Shop Exterior' write 'Independent Coffee Shop Exterior, Cozy "
            "Neighborhood Cafe with Brick Facade, Outdoor Seating and Hanging Sign on a City Street'. No quotation marks and no full stop at the end."
            + (" " + CONCEPT_TITLE_RULE if concept else ""))


TITLE_RULES = title_rules("standard")   # (kept for anything that still reads the old constant)

COPYRIGHT_FREE_RULES = """Copyright-free (strict): the title and every keyword must contain NO brand names, trademarks, company names, product or model names, app or website names, logos, copyrighted characters, titles of films, books, songs, games or artworks, and no names of people. This applies even if they appear in the photo or in the photographer's notes: use the notes to understand the photo, but describe such things in generic words (for example "electric car" instead of a car brand, "smartphone" instead of a phone brand, "soda can" instead of a drink brand, "plastic building blocks" instead of a toy brand, "adhesive bandage" instead of a bandage brand). Plain place names such as cities, countries, regions and natural features are fine."""

BRAND_RULES = """Names and brands: if the input and the photo contain no copyrighted names, brands, trademarks or identifiable entities, do not introduce any into the title or keywords. Do not identify real people from their appearance; only use a person's name if the input gives it."""


# Safety net for "Copyright-free": well-known brand, product and character names that are never
# ordinary words (so "apple", "amazon", "corona" or "target" are deliberately NOT here).
KNOWN_BRANDS = {
    "adidas", "nike", "reebok", "under armour", "new balance", "coca cola", "cocacola", "coke", "pepsi",
    "sprite", "fanta", "dr pepper", "mountain dew", "red bull", "monster energy", "gatorade", "starbucks",
    "dunkin", "mcdonalds", "burger king", "kfc", "taco bell", "wendys", "chick fil a", "chipotle", "dominos",
    "pizza hut", "heineken", "budweiser", "jack daniels", "nestle", "kelloggs", "heinz", "doritos", "oreo",
    "nutella", "pringles", "cheerios", "google", "android", "iphone", "ipad", "macbook", "imac", "airpods",
    "samsung", "microsoft", "xbox", "playstation", "nintendo", "facebook", "instagram", "whatsapp", "tiktok",
    "youtube", "twitter", "snapchat", "linkedin", "netflix", "spotify", "chatgpt", "openai", "uber", "lyft",
    "airbnb", "paypal", "mastercard", "tesla", "toyota", "honda", "chevrolet", "chevy", "bmw", "mercedes",
    "mercedes benz", "audi", "volkswagen", "porsche", "ferrari", "lamborghini", "nissan", "hyundai", "subaru",
    "mazda", "lexus", "jeep", "harley davidson", "walmart", "costco", "ikea", "home depot", "lowes",
    "lego", "disney", "marvel", "pixar", "barbie", "mickey mouse", "star wars", "pokemon", "batman",
    "superman", "spiderman", "spider man", "hello kitty", "harry potter", "louis vuitton", "gucci", "prada",
    "chanel", "rolex", "ray ban", "levis", "zara", "kleenex", "band aid", "post it", "velcro", "ziploc",
    "tupperware", "styrofoam", "frisbee", "jet ski", "jacuzzi", "photoshop", "sharpie", "popsicle", "q tip",
    "chapstick", "bubble wrap", "intel", "nvidia", "ibm", "dell", "lenovo", "canon", "nikon", "gopro", "dji",
}
_BRAND_RES = [re.compile(r"(?<![a-z0-9])" + re.escape(b).replace(r"\ ", r"[\s-]?") + r"s?(?![a-z0-9])")
              for b in sorted(KNOWN_BRANDS)]


def _norm_for_brands(text: str) -> str:
    return re.sub(r"[^a-z0-9\s-]", "", (text or "").casefold().replace("'", "").replace("’", ""))


def brand_names_in(text: str) -> list[str]:
    t = _norm_for_brands(text)
    return [m.group(0) for r in _BRAND_RES for m in [r.search(t)] if m]


def drop_brand_keywords(keywords) -> list[str]:
    """Keywords without any well-known brand, product or character name (for copyright-free photos)."""
    return [k for k in keywords if not brand_names_in(str(k))]


# ----- never-use words, and find & replace -------------------------------------------------------------
def parse_word_list(text: str) -> list[str]:
    """'beautiful, stunning\ncompetitor name' -> ['beautiful', 'stunning', 'competitor name'] (no repeats)."""
    out, seen = [], set()
    for part in re.split(r"[,;\n]+", text or ""):
        w = " ".join(part.split()).strip(" .\"'")
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def _word_pattern(terms) -> re.Pattern | None:
    terms = [t for t in terms or [] if t.strip()]
    if not terms:
        return None
    alts = sorted((r"\s+".join(map(re.escape, t.split())) for t in terms), key=len, reverse=True)
    return re.compile(r"(?<![\w])(?:" + "|".join(alts) + r")(?![\w])", re.I)


def avoid_terms(avoid: str) -> list[str]:
    """The short entries (up to 3 words) of a photo's Avoid note: these are also filtered out for certain.
    Longer phrases ("don't mention the weather") only go to the AI as an instruction."""
    return [t for t in parse_word_list(avoid) if len(t.split()) <= 3]


def drop_never_keywords(keywords, terms) -> list[str]:
    """Remove every keyword that is, or contains as a whole word, one of the never-use words
    ('beautiful' removes 'beautiful' and 'beautiful sunset', but not 'beautify')."""
    pat = _word_pattern(terms)
    return list(keywords) if pat is None else [k for k in keywords if not pat.search(str(k))]


def drop_never_from_title(title: str, terms) -> str:
    """Take the never-use words out of a title and tidy what's left (spaces, commas, dangling 'and')."""
    pat = _word_pattern(terms)
    if pat is None or not title:
        return title
    t = pat.sub("", title)
    t = re.sub(r"\s+([,;:.!?])", r"\1", t)                        # "Sunset , Lake" -> "Sunset, Lake"
    t = re.sub(r"([,;:])(\s*[,;:])+", r"\1", t)                    # ", ," -> ","
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;:-–—")
    for _ in range(2):                                             # a joining word left hanging at either end
        t = re.sub(r"^(and|or|with|of|the|a|an)\s+", "", t, flags=re.I)
        t = re.sub(r"\s+(and|or|with|of|the|a|an|at|in|on|by|for|from|to|over|near)$", "", t,
                   flags=re.I).strip(" ,;:-–—")
    t = re.sub(r"\b(and|or|with)\s+(?=(and|or|with)\b)", "", t, flags=re.I)   # "Lake and with Trees"
    return t.strip(" ,;:-–—")


def find_replace(text: str, find: str, repl: str, whole_words: bool = True, match_case: bool = False) -> str:
    """Replace every occurrence of `find` in `text` (plain words, never a pattern), then tidy the spaces."""
    if not find or not find.strip() or text is None:
        return text
    core_pat = r"\s+".join(map(re.escape, find.split()))
    pat = re.compile((r"(?<![\w])" + core_pat + r"(?![\w])") if whole_words else core_pat, 0 if match_case else re.I)
    out = pat.sub(lambda _m: repl, text)
    return re.sub(r"\s{2,}", " ", out).strip() if out != text else text


def find_replace_keywords(keywords, find: str, repl: str, whole_words: bool = True,
                          match_case: bool = False) -> list[str]:
    """Find & replace inside each keyword; a keyword that ends up empty is removed, and repeats are dropped."""
    out, seen = [], set()
    for k in keywords:
        k2 = find_replace(str(k), find, repl, whole_words, match_case).strip(" ,")
        if k2 != k and str(k) == str(k).lower():
            k2 = k2.lower()                              # keywords written in lowercase stay lowercase
        if k2 and k2.lower() not in seen:
            seen.add(k2.lower())
            out.append(k2)
    return out


def build_prompt(keyword_min: int, keyword_max: int, context: str = "", existing_title: str = "",
                 existing_keywords=None, direction: str = "", photo_note: str = "",
                 photo_type: str = "", lead_keywords=None, title_prefix: str = "",
                 date_in_title: bool = False, title_location: str = "", copyright_free: bool = False,
                 symbols: str = "keep", feedback: str = "", rejected_title: str = "",
                 title_length: str = "detailed", never_use=None, photo_avoid: str = "",
                 direct_titles: bool = True, style: str = "", concept: bool = False,
                 place_keywords: bool = True) -> str:
    """The instructions for one photo. Your notes come first and the title is built around them;
    your directions are repeated at the end. (title_prefix is never shown to the AI.)"""
    context, photo_note, direction = (context or "").strip(), (photo_note or "").strip(), (direction or "").strip()
    lines = ["Write the title and keywords for this stock photo."]
    merged = bool(context) and context == direction       # one "Instructions for the AI" box: facts and directions
    if merged:
        lines += ["", "THE PHOTOGRAPHER'S INSTRUCTIONS. Any facts in them (place, event, company, product, names, "
                  "roles, species) are true: build the title and keywords around them. Any directions in them (how "
                  "to write the title and keywords) take priority over every default rule below:", context]
    elif context:
        lines += ["", "ABOUT THESE PHOTOS (facts from the photographer; treat them as true). Build the title and "
                  "keywords around these facts: use any place, event, company, product, names or roles they give.",
                  context]
    if photo_note:
        lines += ["", "ABOUT THIS PHOTO (notes from the photographer about this one photo; treat them as true). "
                  "Use these notes to clarify what the photo shows, and base the title and keywords on them: "
                  + ("use them to understand the subject, but describe it in generic words. "
                     if copyright_free else
                     "use any subject, name, place, event, species, breed, product or detail they give in the title "
                     "and the keywords. ")
                  + ("Where they differ from ABOUT THESE PHOTOS, these notes win for this photo. " if context else "")
                  + "Notes:", photo_note]
    if not context and not photo_note:
        lines += ["", "The photographer gave no notes for this photo."]
    if photo_type.strip():
        # the photographer chose the photo type: state it as fact instead of guessing from the notes
        lines += ["", f"What this photo shows (set by the photographer; treat it as true): {photo_type.strip()}."]
        if not copyright_free:
            lines += ["Use the photographer's notes, if any, to name the subject (for example the company, product "
                      "or app). If there are no notes, read the name from the photo if it is clearly visible."]
    else:
        lines += ["", INPUT_RULES.replace("photographer's input", "photographer's notes above")
                  .replace("the input", "the notes")]
    lines += [
        "", title_rules(title_length, concept) + (" Work the facts from the photographer's notes into the title."
                                         if context or photo_note else "")
        + (" " + DIRECT_TITLE_RULE if direct_titles else ""),
        "",
        f"Keywords: between {keyword_min} and {keyword_max} unique SEO keywords, most relevant first. All "
        "lowercase, letters and numbers only: no special characters, punctuation or hashtags. No duplicates "
        "or near-duplicates (such as singular and plural of the same word), and don't repeat the same word "
        "across many keywords. Cover the subject, what it is known for, its industry or category, setting, "
        "objects, colours, concepts and likely search terms. For a product or brand, include the brand, the "
        "product line, its variety or flavour and the parent company as keywords; related brands and stores are fine "
        "as keywords too. Never use prices, sale amounts, product or barcode numbers, date or lot codes, or "
        "numbers copied off the package (such as calories or pieces) as keywords, and don't add things that aren't "
        "in the photo (a shopping cart, customers, a warehouse) or that aren't true for the product (frozen for "
        "canned or boxed food).",
        "", COPYRIGHT_FREE_RULES if copyright_free else BRAND_RULES,
    ]
    if (style or "").strip():
        lines += ["", "The photographer's standing preferences, for every photo:", style.strip()]
    if (feedback or "").strip():
        lines += ["", "REDO: the photographer rejected the previous suggestion"
                  + (f" ({json.dumps(rejected_title, ensure_ascii=False)})" if rejected_title else "")
                  + ". Write a clearly better, different title and keywords that fix these problems:",
                  feedback.strip()]
    if never_use:
        lines += ["", "Never use these words or phrases anywhere in the title or the keywords, not even as part of "
                  "a longer keyword: " + ", ".join(never_use) + "."]
    if (photo_avoid or "").strip():
        lines += ["", "AVOID for this photo (the photographer's own words; don't use these words, names or ideas "
                  "anywhere in the title or the keywords): " + single_line(photo_avoid)]
    if symbols in ("convert", "remove"):
        lines += ["", "No symbols: write words instead of symbols in the title and keywords (\"and\" instead of "
                  "\"&\", \"plus\" instead of \"+\", \"percent\" instead of \"%\") and don't use symbols such as "
                  "@ # * ™ ® ©."]
    # The "text before titles" is deliberately NOT shown to the AI: it writes a complete title for the
    # photo alone, and the app puts the text in front afterwards (title_prefix is ignored here).
    if (title_location or "").strip():
        lines += ["", f"The photo was taken in {title_location.strip()}. The photographer adds this location to the "
                  "title themselves, so don't put the place name in your title; describe the subject instead. "
                  + ("Do use the place in the keywords." if place_keywords else
                     "Don't put the place or the date in the keywords either.")]
    if date_in_title:
        lines += ["", "The photographer adds the date the photo was taken to the title themselves, so don't put "
                  "a date in your title."]
    if lead_keywords:
        lines += ["", "The photographer adds these keywords at the front themselves. Use them as information "
                  "about the photo (for example place or date), but don't repeat them in your list: "
                  + ", ".join(lead_keywords)]
    if existing_title or existing_keywords:
        lines += ["", "Metadata already on the file, for reference only (it may be wrong; where it differs from the "
                  "photographer's notes, the notes are right). Write a new title that follows the "
                  "instructions here rather than copying it: "
                  + json.dumps({"title": existing_title, "keywords": list(existing_keywords or [])},
                               ensure_ascii=False)]
    if merged:
        lines += ["", "Remember: follow every direction in the photographer's instructions at the top exactly, even "
                  "where it differs from the rules above."]
    elif direction:
        lines += ["", "PHOTOGRAPHER'S DIRECTIONS (highest priority: follow every one exactly, even where it "
                  "differs from the rules above):", direction]
    if photo_note and not copyright_free:
        lines += ["", f"REMEMBER THE NOTE ABOUT THIS PHOTO: {single_line(photo_note)}. The title must agree with it: "
                  "use the store, brand, product, place, species or name it gives, and never name a different "
                  "store, brand or species than it does (not even one you think you can see, or a store's own "
                  "brand from another chain)."]
    lines += ["", 'Output only JSON in exactly this form: {"title": "...", "keywords": ["...", "..."]}']
    return "\n".join(lines)


# Redo with reasons: what the photographer didn't like about the last suggestion
REDO_PROBLEMS = [   # "What's wrong"
    ("generic", "Too generic",
     "The title was too generic and vague. Be specific about what makes this photo distinctive: the exact subject "
     "(for a product: the brand, the exact product, its variety or flavour, and the parent company), what is "
     "happening and the setting."),
    ("wrong", "Wrong subject, or something misidentified",
     "Something was identified wrongly. Look at the photo again carefully and describe only what is really there."),
    ("long", "Too long",
     "The title was too long. Make it noticeably shorter, keeping only the most important details."),
    ("short", "Too short, or missing detail",
     "The title was too short and blunt. Make it noticeably fuller: say what the subject is or is known for, and add "
     "the setting, the action and descriptive details of the scene."),
    ("notes", "Leaves out the place, event or names from my notes",
     "The title left out facts from the photographer's notes. Work the place, event and names they give into the title."),
    ("invented", "Mentions things that aren't in the photo",
     "The suggestion mentioned things that aren't in the photo. Describe only what is visible or given in the notes."),
    ("names", "Shouldn't name brands, products or people",
     "Don't name any brands, products or people; describe them in generic words."),
    ("flowery", "Too flowery or salesy",
     "Use simple, plain, descriptive words. No flowery or marketing language such as stunning, breathtaking or beautiful."),
    ("editorial", "Not factual enough (editorial)",
     "Write a factual, neutral editorial description: who, what, where and when, without opinion or marketing words."),
    ("keywords", "Keywords off-topic or repetitive",
     "The keywords were off-topic or repetitive. Make every keyword clearly relevant to this photo, and varied."),
    ("keywords_general", "Keywords too general",
     "The keywords were too general. Use more specific ones (exact species, objects, materials, techniques, place "
     "names) and fewer vague ones."),
    ("angle", "Just try a different angle",
     "Try a clearly different wording and angle for the title."),
]
REDO_FOCUS = [      # "What to focus on"
    ("focus_words", "The words and signs in the photo",
     "Focus on any words, signs, labels or text visible in the photo: read them and use them in the title and keywords."),
    ("focus_location", "The location and setting",
     "Make the place and setting the focus of the title: where this is and what the surroundings are like."),
    ("focus_mood", "The mood and feeling",
     "Bring out the mood and atmosphere: the feeling, emotion and ambience of the scene."),
    ("focus_subject", "The main subject only",
     "Make the main subject the focus of the title and leave out background details."),
    ("focus_action", "What's happening (the action)",
     "Focus on what is happening: the action, activity or movement in the photo."),
    ("focus_colours", "The colours, light and time of day",
     "Focus on the main colours, the quality of the light and the time of day."),
    ("focus_people", "The people (without names)",
     "Describe the people: how many, approximate age, what they are doing and wearing, and their expressions, "
     "without naming them."),
    ("focus_concept", "The concept, for commercial buyers",
     "Bring out the concept or idea this photo could illustrate for commercial buyers (for example teamwork, "
     "freedom, success, healthy living) in the title and keywords."),
    ("focus_season", "The season, weather or holiday",
     "Mention the season, weather or holiday shown in the photo."),
]
REDO_REASONS = REDO_PROBLEMS + REDO_FOCUS

# "Every Detect": standing preferences, ticked once and used for every photo. (key, label, short label, rule)
STYLE_ALWAYS = [
    ("factual", "Plain and factual, no flowery or salesy words", "Factual",
     "Write plainly and factually: no flowery, emotional or salesy words such as beautiful, stunning, amazing, "
     "perfect or breathtaking."),
    ("visible", "Only what's visible, nothing invented", "Only what's visible",
     "Describe only what can really be seen in the photo or is stated in the notes: never guess or invent details, "
     "places, events, names or what something is used for."),
    ("no_names", "No brand, product or people's names", "No names",
     "Don't name brands, products, companies or people: describe them in generic words instead."),
    ("use_notes", "Use the place, event and names from my notes", "Use my notes",
     "Always work the place, event and names from the photographer's instructions and photo notes into the title "
     "and the keywords."),
    ("specific_kw", "Specific keywords, not general ones", "Specific keywords",
     "Choose specific keywords (the exact species, product, place, activity or material) over general ones such as "
     "nature, object, thing, item or background."),
]
STYLE_FOCUS = [
    ("words", "The words and signs in the photo", "Signs",
     "Read the text in the photo (signs, labels, packaging) and use it, spelled exactly as written."),
    ("subject", "The main subject only", "Main subject",
     "Keep the title about the main subject and mention the background only briefly."),
    ("action", "What's happening", "Action", "Say what is happening: the action or activity."),
    ("colours", "Colours, light and time of day", "Colours & light",
     "Mention the main colours and the light or time of day."),
    ("mood", "The mood and feeling", "Mood", "Bring out the mood or feeling of the scene, in plain words."),
    ("setting", "The location and setting", "Setting", "Describe where this is and what the surroundings are like."),
    ("people", "The people (without names)", "People",
     "Describe the people: how many, approximate age, what they are doing and wearing, without naming them."),
    ("concept", "The concept, for commercial buyers", "Concepts",
     "Include the ideas this photo could illustrate for commercial buyers (for example teamwork, healthy eating, "
     "sustainability) in the keywords, and in the title when it fits."),
    ("season", "The season, weather or holiday", "Season",
     "Mention the season, weather or holiday when it can be seen."),
]
STYLE_CHOICES = STYLE_ALWAYS + STYLE_FOCUS

# Photo types that rarely have text to read: the label-reading first look is skipped for them, saving one full
# look at the photo each (it's still done when "Focus on the words and signs" is ticked)
NO_TEXT_TYPES = {"landscape", "seascape", "aerial", "night", "wildlife", "pets", "flowers", "portrait",
                 "agriculture", "abstract"}


def style_keys(keys) -> list[str]:
    """Only known choices, in the checklist's own order."""
    want = set(keys or [])
    return [k for k, *_ in STYLE_CHOICES if k in want]


def style_rules(keys) -> str:
    """The standing preferences as instructions for the AI ('' when nothing is ticked)."""
    return "\n".join("- " + rule for k, _l, _s, rule in STYLE_CHOICES if k in set(style_keys(keys)))


def style_summary(keys) -> str:
    """One line for the window: 'Factual · Main subject · Signs'."""
    names = [short for k, _l, short, _r in STYLE_CHOICES if k in set(style_keys(keys))]
    return " · ".join(names)


def redo_feedback(reasons, other: str = "") -> str:
    """The instruction text for the chosen reasons (keys from REDO_REASONS) plus anything typed."""
    lines = [text for key, _, text in REDO_REASONS if key in set(reasons or [])]
    if (other or "").strip():
        lines.append("The photographer also says: " + single_line(other))
    return "\n".join("- " + l for l in lines)


def build_revision_prompt(draft_title: str, draft_keywords, keyword_min: int, keyword_max: int,
                          context: str = "", photo_note: str = "", direction: str = "",
                          title_location: str = "", copyright_free: bool = False, feedback: str = "",
                          never_use=None, photo_avoid: str = "", style: str = "") -> str:
    """Second pass (text only): bring a draft in line with your notes and directions."""
    lines = ["Check this draft title and keyword list for a stock photo against the photographer's instructions."]
    if (style or "").strip():
        lines += ["", "The photographer's standing preferences (make sure the draft follows them):", style.strip()]
    if never_use:
        lines += ["", "Remove these words or phrases from the title and the keywords: " + ", ".join(never_use) + "."]
    if (photo_avoid or "").strip():
        lines += ["", "The photographer says to AVOID this for this photo; take it out of the title and keywords: "
                  + single_line(photo_avoid)]
    if (feedback or "").strip():
        lines += ["", "The photographer rejected an earlier suggestion for these reasons; make sure the draft fixes them:",
                  feedback.strip()]
    if copyright_free:
        lines += ["", COPYRIGHT_FREE_RULES, "Replace every brand, trademark, product, character or person's name in "
                  "the draft with generic words, and remove any keyword that is such a name."]
    if (title_location or "").strip():
        lines += ["", f"The location ({title_location.strip()}) is added to the title separately: remove the place "
                  "name from the title if it's there, but keep it in the keywords."]
    merged = bool((context or "").strip()) and (context or "").strip() == (direction or "").strip()
    if merged:
        lines += ["", "PHOTOGRAPHER'S INSTRUCTIONS (facts the title should reflect, and directions to follow exactly; "
                  "they override all other rules):", context.strip()]
    elif (context or "").strip():
        lines += ["", "ABOUT THESE PHOTOS (facts; the title should reflect them):", context.strip()]
    if (photo_note or "").strip():
        lines += ["", "ABOUT THIS PHOTO (fact; the title and keywords should reflect it"
                  + (", in generic words" if copyright_free else "") + "):", photo_note.strip()]
    if (direction or "").strip() and not merged:
        lines += ["", "PHOTOGRAPHER'S DIRECTIONS (follow every one exactly; they override all other rules):",
                  direction.strip()]
    lines += ["", "Draft:", json.dumps({"title": draft_title, "keywords": list(draft_keywords or [])}, ensure_ascii=False),
              "", "Rewrite the draft so that it follows every direction exactly and works the facts above into the "
              "title where they apply. Keep whatever already complies. The title must still be a complete "
              f"description of the photo. Keep between {keyword_min} and {keyword_max} keywords"
              + ("" if keyword_case_overridden(direction) else ", lowercase") + ", with no duplicates.",
              "", 'Output only JSON in exactly this form: {"title": "...", "keywords": ["...", "..."]}']
    return "\n".join(lines)

# Reword / Double-check: a text-only pass over the title, description and keywords already written ---------
REWORD_TEXT_OPTIONS = [   # (key, label, what the AI is told)
    ("check", "Just double-check (fix mistakes only)",
     "Only fix real mistakes: spelling, grammar, capitalisation, a wrong or doubled word, something that contradicts "
     "the rest of the text. Keep everything else exactly as it is. If nothing is wrong, give it back unchanged."),
    ("shorter", "Make it shorter",
     "Make the title noticeably shorter, keeping only the most important details."),
    ("longer", "Make it fuller (same details)",
     "Make the title noticeably fuller and more descriptive, using only the details already in the text."),
    ("expand", "Make it longer: add what the subject is",
     "The title is too short. Make it noticeably longer and more informative, about 10 to 16 words, by adding "
     "well-known general background about what it names: what kind of thing it is, what it's made of or used for, "
     "its category, its origin or what it's popular or known for. Add only facts that are generally true of the "
     "named subject. Never invent details about this particular photo (colours, numbers, people, actions or a "
     "setting the text doesn't give), and never add a date or the place where the photo was taken."),
    ("plain", "Plainer words, no marketing language",
     "Use plain, simple, descriptive words. Remove flowery or marketing words such as stunning, breathtaking, "
     "beautiful, amazing or perfect."),
    ("repeats", "Remove repeated words",
     "Don't use the same word twice in the title, and don't let the description just repeat the title."),
    ("specific", "More specific",
     "Make it more specific: name the exact subject, kind or variety instead of general words, using only what the "
     "text already says."),
    ("order", "Main subject first",
     "Put the main subject at the start of the title, then the action or setting, then the details."),
    ("editorial", "Factual editorial tone",
     "Write it as a factual, neutral editorial caption: who, what, where and when, without opinion or marketing words."),
    ("context", "Add the context (what it's for, who buys it)",
     "Add the context at the end of the title and as a second sentence of the description: what the product or "
     "scene is for, who uses or buys it, and the lifestyle, theme or concept it relates to (for protein shakes: "
     "nutrition and supplementation for health-conscious consumers). Keep it general and true for that kind of "
     "product; never invent details about the photo."),
    ("commercial", "Concept words for commercial buyers",
     "Bring out the concept the photo could illustrate for commercial buyers (for example teamwork, freedom, healthy "
     "living) where it fits."),
    ("no_names", "No brands, products or people's names",
     "Remove every brand, trademark, product, company or person's name and describe them in generic words."),
]
REWORD_KEYWORD_OPTIONS = [
    ("kw_dupes", "Remove duplicates and near-duplicates",
     "Remove duplicate and near-duplicate keywords (singular and plural, the same word in many phrases)."),
    ("kw_off", "Remove keywords that don't fit",
     "Remove keywords that don't describe what the title and description say is in the photo."),
    ("kw_general", "Swap vague keywords for specific ones",
     "Replace vague, general keywords with more specific ones (exact kind, material, place, technique)."),
    ("kw_order", "Most important first",
     "Order the keywords by importance: what the title names first, then setting, concepts and the rest."),
    ("kw_more", "Add more keywords",
     "Add more relevant keywords (likely search terms, concepts, related subjects) up to the maximum."),
    ("kw_spell", "Fix spelling and make them all lowercase",
     "Fix any misspelled keyword and write every keyword in lowercase, letters and numbers only."),
]
REWORD_OPTIONS = REWORD_TEXT_OPTIONS + REWORD_KEYWORD_OPTIONS
REWORD_FIELDS = [("title", "Title"), ("description", "Description"), ("keywords", "Keywords")]


def build_reword_prompt(title: str, description: str, keywords, fields, options=(), instruction: str = "",
                        dateline: str = "", fixed_keywords=(), keyword_min: int = 7, keyword_max: int = 50,
                        context: str = "", photo_note: str = "", direction: str = "", copyright_free: bool = False,
                        symbols: str = "keep", never_use=None, photo_avoid: str = "",
                        direct_titles: bool = True, style: str = "", title_length: str = "detailed") -> str:
    """The instructions for rewording one photo's text (no image). dateline: the location / date put in front
    of the title, given only as background; the AI must leave it out of what it writes."""
    fields = [f for f, _ in REWORD_FIELDS if f in set(fields or [])]
    picked = [key for key, _, _ in REWORD_OPTIONS if key in set(options or [])
              and (key.startswith("kw_") and "keywords" in fields or not key.startswith("kw_")
                   and ({"title", "description"} & set(fields)))]
    chosen = [text for key, _, text in REWORD_OPTIONS if key in picked]
    if "check" in picked and any(k != "check" and not k.startswith("kw_") for k in picked):
        # with other changes asked for, "keep everything else exactly" would cancel them out
        chosen[picked.index("check")] = ("Also fix any real mistakes: spelling, grammar, capitalisation, a wrong or "
                                         "doubled word, something that contradicts the rest of the text.")
    what = " and ".join(dict(REWORD_FIELDS)[f].lower() for f in fields)
    lines = [f"Here is the text already written for a stock photo. Improve its {what} as asked below. You can't see "
             "the photo: work only from this text and the facts given, and never add details that aren't there."
             + (" (General background about what the text names is fine where asked below; new details about "
                "this particular photo are not.)" if "expand" in set(options or []) else "")]
    lines += ["", "Current text:", json.dumps({"title": title, "description": description,
                                                "keywords": list(keywords or [])}, ensure_ascii=False)]
    if (dateline or "").strip():
        lines += ["", f"The photographer puts \"{dateline.strip()}\" in front of the title and description themselves. "
                  "Use it only to understand where and when the photo was taken. Don't write it, the place or the "
                  "date in your title or description, and don't change it."]
    if fixed_keywords:
        lines += ["", "These keywords are kept as they are and added separately, so leave them out of your list "
                  "(they're background only): " + ", ".join(fixed_keywords)]
    if (context or "").strip():
        lines += ["", "Facts from the photographer (true):", context.strip()]
    if (photo_note or "").strip():
        lines += ["", "About this photo (true):", photo_note.strip()]
    if chosen:
        lines += ["", "What to do:"] + ["- " + c for c in chosen]
    if (instruction or "").strip():
        lines += ["", "THE PHOTOGRAPHER'S REWORDING INSTRUCTIONS (highest priority, follow them exactly):",
                  instruction.strip()]
    if not chosen and not (instruction or "").strip():
        lines += ["", "What to do:", "- " + REWORD_TEXT_OPTIONS[0][2]]
    rules = []
    if "title" in fields:
        lo_n, hi_n = reword_title_words(title, picked, title_length)
        rules.append("Title: one line in Title Case, no quotation marks, no full stop at the end. "
                     + (f"Make it shorter than now ({len(title.split())} words), at least {lo_n} words."
                        if "shorter" in picked else
                        f"It must have {lo_n} to {hi_n} words: never shorter than it is now ({len(title.split())} "
                        "words)" + (", and noticeably longer" if lo_n > len(title.split()) else "") + ".")
                     + (" " + DIRECT_TITLE_RULE if direct_titles else ""))
    if (style or "").strip():
        rules.append("The photographer's standing preferences, for every photo:\n" + style.strip())
    if "description" in fields:
        rules.append(f"Description: one or two complete sentences in normal sentence case ending with a full stop, "
                     f"at most {DESCRIPTION_MAX - 20} characters. If it is empty, write one from the title.")
    if "keywords" in fields:
        rules.append(f"Keywords: between {keyword_min} and {keyword_max} unique keywords, most relevant first, "
                     + ("" if keyword_case_overridden(direction) else "lowercase, ")
                     + "no hashtags or punctuation.")
    lines += ["", "Rules:"] + ["- " + r for r in rules]
    if copyright_free:
        lines += ["", COPYRIGHT_FREE_RULES]
    if never_use:
        lines += ["", "Never use these words or phrases: " + ", ".join(never_use) + "."]
    if (photo_avoid or "").strip():
        lines += ["", "Don't use these words, names or ideas: " + single_line(photo_avoid)]
    if symbols in ("convert", "remove"):
        lines += ["", "Write words instead of symbols (\"and\" instead of \"&\")."]
    if (direction or "").strip() and direction.strip() != (context or "").strip():
        lines += ["", "The photographer's general directions (follow them where they apply):", direction.strip()]
    keep = [dict(REWORD_FIELDS)[f].lower() for f, _ in REWORD_FIELDS if f not in fields]
    if keep:
        lines += ["", "Give the " + " and ".join(keep) + " back exactly as they are."]
    lines += ["", 'Output only JSON in exactly this form: {"title": "...", "description": "...", "keywords": ["..."]}']
    return "\n".join(lines)


LONGER_OPTIONS = {"longer", "expand", "context", "specific"}


def reword_title_words(title: str, options, title_length: str = "detailed") -> tuple[int, int]:
    """How many words a reworded title should have: (at least, at most). Never fewer than it has now, unless
    Make it shorter is ticked; a few more when it should be fuller, longer or get the context; and within the
    title length chosen in Settings (or longer, when the title is already longer than that)."""
    now = len((title or "").split())
    lo, hi = title_words(title_length)
    if "shorter" in set(options or []):
        return max(4, min(lo, now - 2)), max(4, now - 2)
    want = now + 3 if LONGER_OPTIONS & set(options or []) else now
    least = max(lo if LONGER_OPTIONS & set(options or []) else min(lo, now), want)
    return least, max(hi, least + 4)


REWORD_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                   "keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "description", "keywords"],
}


def reword(url: str, model: str, title: str, description: str, keywords, fields, options=(), instruction: str = "",
           dateline: str = "", fixed_keywords=(), keyword_min: int = 7, keyword_max: int = 50, context: str = "",
           photo_note: str = "", direction: str = "", copyright_free: bool = False, symbols: str = "keep",
           never_use=None, photo_avoid: str = "", opener=None, cancel: CancelToken | None = None,
           name: str = "", title_caps: str = "title", direct_titles: bool = True, style: str = "",
           fix_common: bool = True, title_length: str = "detailed") -> dict:
    """Reword / double-check a photo's text with the AI, text only (no image, so it's quick). Only the fields
    asked for change; anything the AI leaves empty keeps its old value. title is the bare title (without the
    location, date or text before titles) and keywords leave out fixed_keywords: the caller puts those back.
    Returns {'title', 'description', 'keywords', 'seconds'}."""
    fields = set(fields or [])
    keyword_min, keyword_max = keyword_range(keyword_min, keyword_max)
    room_min = max(1, keyword_min - len(fixed_keywords or []))
    room_max = max(room_min, keyword_max - len(fixed_keywords or []))
    prompt = build_reword_prompt(title, description, keywords, fields, options, instruction, dateline,
                                 fixed_keywords, room_min, room_max, context, photo_note, direction,
                                 copyright_free, symbols, never_use, photo_avoid, direct_titles=direct_titles,
                                 style=style, title_length=title_length)
    t0 = time.monotonic()
    shorter_ok = "shorter" in set(options or [])
    least = reword_title_words(title, options, title_length)[0] if "title" in fields else 0
    kept_title = False
    debug(f"{name}: reword: fields={sorted(fields)} options={list(options or [])} instruction={_clip(instruction or '', 120)!r}")
    parsed = {}
    for attempt, temperature in ((1, 0.1 if set(options or []) <= {"check"} and not instruction else 0.3), (2, 0.5)):
        if cancel is not None and cancel.is_set():
            raise Stopped(STOPPED)
        body = {"model": model, "stream": False, "format": REWORD_SCHEMA, "keep_alive": AI_KEEP_ALIVE,
                "options": {"temperature": temperature, "num_ctx": AI_NUM_CTX, "num_predict": MAX_REPLY_TOKENS},
                "messages": [{"role": "system", "content": "You edit the titles, descriptions and keywords of stock "
                              "photos. Reply with JSON only, no explanations."},
                             {"role": "user", "content": prompt}]}
        resp = _chat(url, body, opener, cancel)
        content = (resp.get("message") or {}).get("content", "") or ""
        parsed = _parse_json(content)
        debug(f"{name}: reword: attempt {attempt}, {time.monotonic() - t0:.1f} s: {_clip(content, 300)}")
        got_n = len(clean_title(str(parsed.get("title", "") or "")).split())
        if "title" in fields and not shorter_ok and got_n and got_n < least and attempt == 1 \
                and resp.get("done_reason") != "length":
            # small models shorten titles when rewording: say exactly how long it must be, once
            debug(f"{name}: reword: title came back with {got_n} words, needs at least {least}: asking again")
            prompt += (f"\n\nYour title had only {got_n} words: {json.dumps(parsed.get('title'), ensure_ascii=False)}. "
                       f"It must have at least {least} words. Write it again, fuller, keeping every detail of the "
                       "current title.")
            continue
        if resp.get("done_reason") != "length" and any(parsed.get(f) for f in fields):
            break
    never = list(never_use or []) + avoid_terms(photo_avoid)
    new_title, new_desc, new_kws = title, description, list(keywords or [])
    if "title" in fields:
        t = split_leaked_keywords(str(parsed.get("title", "") or "").strip())[0]
        t = drop_sizes(tidy_symbols(t, symbols))
        t = style_title(t, direction, title_caps, direct_titles)
        t = drop_never_from_title(t, never) if never else t
        if not shorter_ok and len(t.split()) < len(title.split()):
            kept_title = bool(t)                     # shorter than before, even after asking again: keep yours
            debug(f"{name}: reword: title still shorter ({len(t.split())} < {len(title.split())} words): kept the old one")
        elif len(t.split()) >= 2:
            new_title = t
    if "description" in fields:
        d = clean_description(str(parsed.get("description", "") or ""), symbols, never)
        if len(d.split()) >= 5 and d.rstrip(".").casefold() != new_title.casefold():
            new_desc = d
    if "keywords" in fields:
        k = drop_never_keywords(drop_size_keywords([tidy_symbols(str(x), symbols)
                                                    for x in _as_list(parsed.get("keywords", []))]), never)
        if copyright_free or "no_names" in set(options or []):
            k = drop_brand_keywords(k)
        fixed = {x.casefold() for x in fixed_keywords or []}
        k = [x for x in seo_keywords(k, lower=not keyword_case_overridden(direction),
                                     protect=" ".join([direction or "", context or "", photo_note or ""]))
             if x.casefold() not in fixed][:room_max]
        if len(k) >= min(room_min, len(new_kws)) and k:
            new_kws = k
    if (new_title, new_desc, new_kws) == (title, description, list(keywords or [])) and not parsed:
        raise ApiError(EMPTY_REPLY)
    if fix_common:
        fixed = fix_common_problems(new_title, new_desc, new_kws, (),
                                    " ".join([context or "", photo_note or "", direction or "", instruction or "",
                                              " ".join(fixed_keywords or [])]))
        if fixed["fixes"]:
            debug(f"{name}: reword: fixed automatically: " + "; ".join(fixed["fixes"]))
        new_title = fixed["title"] if "title" in fields else new_title
        new_desc = fixed["description"] if "description" in fields else new_desc
        new_kws = fixed["keywords"] if "keywords" in fields else new_kws
    return {"title": new_title, "description": new_desc, "keywords": new_kws, "kept_title": kept_title,
            "seconds": round(time.monotonic() - t0, 1)}


def prepare_image(path: str, max_edge: int = PREVIEW_MAX_EDGE) -> str:
    """Small upright JPEG copy for the AI model, made in memory only. Returns base64."""
    from PIL import Image, ImageOps   # imported here so the rest works without Pillow
    src, needs_turn = open_image(path)
    with src as im:
        im.draft("RGB", (max_edge, max_edge))       # fast reduced-size JPEG decode
        if needs_turn:
            im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


NOT_RUNNING = ("Ollama isn't running. Open the Ollama app (free from ollama.com), "
               "then try again.")


def find_ollama() -> list[str] | None:
    """The command that starts Ollama on this computer, or None if it isn't installed.
    The desktop app is preferred (it starts the server and stays in the tray / menu bar);
    otherwise the command-line ollama is started with 'serve'."""
    home = os.path.expanduser("~")
    if os.name == "nt":
        local, progs = os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")
        folders = [os.path.join(local, "Programs", "Ollama")] if local else []
        folders += [os.path.join(progs, "Ollama")] if progs else []
        for folder in folders:
            for name in ("ollama app.exe", "Ollama.exe"):
                if os.path.isfile(os.path.join(folder, name)):
                    return [os.path.join(folder, name)]
        cli = shutil.which("ollama") or shutil.which("ollama.exe")
        if not cli and os.environ.get("LOCALAPPDATA"):
            p = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Ollama", "ollama.exe")
            cli = p if os.path.isfile(p) else None
        return [cli, "serve"] if cli else None
    if sys.platform == "darwin":
        for p in ("/Applications/Ollama.app", os.path.join(home, "Applications", "Ollama.app")):
            if os.path.isdir(p):
                return ["open", "-a", p]
    # apps opened from the Finder don't see Homebrew's PATH, so the usual folders are checked too
    for cli in (shutil.which("ollama"), "/opt/homebrew/bin/ollama", "/usr/local/bin/ollama", "/usr/bin/ollama"):
        if cli and os.path.isfile(cli) and os.access(cli, os.X_OK):
            return [cli, "serve"]
    return None


def open_ollama() -> bool:
    """Start Ollama in the background. False when it can't be found on this computer."""
    cmd = find_ollama()
    if not cmd:
        return False
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS (no console window), own group
    else:
        kw["start_new_session"] = True   # keeps running when this app closes
    try:
        subprocess.Popen(cmd, **kw)
    except OSError as e:
        debug(f"couldn't open Ollama with {cmd}: {e}")
        return False
    debug(f"opened Ollama: {cmd}")
    return True


def _ollama(url: str, path: str, body: dict | None = None, timeout: float = 10, opener=None):
    """Call the local Ollama server. GET when body is None, otherwise POST JSON."""
    opener = opener or urllib.request.urlopen
    # "localhost" can stall on Windows (it tries IPv6 first and retries); Ollama listens on 127.0.0.1
    url = re.sub(r"^(https?://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", url.strip(), flags=re.I)
    req = urllib.request.Request(url.rstrip("/") + path,
                                 data=None if body is None else json.dumps(body).encode("utf-8"),
                                 method="GET" if body is None else "POST",
                                 headers={"content-type": "application/json"})
    try:
        return opener(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error") or e.reason
        except Exception:
            msg = e.reason
        if e.code == 404 and "not found" in str(msg).lower():
            name = (body or {}).get("model", "")
            raise ApiError(f"The model {name} isn't downloaded yet. Download it in Settings.")
        raise ApiError(f"Ollama error: {msg}")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
            raise ApiError("The AI model took too long to answer. Try again, or pick a smaller model in Settings.")
        raise ApiError(NOT_RUNNING)


def _json(resp) -> dict:
    with resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_version(url: str, opener=None) -> str | None:
    """Ollama's version if it is running, else None."""
    try:
        return _json(_ollama(url, "/api/version", timeout=3, opener=opener)).get("version", "")
    except (ApiError, ValueError):
        return None


def list_models(url: str, opener=None) -> list[str]:
    """Names of all installed models (one quick request)."""
    return sorted(filter(None, (m.get("name") or m.get("model") for m in
                                _json(_ollama(url, "/api/tags", timeout=5, opener=opener)).get("models", []))))


# the model is loaded with the same settings every request uses: a different context size would make
# Ollama load it all over again on the first photo
AI_NUM_CTX = 8192
AI_KEEP_ALIVE = "15m"       # Ollama unloads the model after this long without a request


def same_model_name(a: str, b: str) -> bool:
    """'gemma3' and 'gemma3:latest' are the same model."""
    norm = lambda n: (n or "").strip() if ":" in (n or "") else (n or "").strip() + ":latest"
    return norm(a) == norm(b)


def model_state(url: str, model: str, opener=None) -> dict:
    """Is Ollama running, is the model downloaded, and is it loaded in memory right now?
    'on_gpu' is the share of it on the graphics card (1.0 = all of it, 0 = all on the processor)."""
    version = ollama_version(url, opener)
    st = {"running": version is not None, "installed": False, "loaded": False, "on_gpu": None}
    if version is None:
        return st
    try:
        for m in _json(_ollama(url, "/api/ps", timeout=5, opener=opener)).get("models", []):
            if same_model_name(m.get("name") or m.get("model") or "", model):
                size, vram = m.get("size") or 0, m.get("size_vram") or 0
                st.update(loaded=True, installed=True, on_gpu=(vram / size) if size else None)
                return st
        st["installed"] = any(same_model_name(n, model) for n in list_models(url, opener))
    except (ApiError, ValueError):
        pass
    return st


def load_model(url: str, model: str, opener=None) -> None:
    """Load the model into memory now (or keep it there for another AI_KEEP_ALIVE), so the first photo
    doesn't wait for it. Answers right away when it's already loaded."""
    _json(_ollama(url, "/api/generate", {"model": model, "keep_alive": AI_KEEP_ALIVE,
                                         "options": {"num_ctx": AI_NUM_CTX}}, timeout=600, opener=opener))


def unload_model(url: str, model: str, opener=None, timeout: float = 20) -> None:
    """Take the model out of memory now (Ollama drops whatever it was still working on for this app).
    Raises ApiError when Ollama doesn't answer in time: then it's stuck and needs a restart."""
    _json(_ollama(url, "/api/generate", {"model": model, "keep_alive": 0}, timeout=timeout, opener=opener))


def restart_ollama(url: str, wait: float = 15) -> str:
    """Force Ollama to quit (the app, the server and the model it was running), then start it again.
    Returns "" when it was started again, otherwise a short reason for the person."""
    if os.name == "nt":
        cmds = [["taskkill", "/F", "/T", "/IM", "ollama app.exe"], ["taskkill", "/F", "/T", "/IM", "ollama.exe"]]
    else:   # the Mac app is "Ollama"; the server and the model runner are both "ollama"
        cmds = [["pkill", "-x", "Ollama"], ["pkill", "-x", "ollama"]]
    for cmd in cmds:
        try:
            subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=15,
                           **({"creationflags": 0x08000000} if os.name == "nt" else {}))
        except (OSError, subprocess.TimeoutExpired) as e:
            debug(f"restart Ollama: {cmd[0]} failed: {e}")
    deadline = time.monotonic() + wait
    while ollama_version(url) is not None and time.monotonic() < deadline:
        time.sleep(0.5)
    if ollama_version(url) is not None:
        # e.g. Linux, where it runs as a system service: it may simply have been started again for us
        debug("restart Ollama: it was still answering after the stop")
        return ""
    if not open_ollama():
        return "Ollama was stopped, but it couldn't be found to start it again. Open it yourself."
    debug("restart Ollama: stopped and started again")
    return ""


def ai_ready(url: str, opener=None) -> dict:
    """Quick check before a batch: is Ollama running, and which models are installed?"""
    version = ollama_version(url, opener)
    try:
        names = list_models(url, opener) if version is not None else []
    except (ApiError, ValueError):
        names = []
    return {"version": version, "names": names}


def list_vision_models(url: str, opener=None) -> list[str]:
    """Installed models that can look at images (models whose capabilities can't be
    read are included, so nothing usable is hidden)."""
    names = [m.get("name") or m.get("model") for m in
             _json(_ollama(url, "/api/tags", timeout=5, opener=opener)).get("models", [])]
    out = []
    for n in filter(None, names):
        try:
            caps = _json(_ollama(url, "/api/show", {"model": n}, timeout=10, opener=opener)).get("capabilities")
        except (ApiError, ValueError):
            caps = None
        if caps is None or "vision" in caps:
            out.append(n)
    return sorted(out)


def pull_model(url: str, name: str, progress=None, cancel=None, opener=None) -> None:
    """Download a model with Ollama. progress(fraction 0..1 or None, text) is called as it goes."""
    resp = _ollama(url, "/api/pull", {"model": name, "stream": True}, timeout=60, opener=opener)
    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                raise ApiError("Download stopped.")
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                raise ApiError(f"Download failed: {ev['error']}")
            total, done = ev.get("total"), ev.get("completed")
            frac = (done / total) if total and done is not None else None
            if progress:
                progress(frac, ev.get("status", ""))
            if ev.get("status") == "success":
                return
    raise ApiError("The download didn't finish. Try again.")


STOPPED = "__cancelled__"


class Stopped(ApiError):
    """The request was stopped (Stop button, or the photo went over its time limit)."""


class CancelToken(threading.Event):
    """Stop switch for one photo. cancel() also cuts the connection to Ollama, so a request that's
    in progress ends at once (and Ollama stops working on it) instead of running to the end."""

    def __init__(self):
        super().__init__()
        self._conns = set()
        self._lock = threading.Lock()

    def watch(self, conn):
        with self._lock:
            if self.is_set():
                raise Stopped(STOPPED)
            self._conns.add(conn)

    def unwatch(self, conn):
        with self._lock:
            self._conns.discard(conn)

    def cancel(self):
        self.set()
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            try:
                if c.sock is not None:
                    c.sock.shutdown(socket.SHUT_RDWR)   # wakes up the thread that's waiting for the reply
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass


def _post_json(url: str, path: str, body: dict, timeout: float, cancel: CancelToken) -> dict:
    """POST to Ollama in a way that cancel() can interrupt at any moment."""
    url = re.sub(r"^(https?://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", url.strip(), flags=re.I)
    u = urllib.parse.urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname or "127.0.0.1", u.port or (443 if u.scheme == "https" else 80), timeout=timeout)
    cancel.watch(conn)
    try:
        conn.request("POST", u.path.rstrip("/") + path, body=json.dumps(body).encode("utf-8"),
                     headers={"content-type": "application/json"})
        resp = conn.getresponse()
        status, data = resp.status, resp.read()
    except (OSError, http.client.HTTPException) as e:
        if cancel.is_set():
            raise Stopped(STOPPED)
        if isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in str(e).lower():
            raise ApiError("The AI model took too long to answer. Try again, or pick a smaller model in Settings.")
        raise ApiError(NOT_RUNNING)
    finally:
        cancel.unwatch(conn)
        conn.close()
    if cancel.is_set():
        raise Stopped(STOPPED)
    if status >= 400:
        try:
            msg = json.loads(data.decode("utf-8")).get("error") or f"HTTP {status}"
        except ValueError:
            msg = f"HTTP {status}"
        if status == 404 and "not found" in str(msg).lower():
            raise ApiError(f"The model {body.get('model', '')} isn't downloaded yet. Download it in Settings.")
        raise ApiError(f"Ollama error: {msg}")
    return json.loads(data.decode("utf-8"))


def _chat(url: str, body: dict, opener=None, cancel: CancelToken | None = None) -> dict:
    """One chat request, with a single retry if the model hiccups. A stop is never retried."""
    last = None
    for attempt in range(2):
        try:
            if opener is not None:                   # tests pass their own opener
                return _json(_ollama(url, "/api/chat", body, timeout=600, opener=opener))
            return _post_json(url, "/api/chat", body, 600, cancel or CancelToken())
        except Stopped:
            raise
        except ApiError as e:
            last = e
            if str(e) == NOT_RUNNING or "isn't downloaded" in str(e) or attempt:
                raise
            debug(f"chat request failed ({e}); trying once more in 2 s")
            time.sleep(2)
        except ValueError:
            last = ApiError("The AI model's reply couldn't be read. Try again.")
    raise last or ApiError("The AI model didn't answer.")


def _parse_json(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")      # some models wrap JSON in extra text
        try:
            parsed = json.loads(text[a:b + 1]) if a >= 0 and b > a else {}
        except ValueError:
            parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _as_list(kws) -> list:
    if isinstance(kws, str):
        return parse_keywords_text(kws)
    return [k for k in kws if isinstance(k, (str, int, float))] if isinstance(kws, list) else []


def rewrite_facts(context: str = "", photo_note: str = "", direction: str = "", copyright_free: bool = False) -> str:
    """What every later rewrite of a title must keep: your photo note, your instructions (and the text read
    from the label, which is added to them). Without these a rewrite can undo what the note fixed
    (a heron turning back into a crane)."""
    context, photo_note, direction = (context or "").strip(), (photo_note or "").strip(), (direction or "").strip()
    parts = []
    if photo_note:
        parts.append("About this photo (true; keep it in the title" + (", in generic words" if copyright_free else "")
                     + "): " + single_line(photo_note) + ".")
    if context:
        parts.append("The photographer's instructions (follow them): " + single_line(context))
    if direction and direction != context and direction not in context:
        parts.append("The photographer's directions (follow them): " + single_line(direction))
    return " ".join(parts)


def _fix_text_problems(ask, name, title, desc, checks, limits, symbols, direction, title_caps, direct_titles,
                       title_never, never_use, style, min_words, facts: str = ""):
    """The automatic checks: if they find problems, ask once (text only) for a fix, and keep it only if it's
    really better. Returns (title, description)."""
    tp, dp = text_problems(title, desc, checks, limits)
    if not tp and not dp:
        return title, desc
    fields = (["title"] if tp else []) + (["description"] if dp else [])
    prompt = ("Improve the text of this stock photo. "
              f"Title: {json.dumps(title, ensure_ascii=False)}. "
              + (f"Description: {json.dumps(desc, ensure_ascii=False)}. " if desc else "")
              + "Fix only these problems: " + " ".join(tp + dp)
              + (" " + facts if facts else "")
              + " Keep everything else that is correct, and keep the meaning. The title stays one line in Title Case, "
              "with no quotation marks and no full stop. "
              + (DIRECT_TITLE_RULE + " " if direct_titles else "")
              + (f"Follow the photographer's standing preferences:\n{style.strip()}\n" if (style or "").strip() else "")
              + "Output only JSON: {" + ", ".join(f'"{f}": "..."' for f in fields) + "}")
    schema = {"type": "object", "properties": {f: {"type": "string"} for f in fields}, "required": fields}
    debug(f"{name}: checks found: {' | '.join(p.split(':')[0] for p in tp + dp)}")
    try:
        got = ask(prompt, schema, "checks", with_image=False, temperature=0.2)
    except Stopped:
        raise
    except ApiError:
        return title, desc
    if tp:
        t2 = split_leaked_keywords(drop_sizes(tidy_symbols(str(got.get("title", "") or "").strip(), symbols)))[0]
        t2 = style_title(t2, direction, title_caps, direct_titles)
        t2 = drop_never_from_title(t2, title_never) if title_never else t2
        if len(t2.split()) >= max(3, min_words // 2) and \
                len(text_problems(t2, "", checks, limits)[0]) < len(tp):
            debug(f"{name}: title fixed by the checks: {t2!r}")
            title = t2
    if dp:
        d2 = clean_description(str(got.get("description", "") or ""), symbols, never_use)
        if len(d2.split()) >= 5 and len(text_problems(title, d2, checks, limits)[1]) < len(dp):
            debug(f"{name}: description fixed by the checks")
            desc = d2
    return title, desc


def identify(path: str, url: str, model: str, keyword_min: int, keyword_max: int, context: str = "",
             existing_title: str = "", existing_keywords=None, direction: str = "",
             photo_note: str = "", photo_type: str = "", lead_keywords=None, title_prefix: str = "",
             date_in_title: bool = False, revise: bool = True, title_location: str = "",
             copyright_free: bool = False, opener=None, cancel: CancelToken | None = None,
             symbols: str = "keep", feedback: str = "", rejected_title: str = "",
             title_length: str = "detailed", expand_short: bool = False,
             read_label: bool = False, tidy: bool = False, never_use=None, never_in_title: bool = True,
             photo_avoid: str = "", description: bool = False, title_caps: str = "title",
             direct_titles: bool = True, style: str = "", checks=(), limits=None, fix_common: bool = True,
             concept: bool = False, text_model: str = "", place_keywords: bool = True) -> dict:
    """Ask the local vision model for an SEO title and keywords. text_model: another model for the steps that only
    send text (the double-check, rewrites, checks); the photo model still does everything that looks at the photo.
    With revise=True and any notes or directions given, a second (text-only) pass checks the draft
    against them. With description=True a description (caption) is also written, once the title is
    final. Returns {'title', 'keywords', 'description', 'seconds', 'revised'}."""
    keyword_min, keyword_max = keyword_range(keyword_min, keyword_max)
    name = os.path.basename(path)
    t0 = time.monotonic()
    try:
        image_b64 = prepare_image(path)
    except Exception as e:
        debug(f"{name}: couldn't open the image: {e!r}")
        raise ApiError(f"couldn't open the image: {e}")
    if debugging():
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        debug(f"{name}: START model={model} url={url} file={size} bytes, image sent={len(image_b64) * 3 // 4} bytes "
              f"(prepared in {time.monotonic() - t0:.2f} s), keywords {keyword_min}-{keyword_max}, revise={revise}, "
              f"copyright_free={copyright_free}, type={'yes' if photo_type else 'auto'}, "
              f"about={len(context or '')} chars, direction={len(direction or '')} chars, "
              f"note={_clip(photo_note or '', 80)!r}, location={title_location!r}")
    prompt = build_prompt(keyword_min, keyword_max, context, existing_title, existing_keywords,
                          direction, photo_note, photo_type, lead_keywords, title_prefix, date_in_title,
                          title_location, copyright_free, symbols, feedback, rejected_title, title_length,
                          never_use=never_use, photo_avoid=photo_avoid, direct_titles=direct_titles, style=style,
                          concept=concept, place_keywords=place_keywords)
    system = system_prompt(direction)

    def body(user_text, schema, with_image=True, temperature=0.2):
        user = {"role": "user", "content": user_text}
        if with_image:
            user["images"] = [image_b64]
        return {
            "model": model if with_image or not text_model else text_model, "stream": False, "format": schema,
            "keep_alive": AI_KEEP_ALIVE,                 # stay loaded between photos in a batch
            # num_predict caps the reply: a normal one is ~250 tokens, but now and then a model in JSON mode
            # gets stuck writing endless blank space; the cap ends that in seconds instead of minutes
            "options": {"temperature": temperature, "num_ctx": AI_NUM_CTX, "num_predict": MAX_REPLY_TOKENS},
            "messages": [{"role": "system", "content": system}, user],
        }

    def ask(user_text, schema, step="draft", **kw):
        """One request to the model. A reply that comes back cut off or unreadable is asked again once,
        a little more freely (a re-roll almost always fixes a model that got stuck)."""
        need = ("title" if schema in (OUTPUT_SCHEMA, TITLE_SCHEMA) else "brand" if schema is LABEL_SCHEMA
                else "description" if schema is DESCRIPTION_SCHEMA else "keywords")                     # what a usable reply must have
        for attempt in (1, 2):
            if cancel is not None and cancel.is_set():
                debug(f"{name}: {step}: stopped before sending")
                raise Stopped(STOPPED)
            t = time.monotonic()
            debug(f"{name}: {step}: request sent (attempt {attempt}, prompt {len(user_text)} chars"
                  f"{', with image' if kw.get('with_image', True) else ', text only'}"
                  f"{' to ' + text_model if text_model and not kw.get('with_image', True) else ''})")
            try:
                resp = _chat(url, body(user_text, schema, **kw), opener, cancel)
            except Stopped:
                debug(f"{name}: {step}: STOPPED after {time.monotonic() - t:.1f} s (Stop button or time limit)")
                raise
            except ApiError as e:
                debug(f"{name}: {step}: ERROR after {time.monotonic() - t:.1f} s: {e}")
                raise
            content = (resp.get("message") or {}).get("content", "") or ""
            parsed = _parse_json(content)
            if debugging():
                ns = lambda k: f"{(resp.get(k) or 0) / 1e9:.2f}s"
                debug(f"{name}: {step}: reply in {time.monotonic() - t:.1f} s | done_reason={resp.get('done_reason')} "
                      f"total={ns('total_duration')} load={ns('load_duration')} "
                      f"prompt_tokens={resp.get('prompt_eval_count')} ({ns('prompt_eval_duration')}) "
                      f"reply_tokens={resp.get('eval_count')} ({ns('eval_duration')}) | "
                      f"{len(content)} chars: {_clip(content, 300)}")
            cut_off = resp.get("done_reason") == "length"
            # usable: has a title (main request), or has the keyword list at all (an empty top-up just means
            # "nothing more"). The optional double-check isn't re-asked when it's unreadable: the draft stays.
            usable = bool(parsed.get(need)) if need in ("title", "description") else need in parsed
            if not cut_off and (usable or step in ("double-check", "read label")):
                return parsed
            debug(f"{name}: {step}: PROBLEM: reply {'was cut off at the length cap' if cut_off else 'had no ' + need}"
                  + (f"; full reply: {_clip(content, 2000)}" if attempt == 1 else ""))
            if attempt == 2 or step == "read label":
                return parsed                      # keep whatever arrived (cut off, or nothing usable)
            kw["temperature"] = 0.5                  # re-roll
        return parsed

    started = time.monotonic()
    own_context = context                            # (the label text below doesn't by itself need a double-check)
    read = ""
    if read_label and not copyright_free:
        # first, only read the text on the product or sign; the title is then written from exact spellings
        try:
            read = label_note(ask(LABEL_PROMPT, LABEL_SCHEMA, "read label", temperature=0.0))
            debug(f"{name}: read label: {read or 'no readable text'}")
        except Stopped:
            raise
        except ApiError:
            read = ""                                 # carry on without it
        if read:
            prompt += ("\n\nText read from this photo in a first close look (" + read + "). Use these exact "
                       "spellings for the brand, product, variety or flavour and parent company in the title and "
                       "keywords, as long as they fit what you see.")
            context = ((context or "").strip() + "\nText on the product or sign: " + read).strip()
    facts = rewrite_facts(context, photo_note, direction, copyright_free)   # kept by every later rewrite
    # a redo is asked a little more freely, so it doesn't come back with the same words
    draft = ask(prompt, OUTPUT_SCHEMA, "draft", temperature=0.5 if (feedback or "").strip() else 0.2)
    raw_title = str(draft.get("title", "") or "").strip()
    raw_keywords = _as_list(draft.get("keywords", []))
    raw_title, leaked = split_leaked_keywords(raw_title)
    if leaked:
        debug(f"{name}: the keywords had been written into the title; moved {len(leaked)} back to the keywords")
        raw_keywords = list(raw_keywords) + [k for k in leaked if k not in raw_keywords]

    revised = False
    if revise and raw_title and (copyright_free or any((t or "").strip()
                                                       for t in (direction, own_context, photo_note, feedback))):
        # second pass: make the draft follow your notes and directions (fast: text only, no image)
        try:
            fixed = ask(build_revision_prompt(raw_title, raw_keywords, keyword_min, keyword_max,
                                              context, photo_note, direction, title_location, copyright_free,
                                              feedback, never_use=never_use, photo_avoid=photo_avoid, style=style),
                        OUTPUT_SCHEMA, "double-check", with_image=False, temperature=0.1)
            new_title = split_leaked_keywords(str(fixed.get("title", "") or "").strip())[0]
            new_keywords = _as_list(fixed.get("keywords", []))
            if new_title:
                raw_title, revised = new_title, True
            if len(new_keywords) >= min(keyword_min, len(raw_keywords)):
                raw_keywords, revised = new_keywords, True
        except Stopped:
            raise
        except ApiError:
            pass                                     # keep the draft

    # default clean-up, except where your directions say otherwise
    # in copyright-free mode names from your notes are not protected: they mustn't end up in the keywords
    protect = " ".join([direction or ""] if copyright_free else [direction or "", context or "", photo_note or ""])
    lower = not keyword_case_overridden(direction)
    lo, hi = title_words(title_length)
    if direct_titles:
        raw_title = direct_title(raw_title)
    if expand_short and raw_title and len(raw_title.split()) < lo and not title_case_overridden(direction) \
            and not (feedback or "").strip().count("too long"):
        # too short and blunt: ask once for a fuller version (looking at the photo again, so it can add
        # visible detail of the scene)
        expand = (f"This stock photo title is too short: {json.dumps(raw_title, ensure_ascii=False)}. Rewrite it as one "
                  f"title of {lo} to {hi} words. Keep everything that is correct and add what the subject is or is known "
                  "for, what is happening or the setting, and descriptive details of the scene that can be seen. Only mention the "
                  "time of day or light if it is a striking part of the photo. "
                  + ("Don't add a place name; it is added separately. " if (title_location or "").strip() else "")
                  + (COPYRIGHT_FREE_RULES + " " if copyright_free else "")
                  + (facts + " " if facts else "")
                  + "Title Case, no quotation marks and no full stop at the end. "
                  + (DIRECT_TITLE_RULE + " " if direct_titles else "")
                  + 'Output only JSON: {"title": "..."}')
        try:
            longer = split_leaked_keywords(str(ask(expand, TITLE_SCHEMA, "longer title").get("title", "") or "").strip())[0]
            if len(longer.split()) > len(raw_title.split()):
                debug(f"{name}: title was {len(raw_title.split())} words, expanded to {len(longer.split())}")
                raw_title = longer
        except Stopped:
            raise
        except ApiError:
            pass                                     # keep the short one
    raw_title = drop_sizes(tidy_symbols(drop_trailing_location(raw_title, title_location), symbols))
    title = style_title(raw_title, direction, title_caps, direct_titles)
    avoid = avoid_terms(photo_avoid)                 # this photo's own: always out of the title too
    title_never = (list(never_use or []) if never_in_title else []) + avoid
    if title_never and drop_never_from_title(title, title_never) != title:
        # the AI used a word it was told not to: ask once for the title rewritten without it (text only,
        # quick), so the title isn't left with a hole where the word was; anything left is still cut out
        rewrite = (f"Rewrite this stock photo title without these words or phrases: {', '.join(title_never)}. "
                   f"Title: {json.dumps(title, ensure_ascii=False)}. "
                   + (facts + " " if facts else "")
                   + "Keep its meaning and length, use other words where needed, Title Case, no quotation marks, "
                   'no full stop. Output only JSON: {"title": "..."}')
        try:
            fixed = split_leaked_keywords(str(ask(rewrite, TITLE_SCHEMA, "remove words", with_image=False)
                                              .get("title", "") or "").strip())[0]
            fixed = drop_never_from_title(style_title(fixed, direction, title_caps, direct_titles), title_never)
            if len(fixed.split()) >= 3:
                debug(f"{name}: title rewritten without never-use / avoid words")
                title = fixed
        except Stopped:
            raise
        except ApiError:
            pass
        title = drop_never_from_title(title, title_never)
    never_use = list(never_use or []) + avoid
    raw_keywords = drop_never_keywords(drop_size_keywords([tidy_symbols(str(k), symbols) for k in raw_keywords]),
                                       never_use)
    if copyright_free:
        raw_keywords = drop_brand_keywords(raw_keywords)
    keywords = seo_keywords(raw_keywords, keep=lead_keywords, lower=lower, protect=protect)
    if tidy:
        keywords = tidy_keywords(keywords, title, protect)
    keywords = keywords[:keyword_max]
    own_known = " ".join([own_context or "", photo_note or "", direction or "", " ".join(lead_keywords or [])])
    if fix_common and title:
        # take the junk out now, so the step below can top the list back up to the minimum
        keywords = fix_common_problems(title, "", keywords, (), own_known, label=read)["keywords"]

    if title and len(keywords) < keyword_min:
        # one follow-up request for more keywords so every photo gets at least the minimum
        want = keyword_max - len(keywords)
        more_prompt = (prompt + "\n\nYou have already chosen these keywords: " + ", ".join(keywords)
                       + f".\nGive {want} MORE keywords for the same photo, following the same rules and the "
                       "photographer's directions, none of them repeating or rewording the ones above. "
                       "Output only JSON: {\"keywords\": [\"...\"]}")
        try:
            more = ask(more_prompt, MORE_KEYWORDS_SCHEMA, "more keywords")
            extra = drop_never_keywords(drop_size_keywords([tidy_symbols(str(k), symbols)
                                                            for k in _as_list(more.get("keywords", []))]), never_use)
            if copyright_free:
                extra = drop_brand_keywords(extra)
            extra = seo_keywords(extra, keep=list(lead_keywords or []) + keywords, lower=lower, protect=protect)
            if tidy:
                extra = tidy_keywords(extra, title, protect)
            if fix_common:
                extra = fix_common_problems(title, "", extra, (), own_known, label=read)["keywords"]
            keywords += extra[:keyword_max - len(keywords)]
        except Stopped:
            raise
        except ApiError:
            pass                                     # keep what we have; the photo still gets its title

    if not title and not keywords:
        debug(f"{name}: FAILED: no title or keywords after {time.monotonic() - started:.1f} s")
        raise ApiError(EMPTY_REPLY)
    desc, cats, adobe = "", [], None
    if description and title:
        # the description (caption) Alamy, Dreamstime and Shutterstock want: written from the finished title,
        # looking at the photo again so it can add what the title leaves out
        try:
            got = ask(build_description_prompt(title, keywords, own_context, photo_note, direction, title_location,
                                               copyright_free, symbols, never_use, photo_avoid, concept=concept),
                      DESCRIPTION_SCHEMA, "description")
            desc = clean_description(str(got.get("description", "") or ""), symbols, never_use)
            cats = shutterstock_categories(got.get("categories"))
            adobe = adobe_category(got.get("adobe_category"))
            if len(desc.split()) < 5 or desc.rstrip(".").casefold() == title.casefold():
                desc = ""                            # nothing better than the title: it stands in for it
            debug(f"{name}: description: {desc!r}")
        except Stopped:
            raise
        except ApiError:
            desc = ""                                # the title is saved as the description instead
    if checks and title:
        title, desc = _fix_text_problems(ask, name, title, desc, checks, limits, symbols, direction, title_caps,
                                         direct_titles, title_never, never_use, style, lo, facts)
    if fix_common:
        fixed = fix_common_problems(title, desc, keywords, cats, own_known, label=read, check_parents=True)
        if fixed["fixes"]:
            debug(f"{name}: fixed automatically: " + "; ".join(fixed["fixes"]))
        title, desc, keywords, cats = fixed["title"], fixed["description"], fixed["keywords"], fixed["categories"]
    debug(f"{name}: DONE in {time.monotonic() - started:.1f} s: {len(keywords)} keywords, revised={revised}, "
          f"title={title!r}")
    result = {"title": title, "keywords": keywords, "description": desc, "categories": cats, "adobe_category": adobe,
              "seconds": round(time.monotonic() - started, 1),
              "revised": revised, "label": read}
    if copyright_free:
        names = brand_names_in(title)
        if names:
            result["warning"] = "check the title, it may name a brand: " + ", ".join(names)
    return result


# Brand spellings ---------------------------------------------------------------------------------------------
# One brand per line: the right spelling = wrong spellings (fixed everywhere) | other ways buyers search for it
# (added to the keywords, when there's room). Edit the list in Settings → Brands.
DEFAULT_BRAND_SPELLINGS = """\
BJ's = BJs, Bjs | bjs
BJ's Wholesale Club = BJs Wholesale Club, BJs Wholesale | bjs wholesale
Sam's Club = Sams Club | sams club
Trader Joe's = Trader Joes | trader joes
Kellogg's = Kelloggs, Kellog's, Kellogs | kelloggs
McDonald's = McDonalds, Mc Donalds, Mcdonald's | mcdonalds
Lowe's = Lowes | lowes
Macy's = Macys | macys
Kohl's = Kohls | kohls
Dick's Sporting Goods = Dicks Sporting Goods | dicks sporting goods
Walgreens = Walgreen's
Wegmans = Wegman's
Albertsons = Albertson's
Walmart = Wal-Mart, Wal Mart | wal-mart
H-E-B = HEB, H.E.B. | heb
7-Eleven = 7 Eleven, 7Eleven, Seven Eleven, Seven-Eleven | 7 eleven, 711
Chick-fil-A = Chick fil A, Chickfila, Chick-Fil-A | chick fil a
Coca-Cola = Coca Cola, CocaCola | coca cola, coke
Dr Pepper = Dr. Pepper | dr. pepper
Red Bull = Redbull, Red-Bull | redbull
PepsiCo = Pepsico, Pepsi Co | pepsico
Frito-Lay = Frito Lay, Fritolay | frito lay
Häagen-Dazs = Haagen-Dazs, Haagen Dazs, Häagen Dazs | haagen dazs
Ben & Jerry's = Ben and Jerry's, Ben & Jerrys, Ben and Jerrys | ben and jerrys
M&M's = M&Ms, M and M's, M & M's | m&ms, m and ms
Reese's = Reeses | reeses
Hershey's = Hersheys | hersheys
Campbell's = Campbells | campbells
Hellmann's = Hellmanns, Hellmans, Hellman's | hellmanns
Kit Kat = KitKat, Kit-Kat | kitkat
Nestlé = Nestle | nestle
L'Oréal = L'Oreal, Loreal | loreal
Procter & Gamble = Procter and Gamble, Proctor & Gamble, Proctor and Gamble | p&g
Johnson & Johnson = Johnson and Johnson | j&j
Arm & Hammer = Arm and Hammer
Bath & Body Works = Bath and Body Works | bath and body works
Colgate-Palmolive = Colgate Palmolive
Anheuser-Busch = Anheuser Busch
Kraft Heinz = Kraft-Heinz
IKEA = Ikea | ikea
LEGO = Lego | lego
FedEx = Fed Ex | fedex
T-Mobile = TMobile, T Mobile | t mobile
iPhone = I-Phone, I Phone | iphone
YouTube = You Tube | youtube
PlayStation = Play Station | playstation
"""


def parse_brand_spellings(text: str) -> list[tuple[str, list[str], list[str]]]:
    """'Right = wrong, wrong | also, also' lines -> [(right, [wrong…], [search alternates…])]. Lines starting
    with # are notes."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        main, _, alts = line.partition("|")
        right, _, wrong = main.partition("=")
        right = right.strip()
        if not right:
            continue
        split = lambda s: [w.strip() for w in s.split(",") if w.strip()]
        out.append((right, split(wrong), split(alts)))
    return out


def _brand_pattern(name: str) -> str:
    """A loose pattern for a name: any case, straight or curly apostrophe (or none), hyphen / space / nothing
    between parts, accents optional, & or 'and'."""
    accents = {"a": "aàáâäã", "e": "eèéêë", "i": "iìíîï", "o": "oòóôöõ", "u": "uùúûü", "c": "cç", "n": "nñ"}
    out = []
    for ch in unicodedata.normalize("NFC", name):
        base = unicodedata.normalize("NFKD", ch)[0].lower()
        if ch in "'’‘`":
            out.append("['’‘`]?")
        elif ch in "-– ":
            out.append(r"[-–\s]?")
        elif ch == ".":
            out.append(r"\.?")
        elif ch == "&":
            out.append(r"(?:&|\s?and\s?)")
        elif base in accents:
            out.append("[" + accents[base] + accents[base].upper() + "]")
        else:
            out.append(re.escape(ch))
    body = "".join(out)
    return r"(?<![\w'’])" + body + r"(?![\w’]|'\w)"


def brand_rules(text: str):
    """Compiled rules: [(right, regex matching it or a wrong spelling, search alternates)]. Longer names first,
    so 'BJ's Wholesale Club' wins over 'BJ's'."""
    rules = []
    for right, wrong, alts in parse_brand_spellings(text):
        names = sorted({right, *wrong}, key=len, reverse=True)
        rx = re.compile("|".join(_brand_pattern(n) for n in names), re.IGNORECASE)
        rules.append((right, rx, alts))
    rules.sort(key=lambda r: len(r[0]), reverse=True)
    return rules


def fix_brand_spellings(title: str, description: str, keywords, rules, add_alternates: bool = True,
                        keyword_max: int = 50) -> dict:
    """Write every brand the way it's spelled (in the title, description and keywords), and add the other ways
    buyers search for it to the keywords, right after the brand, while there's room.
    Returns {'title', 'description', 'keywords', 'fixes': ["BJs → BJ's", …]}."""
    fixes = []

    def fix_text(text, lower=False):
        if not text:
            return text
        found = []                                   # longer names first; each match becomes a placeholder,
        for right, rx, _alts in rules:              # so a shorter name can't match inside it afterwards
            want = right.lower() if lower else right

            def sub(m, want=want):
                if m.group(0) != want:
                    fixes.append(f"{m.group(0)} → {want}")
                found.append(want)
                return f"\x00{len(found) - 1}\x00"
            text = rx.sub(sub, text)
        return re.sub(r"\x00(\d+)\x00", lambda m: found[int(m.group(1))], text)

    title = fix_text(title)
    description = fix_text(description)
    kws, seen, added = [], set(), []
    for k in keywords or []:
        lower = k == k.lower()
        nk = fix_text(k, lower=lower)
        if nk.casefold() not in seen:
            seen.add(nk.casefold())
            kws.append(nk)
    if add_alternates:
        out = []
        for k in kws:
            out.append(k)
            for right, rx, alts in rules:
                if alts and rx.fullmatch(k):
                    for a in alts[:2]:
                        if a.casefold() not in seen and len(kws) + len(added) < keyword_max:
                            seen.add(a.casefold())
                            added.append(a)
                            out.append(a)
        kws = out
    if added:
        fixes.append("added " + ", ".join(added))
    return {"title": title, "description": description, "keywords": kws, "fixes": fixes}


# Keyword order --------------------------------------------------------------------------------------------------
# general words that rarely win a search on their own: fine to have, but not in the first ten
GENERIC_KEYWORDS = {
    "food", "shelf", "shelves", "store", "retail", "product", "products", "grocery", "supermarket", "shopping",
    "display", "business", "commercial", "consumer", "consumers", "sale", "sales", "market", "aisle", "background",
    "closeup", "close-up", "object", "objects", "item", "items", "package", "packaging", "nobody", "indoors",
    "indoor", "outdoors", "colorful", "color", "colour", "variety", "selection", "choice", "group", "many",
    "lifestyle", "concept", "design", "brand", "brands", "logo", "editorial", "illustrative editorial",
}


def order_keywords(keywords, title: str, lead: int = 0, keep_last=()) -> list[str]:
    """Put the keywords that matter in the first ten (stock sites weigh those the most): your keywords to put
    first (the first `lead`) stay first; then the ones that name what's in the title (subject, brand, product),
    specific before general; then the rest, with general words like 'food', 'shelf', 'retail' last.
    keep_last: keywords left at the very end (the month and year)."""
    keywords = list(keywords or [])
    head, rest = keywords[:lead], keywords[lead:]
    last_set = {k.casefold() for k in keep_last}
    tail = [k for k in rest if k.casefold() in last_set]
    rest = [k for k in rest if k.casefold() not in last_set]
    words = {_singular(w) for w in re.findall(r"[\w']+", (title or "").casefold().replace("'", ""))
             if w not in SMALL_WORDS}

    def in_title(k):
        ws = [_singular(w) for w in re.findall(r"[\w']+", k.casefold().replace("'", "")) if w not in SMALL_WORDS]
        return bool(ws) and all(w in words for w in ws)

    generic = lambda k: k.casefold() in GENERIC_KEYWORDS
    tiers = ([k for k in rest if in_title(k) and not generic(k)], [k for k in rest if in_title(k) and generic(k)],
             [k for k in rest if not in_title(k) and not generic(k)], [k for k in rest if not in_title(k) and generic(k)])
    return head + [k for t in tiers for k in t] + tail


def keyword_order_problem(keywords, title: str, lead: int = 0, keep_last=()) -> str:
    """What's wrong with the first ten keywords, in a few words ('' when they're fine)."""
    better = order_keywords(keywords, title, lead, keep_last)
    now, want = list(keywords or [])[:10], better[:10]
    missing = [k for k in want if k not in now]
    if not missing:
        return ""
    return ("not in the first ten: " + ", ".join(missing[:4]) + ("…" if len(missing) > 4 else ""))


# Look-alike photos (the same shot twice: Shutterstock and Adobe reject near-duplicates) -------------------------
def image_hash(gray_9x8: list[int]) -> int:
    """A difference hash from a 9 × 8 grayscale thumbnail (row by row): 64 bits, each 'is this pixel brighter
    than the next one'. Near-identical photos get hashes a few bits apart."""
    h = 0
    for y in range(8):
        row = gray_9x8[y * 9:(y + 1) * 9]
        for x in range(8):
            h = (h << 1) | (1 if row[x] > row[x + 1] else 0)
    return h


def look_alike_groups(hashes: dict, max_bits: int = 6) -> list[list[str]]:
    """Groups of keys whose image hashes differ by at most max_bits of 64 (the same scene, moments apart)."""
    keys = [k for k, h in hashes.items() if h is not None]
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if bin(hashes[a] ^ hashes[b]).count("1") <= max_bits:
                parent[find(a)] = find(b)
    groups = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    return [g for g in groups.values() if len(g) > 1]
