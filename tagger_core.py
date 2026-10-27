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
APP_VERSION = "2026.10.27.14"
JPEG_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif"}

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


def collect_jpegs(paths) -> tuple[list[str], int]:
    """Expand dropped files/folders (folders searched including subfolders).
    Returns (sorted unique JPEG paths, number of non-JPEG files skipped)."""
    found, skipped, seen = [], 0, set()

    def add(p):
        nonlocal skipped
        if is_jpeg(p):
            key = os.path.normcase(os.path.abspath(p))
            if key not in seen:
                seen.add(key)
                found.append(os.path.abspath(p))
        elif not os.path.basename(p).startswith("."):
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


def clean_title(text: str) -> str:
    t = single_line(text).strip().strip('"').strip("'").strip()
    return t[:-1] if t.endswith(".") and not t.endswith("..") else t


# Symbols in the AI's titles and keywords ------------------------------------------------------
SYMBOL_MODES = [("convert", "Convert to words (& → and, + → plus, @ → at, % → percent)"),
                ("remove", "Remove them"),
                ("keep", "Keep as written")]
SYMBOL_WORDS = [("&", "and"), ("+", "plus"), ("@", "at"), ("%", "percent"), ("°", "degrees")]
SYMBOL_DROP = "™®©#*~^|<>{}[]\\=_`•"          # never useful in a title or keyword


def tidy_symbols(text: str, mode: str = "convert") -> str:
    """Convert symbols to words ('Salt & Pepper' -> 'Salt and Pepper'), remove them, or keep them.
    Normal punctuation (, . - ' : ! ? / ( ) and currency signs) is always left alone."""
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
    "location_for_ai": True,  # also tell the AI the location, so it uses it in keywords
    "location_history": [],   # locations used before, most recent first (the dropdown in Location in title)
    "revise": True,           # double-check each draft against your instructions (one extra, text-only pass)
    "xmp2iptc_on_save": True, # after saving, copy XMP to IPTC with ExifTool's xmp2iptc.args (keeps IPTC in step with XMP)
    "xmp2iptc_args": "",      # path to xmp2iptc.args; blank = find it next to ExifTool, else the bundled copy
    "meta_fields": None,      # which metadata fields are written (None = the usual set, see METADATA_FIELDS)
    "symbols": "convert",     # symbols in the AI's titles and keywords: convert (& -> and) / remove / keep
    "title_length": "detailed",  # short / standard / detailed (see TITLE_LENGTHS)
    "ai_time_limit": 30,      # seconds per photo before it's stopped (0 = no limit); first photo gets extra
    "ai_retries": 1,          # how many times a photo that went over the limit is tried again
    "debug_log": False,       # write a detailed log of every AI request to debug_log.txt
    "check_updates": True,    # look for a newer release on GitHub when the app starts
    "last_seen_version": "",  # the version whose "What's new" was last shown
    "skipped_update": "",     # a version the user chose to skip
    "read_label": True,       # read the text on packaging and signs first (one quick extra step per photo)
    "tidy_keywords": True,    # remove filler keywords and put the title's words first
    "auto_download": False,   # download new versions without asking first (installed on restart)
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


def export_csv(path: str, rows: list[dict], fmt: str = "shutterstock") -> int:
    """Write the photos to a CSV file. rows: dicts with file, folder, title, keywords, editorial (bool),
    and for 'full' also type, note, taken, saved. Returns how many photos were written.
    The upload formats match what Shutterstock and Adobe Stock accept: one row per file, the keywords
    comma-separated in one cell. Columns they need that the app doesn't know (categories) are left
    empty to fill in on the site."""
    import csv
    # one keyword may never contain a comma: on upload it would split into two keywords
    rows = [dict(r, keywords=[" ".join(str(k).replace(",", " ").split()) for k in r["keywords"]]) for r in rows]
    if fmt == "shutterstock":
        head = ["Filename", "Description", "Keywords", "Categories", "Illustration", "Mature Content", "Editorial"]
        body = [[r["file"], r["title"], ",".join(r["keywords"]), "", "no", "no",
                 "yes" if r.get("editorial") else "no"] for r in rows]
        enc = "utf-8"
    elif fmt == "adobe":
        head = ["Filename", "Title", "Keywords", "Category", "Releases"]
        body = [[r["file"], r["title"], ",".join(r["keywords"]), "", ""] for r in rows]
        enc = "utf-8"
    else:
        head = ["File", "Folder", "Title", "Title length", "Keywords", "Keyword count", "Photo type",
                "Photo notes", "Date taken", "Editorial", "Saved"]
        body = [[r["file"], r["folder"], r["title"], len(r["title"]), ", ".join(r["keywords"]), len(r["keywords"]),
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


def check_for_update(current: str = APP_VERSION, opener=None, timeout: float = 6) -> dict | None:
    """Ask GitHub for the latest release. Returns {'version', 'url', 'notes'} if it's newer than
    `current`, else None. Only the public release info is fetched; nothing is sent about you or your
    photos. Any problem (offline, GitHub unreachable, no releases) just means 'no update'."""
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
def parse_location(location: str) -> dict:
    """'City, State, Country' -> parts. 'Tokyo, Japan' -> city + country; one part -> city."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        return {"city": "", "state": "", "country": ""}
    if len(parts) == 1:
        return {"city": parts[0], "state": "", "country": ""}
    if len(parts) == 2:
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
     "tip": "The older IPTC title field (up to 64 bytes). Still read by many sites and tools.",
     "args": lambda t, k, d, l: _set("IPTC:ObjectName", truncate_utf8(t, IPTC_TITLE_MAX))},
    {"key": "headline", "group": "Title", "label": "Headline (XMP and IPTC)", "default": True,
     "tip": "A short headline. Editorial agencies and Photo Mechanic use it as the title.",
     "args": lambda t, k, d, l: _set("XMP-photoshop:Headline", t) + _set("IPTC:Headline", truncate_utf8(t, 256))},
    {"key": "title_windows", "group": "Title", "label": "Windows Title (XPTitle)", "default": False,
     "tip": "The title Windows File Explorer shows in some older versions. Usually not needed.",
     "args": lambda t, k, d, l: _set("EXIF:XPTitle", t)},
    {"key": "description", "group": "Description", "label": "Description / Caption (XMP and IPTC)", "default": True,
     "tip": "The title is also saved as the description. Shutterstock and other stock sites use it as the title.",
     "args": lambda t, k, d, l: _set("XMP-dc:Description", d) + _set("IPTC:Caption-Abstract", truncate_utf8(d, 2000))},
    {"key": "description_exif", "group": "Description", "label": "EXIF Image Description", "default": False,
     "tip": "The camera-style description field. Some older programs read only this one.",
     "args": lambda t, k, d, l: _set("EXIF:ImageDescription", d)},
    {"key": "keywords_xmp", "group": "Keywords", "label": "XMP Keywords (dc:subject)", "default": True,
     "tip": "The standard keyword field. Read by Lightroom, Bridge, Adobe Stock, Windows and most stock sites.",
     "args": lambda t, k, d, l: _kw_args("XMP-dc:Subject", k)},
    {"key": "keywords_iptc", "group": "Keywords", "label": "IPTC Keywords", "default": True,
     "tip": "The older IPTC keyword field (each up to 64 bytes). Still read by many sites and tools.",
     "args": lambda t, k, d, l: _kw_args("IPTC:Keywords", k, IPTC_KEYWORD_MAX)},
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


class ExifTool:
    """Runs ExifTool with an argument file, so any file name or Unicode text is safe."""

    def __init__(self, exe: str):
        self.exe = exe

    def _run(self, args: list[str], timeout: int = 180) -> str:
        fd, argfile = tempfile.mkstemp(suffix=".args", prefix="iptc_tagger_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                for a in ["-charset", "filename=utf8"] + args:
                    if "\n" in a or "\r" in a:
                        raise ExifToolError("line breaks are not allowed in metadata values")
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

    def read(self, paths: list[str]) -> dict[str, dict]:
        """Current title and keywords for each file, keyed by the path given."""
        result = {}
        for start in range(0, len(paths), 200):
            chunk = paths[start:start + 200]
            out = self._run(["-j", "-G1", "-a", "-api", "struct=0",
                             "-XMP-dc:Title", "-IPTC:ObjectName",
                             "-XMP-dc:Subject", "-IPTC:Keywords",
                             "-EXIF:DateTimeOriginal", "-XMP-exif:DateTimeOriginal", "-EXIF:CreateDate"] + chunk)
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
                result[src] = {"title": str(title), "keywords": kws, "taken": taken}
            for p in chunk:
                result.setdefault(p, {"title": "", "keywords": [], "taken": ""})
        return result

    def xmp_to_iptc(self, path: str, args_file: str) -> None:
        """Copy XMP to IPTC with ExifTool's xmp2iptc.args: -overwrite_original -P -m
        -charset iptc=utf8 -codedcharacterset=utf8 -tagsFromFile @ -@ xmp2iptc.args"""
        out = self._run(["-overwrite_original", "-P", "-m", "-charset", "iptc=utf8", "-codedcharacterset=utf8",
                         "-tagsFromFile", "@", "-@", args_file, path])
        if "1 image files updated" not in out and "1 image files unchanged" not in out:
            raise ExifToolError("the XMP to IPTC conversion didn't update the file: " + (out.strip() or "no output"))

    def write(self, path: str, title: str, keywords: list[str], description: str | None = None,
              fields=None, location: str = "") -> None:
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
        desc = clean_title(description if description is not None else title)
        args = ["-P", "-overwrite_original", "-charset", "iptc=UTF8", "-IPTC:CodedCharacterSet=UTF8"]
        for f in METADATA_FIELDS:
            if f["key"] in fields:
                args += f["args"](title, keywords, desc, location)
        out = self._run(args + [path])
        if "1 image files updated" not in out and "1 image files unchanged" not in out:
            raise ExifToolError(out.strip() or "ExifTool did not update the file")


# Undo a save: every tag a save can touch (as ExifTool reports it with -G1 -> the name to write it back)
UNDO_TAGS = [("XMP-dc:Title", "XMP-dc:Title"), ("IPTC:ObjectName", "IPTC:ObjectName"),
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
    out = et._run(["-j", "-G1", "-a", "-api", "struct=0", "-charset", "iptc=UTF8"]
                  + ["-" + t.split(":", 1)[1] for t, _ in UNDO_TAGS] + [path])
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
    args = ["-P", "-overwrite_original", "-charset", "iptc=UTF8", "-IPTC:CodedCharacterSet=UTF8"]
    for _, tag in UNDO_TAGS:
        v = (snap or {}).get(tag)
        args.append(f"-{tag}=")                       # clear first (also removes every keyword)
        if isinstance(v, list):
            args += [f"-{tag}={single_line(x)}" for x in v]
        elif v:
            args.append(f"-{tag}={single_line(v)}")

    def run():
        out = et._run(args + [path])
        if "1 image files updated" not in out and "1 image files unchanged" not in out:
            raise ExifToolError(out.strip() or "ExifTool did not update the file")
    _guarded(path, run)


_undo_lock = threading.Lock()


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


def _guarded(path: str, action) -> None:
    """Run a metadata change, then prove the picture itself is untouched. If the image data ever
    differs, the original file is restored exactly and an error raised."""
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
               xmp2iptc_args: str | None = None, fields=None, location: str = "") -> None:
    """Write metadata, then prove the picture itself is untouched. If the image
    data ever differs, the original file is restored exactly and an error raised."""
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
        if fields is None:
            et.write(path, title, keywords, description)
        else:
            et.write(path, title, keywords, description, fields=fields, location=location)
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


def title_case(text: str) -> str:
    """SEO Title Case: capitalise each word except small words in the middle; leave words
    that already have their own capitals (iPhone, NASA, McDonald's) exactly as they are."""
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
            if not edge and bare in SMALL_WORDS:
                return part.lower()
            j = next(k for k, c in enumerate(part) if c.isalpha())
            return part[:j] + part[j].upper() + part[j + 1:]

        parts = word.split("-")
        out.append("-".join(fix(pt, starts or last or k > 0) for k, pt in enumerate(parts)))
    return " ".join(out)


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
        filled = re.sub(r"\s+([,;:])", r"\1", " ".join(filled.split()))   # "Durham :" -> "Durham:"
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
# what goes between the location and the date ("Durham, NC - October 26, 2026")
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


LOCATION_DATE_JOIN = _LOC_JOIN["dash"]   # editorial dateline: "Durham, North Carolina, USA - October 26, 2026"


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


def strip_title_extras(title: str, prefix: str = "", date: str = "", location: str = "") -> str:
    """The bare title: remove your text before titles, the location and the date taken (in any of the
    positions and separators compose_title uses), so only the description itself is left."""
    t = (title or "").strip()
    seps = r"[\s,;:|–—-]*"
    location = (location or "").strip()
    pats = []
    if location and date:                                        # "Location - Date" (any joiner)
        pats.append(re.escape(location) + r"\s*[,;|–—-]?\s*" + re.escape(date))
    if date:
        pats.append(re.escape(date))

    def drop_start(x):
        for p in pats:
            x = re.sub(r"^\s*[\(\[]?\s*" + p + r"\s*[\)\]]?" + seps, "", x, flags=re.I)
        if location:   # the location on its own only counts when punctuation follows it ("Durham:" not "Durham Bulls")
            x = re.sub(r"^\s*" + re.escape(location) + r"\s*[,;:|–—-]+\s*", "", x, flags=re.I)
        return x

    t = drop_start(t)                                            # location/date at the very start
    if prefix and t.casefold().startswith(prefix.strip().casefold()):
        t = t[len(prefix.strip()):].strip()                     # your text before titles
    t = drop_start(t)                                            # location/date just before the title
    for p in pats:                                               # location/date after the title
        t = re.sub(r"[\s,;|–—-]*[\(\[]?\s*" + p + r"\s*[\)\]]?\s*$", "", t, flags=re.I)
    if location:
        t = re.sub(r"\s*[,;|–—-]+\s*" + re.escape(location) + r"\s*$", "", t, flags=re.I)
    return t.strip(" ,;:|–—-")


def drop_trailing_location(title: str, location: str) -> str:
    """'Pier at Sunset in Durham, North Carolina' -> 'Pier at Sunset' when that location is added
    to the title separately (so the place isn't in the title twice)."""
    location = (location or "").strip()
    if not location or not title:
        return title
    parts = [re.escape(location)]
    first = location.split(",")[0].strip()
    if first and first != location:
        parts.append(re.escape(first) + r"(?:\s*,[^,]{1,40}){0,2}")        # "in Durham, NC" with a shorter location form
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


def place_editorial_keywords(keywords, extra, pos: str = "before", after: int = 0) -> list[str]:
    """Put the Editorial keywords at the start (after the first `after` keywords, i.e. your keywords to put
    first) or at the end of the list. Ones already in the list aren't added twice."""
    keywords = clean_keywords(keywords)
    if pos == "after":
        have = {k.casefold() for k in keywords}
        return keywords + [k for k in clean_keywords(extra) if k.casefold() not in have]
    head = keywords[:after]
    head_set = {k.casefold() for k in head}
    extra = [k for k in clean_keywords(extra) if k.casefold() not in head_set]
    moved = {k.casefold() for k in extra}               # a suggestion like "tokyo" moves up with them
    return head + extra + [k for k in keywords[after:] if k.casefold() not in moved]


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
    return (SYSTEM_PROMPT + "\n\nThe photographer's directions below are your highest priority. They override "
            "every default rule (title style, length, wording and keyword rules). Follow each one exactly:\n"
            + direction.strip())

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

MORE_KEYWORDS_SCHEMA = {
    "type": "object",
    "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["keywords"],
}

# "Read the label first": a quick first look that only copies the text printed on the product or sign
LABEL_SCHEMA = {
    "type": "object",
    "properties": {"brand": {"type": "string"}, "product": {"type": "string"},
                   "variety": {"type": "string"}, "size": {"type": "string"},
                   "parent_company": {"type": "string"},
                   "other_text": {"type": "array", "items": {"type": "string"}}},
    "required": ["brand", "product", "variety", "size", "parent_company", "other_text"],
}

LABEL_PROMPT = (
    "Look closely at this photo and copy the text you can actually read on the main product, packaging, sign, "
    "screen or storefront. Don't describe the photo. Fill in: brand (the brand or store name), product (the exact "
    "product name or product line), variety (flavour, scent, style, colour or edition printed on it), size (weight, "
    "volume or count printed on it), parent_company (the company that owns the brand, only if you are sure, "
    "otherwise empty) and other_text (up to 6 other short pieces of legible text, such as a slogan or a claim like "
    "'gluten free'). Spell everything exactly as printed. Leave a field empty if it can't be read; never guess. "
    'Output only JSON: {"brand": "", "product": "", "variety": "", "size": "", "parent_company": "", '
    '"other_text": []}')


def label_note(label: dict) -> str:
    """The text read from the photo, as a line for the main prompt ('' if nothing was readable)."""
    if not isinstance(label, dict):
        return ""
    parts = []
    for key, name in (("brand", "brand"), ("product", "product"), ("variety", "variety or flavour"),
                      ("size", "size"), ("parent_company", "parent company")):
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
     "its variety, flavour, scent or size from the packaging, and the brand's parent company"),
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
- If the input is only a retail product name, the photo shows that product sitting on a grocery or retail store shelf; name its variety, flavour or size from the packaging, and its parent company.
- If the input is a name followed by the word "app", the photo shows that app on a phone screen in the Google Play Store.
- Otherwise, treat the input as accurate information about the photo.
- If there is no input, describe what you see in the photo."""

# How long titles should be: (key, label, fewest words, most words)
TITLE_LENGTHS = [("short", "Short (6–10 words)", 6, 10),
                 ("standard", "Standard (10–15 words)", 10, 15),
                 ("detailed", "Detailed (14–22 words)", 14, 22)]


def title_words(length: str = "detailed") -> tuple[int, int]:
    return next(((lo, hi) for k, _, lo, hi in TITLE_LENGTHS if k == length), (14, 22))


def title_rules(length: str = "detailed") -> str:
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
            "or product line and its variety, flavour, scent, style or size as read from the packaging (for example "
            "'Doritos Cool Ranch Flavored Tortilla Chips' rather than 'Bag of Chips'); and for a brand, product, store, "
            "restaurant or company, name its well-known parent company too (for example 'Doritos, a Frito-Lay and "
            "PepsiCo Brand' or 'KFC Restaurant, Owned by Yum! Brands'), but only when you are sure of it. "
            "Join the parts naturally with commas or words such as with, on and in. "
            "The title must be complete and stand on its own, describing only what is in this photo. Not short and "
            "blunt: for example, instead of 'Coffee Shop Exterior' write 'Independent Coffee Shop Exterior, Cozy "
            "Neighborhood Cafe with Brick Facade, Outdoor Seating and Hanging Sign on a City Street'. No quotation marks and no full stop at the end.")


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


def build_prompt(keyword_min: int, keyword_max: int, context: str = "", existing_title: str = "",
                 existing_keywords=None, direction: str = "", photo_note: str = "",
                 photo_type: str = "", lead_keywords=None, title_prefix: str = "",
                 date_in_title: bool = False, title_location: str = "", copyright_free: bool = False,
                 symbols: str = "keep", feedback: str = "", rejected_title: str = "",
                 title_length: str = "detailed") -> str:
    """The instructions for one photo. Your notes come first and the title is built around them;
    your directions are repeated at the end. (title_prefix is never shown to the AI.)"""
    context, photo_note, direction = (context or "").strip(), (photo_note or "").strip(), (direction or "").strip()
    lines = ["Write the title and keywords for this stock photo."]
    if context:
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
        "", title_rules(title_length) + (" Work the facts from the photographer's notes into the title."
                                         if context or photo_note else ""),
        "",
        f"Keywords: between {keyword_min} and {keyword_max} unique SEO keywords, most relevant first. All "
        "lowercase, letters and numbers only: no special characters, punctuation or hashtags. No duplicates "
        "or near-duplicates (such as singular and plural of the same word), and don't repeat the same word "
        "across many keywords. Cover the subject, what it is known for, its industry or category, setting, "
        "objects, colours, concepts and likely search terms. For a product or brand, include the brand, the "
        "product line, its variety or flavour and the parent company as keywords.",
        "", COPYRIGHT_FREE_RULES if copyright_free else BRAND_RULES,
    ]
    if (feedback or "").strip():
        lines += ["", "REDO: the photographer rejected the previous suggestion"
                  + (f" ({json.dumps(rejected_title, ensure_ascii=False)})" if rejected_title else "")
                  + ". Write a clearly better, different title and keywords that fix these problems:",
                  feedback.strip()]
    if symbols in ("convert", "remove"):
        lines += ["", "No symbols: write words instead of symbols in the title and keywords (\"and\" instead of "
                  "\"&\", \"plus\" instead of \"+\", \"percent\" instead of \"%\") and don't use symbols such as "
                  "@ # * ™ ® ©."]
    # The "text before titles" is deliberately NOT shown to the AI: it writes a complete title for the
    # photo alone, and the app puts the text in front afterwards (title_prefix is ignored here).
    if (title_location or "").strip():
        lines += ["", f"The photo was taken in {title_location.strip()}. The photographer adds this location to the "
                  "title themselves, so don't put the place name in your title; describe the subject instead. "
                  "Do use the place in the keywords."]
    if date_in_title:
        lines += ["", "The photographer adds the date the photo was taken to the title themselves, so don't put "
                  "a date in your title."]
    if lead_keywords:
        lines += ["", "The photographer adds these keywords at the front themselves. Use them as information "
                  "about the photo (for example place or date), but don't repeat them in your list: "
                  + ", ".join(lead_keywords)]
    if existing_title or existing_keywords:
        lines += ["", "Metadata already on the file, for reference only. Write a new title that follows the "
                  "instructions here rather than copying it: "
                  + json.dumps({"title": existing_title, "keywords": list(existing_keywords or [])},
                               ensure_ascii=False)]
    if direction:
        lines += ["", "PHOTOGRAPHER'S DIRECTIONS (highest priority: follow every one exactly, even where it "
                  "differs from the rules above):", direction]
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


def redo_feedback(reasons, other: str = "") -> str:
    """The instruction text for the chosen reasons (keys from REDO_REASONS) plus anything typed."""
    lines = [text for key, _, text in REDO_REASONS if key in set(reasons or [])]
    if (other or "").strip():
        lines.append("The photographer also says: " + single_line(other))
    return "\n".join("- " + l for l in lines)


def build_revision_prompt(draft_title: str, draft_keywords, keyword_min: int, keyword_max: int,
                          context: str = "", photo_note: str = "", direction: str = "",
                          title_location: str = "", copyright_free: bool = False, feedback: str = "") -> str:
    """Second pass (text only): bring a draft in line with your notes and directions."""
    lines = ["Check this draft title and keyword list for a stock photo against the photographer's instructions."]
    if (feedback or "").strip():
        lines += ["", "The photographer rejected an earlier suggestion for these reasons; make sure the draft fixes them:",
                  feedback.strip()]
    if copyright_free:
        lines += ["", COPYRIGHT_FREE_RULES, "Replace every brand, trademark, product, character or person's name in "
                  "the draft with generic words, and remove any keyword that is such a name."]
    if (title_location or "").strip():
        lines += ["", f"The location ({title_location.strip()}) is added to the title separately: remove the place "
                  "name from the title if it's there, but keep it in the keywords."]
    if (context or "").strip():
        lines += ["", "ABOUT THESE PHOTOS (facts; the title should reflect them):", context.strip()]
    if (photo_note or "").strip():
        lines += ["", "ABOUT THIS PHOTO (fact; the title and keywords should reflect it"
                  + (", in generic words" if copyright_free else "") + "):", photo_note.strip()]
    if (direction or "").strip():
        lines += ["", "PHOTOGRAPHER'S DIRECTIONS (follow every one exactly; they override all other rules):",
                  direction.strip()]
    lines += ["", "Draft:", json.dumps({"title": draft_title, "keywords": list(draft_keywords or [])}, ensure_ascii=False),
              "", "Rewrite the draft so that it follows every direction exactly and works the facts above into the "
              "title where they apply. Keep whatever already complies. The title must still be a complete "
              f"description of the photo. Keep between {keyword_min} and {keyword_max} keywords"
              + ("" if keyword_case_overridden(direction) else ", lowercase") + ", with no duplicates.",
              "", 'Output only JSON in exactly this form: {"title": "...", "keywords": ["...", "..."]}']
    return "\n".join(lines)


def prepare_image(path: str, max_edge: int = PREVIEW_MAX_EDGE) -> str:
    """Small upright JPEG copy for the AI model, made in memory only. Returns base64."""
    from PIL import Image, ImageOps   # imported here so the rest works without Pillow
    with Image.open(path) as im:
        im.draft("RGB", (max_edge, max_edge))       # fast reduced-size JPEG decode
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


NOT_RUNNING = ("Ollama isn't running. Open the Ollama app (free from ollama.com), "
               "then try again.")


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


def identify(path: str, url: str, model: str, keyword_min: int, keyword_max: int, context: str = "",
             existing_title: str = "", existing_keywords=None, direction: str = "",
             photo_note: str = "", photo_type: str = "", lead_keywords=None, title_prefix: str = "",
             date_in_title: bool = False, revise: bool = True, title_location: str = "",
             copyright_free: bool = False, opener=None, cancel: CancelToken | None = None,
             symbols: str = "keep", feedback: str = "", rejected_title: str = "",
             title_length: str = "detailed", expand_short: bool = False,
             read_label: bool = False, tidy: bool = False) -> dict:
    """Ask the local vision model for an SEO title and keywords.
    With revise=True and any notes or directions given, a second (text-only) pass checks the draft
    against them. Returns {'title', 'keywords', 'seconds', 'revised'}."""
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
                          title_location, copyright_free, symbols, feedback, rejected_title, title_length)
    system = system_prompt(direction)

    def body(user_text, schema, with_image=True, temperature=0.2):
        user = {"role": "user", "content": user_text}
        if with_image:
            user["images"] = [image_b64]
        return {
            "model": model, "stream": False, "format": schema,
            "keep_alive": "15m",                         # stay loaded between photos in a batch
            # num_predict caps the reply: a normal one is ~250 tokens, but now and then a model in JSON mode
            # gets stuck writing endless blank space; the cap ends that in seconds instead of minutes
            "options": {"temperature": temperature, "num_ctx": 8192, "num_predict": MAX_REPLY_TOKENS},
            "messages": [{"role": "system", "content": system}, user],
        }

    def ask(user_text, schema, step="draft", **kw):
        """One request to the model. A reply that comes back cut off or unreadable is asked again once,
        a little more freely (a re-roll almost always fixes a model that got stuck)."""
        need = ("title" if schema in (OUTPUT_SCHEMA, TITLE_SCHEMA) else "brand" if schema is LABEL_SCHEMA
                else "keywords")                     # what a usable reply must have
        for attempt in (1, 2):
            if cancel is not None and cancel.is_set():
                debug(f"{name}: {step}: stopped before sending")
                raise Stopped(STOPPED)
            t = time.monotonic()
            debug(f"{name}: {step}: request sent (attempt {attempt}, prompt {len(user_text)} chars"
                  f"{', with image' if kw.get('with_image', True) else ', text only'})")
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
            usable = bool(parsed.get("title")) if need == "title" else need in parsed
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
    # a redo is asked a little more freely, so it doesn't come back with the same words
    draft = ask(prompt, OUTPUT_SCHEMA, "draft", temperature=0.5 if (feedback or "").strip() else 0.2)
    raw_title = str(draft.get("title", "") or "").strip()
    raw_keywords = _as_list(draft.get("keywords", []))

    revised = False
    if revise and raw_title and (copyright_free or any((t or "").strip()
                                                       for t in (direction, own_context, photo_note, feedback))):
        # second pass: make the draft follow your notes and directions (fast: text only, no image)
        try:
            fixed = ask(build_revision_prompt(raw_title, raw_keywords, keyword_min, keyword_max,
                                              context, photo_note, direction, title_location, copyright_free,
                                              feedback),
                        OUTPUT_SCHEMA, "double-check", with_image=False, temperature=0.1)
            new_title = str(fixed.get("title", "") or "").strip()
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
                  + "Title Case, no quotation marks and no full stop at the end. "
                  'Output only JSON: {"title": "..."}')
        try:
            longer = str(ask(expand, TITLE_SCHEMA, "longer title").get("title", "") or "").strip()
            if len(longer.split()) > len(raw_title.split()):
                debug(f"{name}: title was {len(raw_title.split())} words, expanded to {len(longer.split())}")
                raw_title = longer
        except Stopped:
            raise
        except ApiError:
            pass                                     # keep the short one
    raw_title = tidy_symbols(drop_trailing_location(raw_title, title_location), symbols)
    title = clean_title(raw_title) if title_case_overridden(direction) else title_case(raw_title)
    raw_keywords = [tidy_symbols(str(k), symbols) for k in raw_keywords]
    if copyright_free:
        raw_keywords = drop_brand_keywords(raw_keywords)
    keywords = seo_keywords(raw_keywords, keep=lead_keywords, lower=lower, protect=protect)
    if tidy:
        keywords = tidy_keywords(keywords, title, protect)
    keywords = keywords[:keyword_max]

    if title and len(keywords) < keyword_min:
        # one follow-up request for more keywords so every photo gets at least the minimum
        want = keyword_max - len(keywords)
        more_prompt = (prompt + "\n\nYou have already chosen these keywords: " + ", ".join(keywords)
                       + f".\nGive {want} MORE keywords for the same photo, following the same rules and the "
                       "photographer's directions, none of them repeating or rewording the ones above. "
                       "Output only JSON: {\"keywords\": [\"...\"]}")
        try:
            more = ask(more_prompt, MORE_KEYWORDS_SCHEMA, "more keywords")
            extra = [tidy_symbols(str(k), symbols) for k in _as_list(more.get("keywords", []))]
            if copyright_free:
                extra = drop_brand_keywords(extra)
            extra = seo_keywords(extra, keep=list(lead_keywords or []) + keywords, lower=lower, protect=protect)
            if tidy:
                extra = tidy_keywords(extra, title, protect)
            keywords += extra[:keyword_max - len(keywords)]
        except Stopped:
            raise
        except ApiError:
            pass                                     # keep what we have; the photo still gets its title

    if not title and not keywords:
        debug(f"{name}: FAILED: no title or keywords after {time.monotonic() - started:.1f} s")
        raise ApiError("The AI model returned no title or keywords for this photo. Try again or try another model.")
    debug(f"{name}: DONE in {time.monotonic() - started:.1f} s: {len(keywords)} keywords, revised={revised}, "
          f"title={title!r}")
    result = {"title": title, "keywords": keywords, "seconds": round(time.monotonic() - started, 1),
              "revised": revised, "label": read}
    if copyright_free:
        names = brand_names_in(title)
        if names:
            result["warning"] = "check the title, it may name a brand: " + ", ".join(names)
    return result
