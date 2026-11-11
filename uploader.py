"""Uploading photos to stock sites by FTP, FTPS or SFTP.

Adapted from StockUp (github.com/kefirman9000/stockup): the same connections (encrypted first), the same safety
rules (a half-sent photo is removed from the server, nothing already on a server is overwritten, nothing is sent
twice) and Pause / Resume / Stop that work part-way through a photo. Logins are shared with StockUp when you have
it: usernames are read from its settings and passwords from the same keychain entries, so you don't type them
twice. IPTC Tagger never writes to StockUp's files."""
import configparser
import csv
import ftplib
import hashlib
import json
import os
import re
import ssl
import sys
import threading
import time
import unicodedata
from datetime import datetime

import tagger_core as core

KEYRING_SERVICE = "IPTC Tagger"
STOCKUP_SERVICE = "StockUp"
UPLOAD_EXT = (".jpg", ".jpeg")      # what the sites take as photos

# What each site needs to connect, and what's left to do on the site after the upload.
SITES = {
    "shutterstock": {
        "label": "Shutterstock", "short": "SS",
        "ftp": {"protocol": "ftps", "host": "ftps.shutterstock.com", "port": 21},
        "login_hint": "Your Shutterstock contributor email or username, and password.",
        "safe_names": True,
        "after": "Wait until the photos show up on Shutterstock's Submit page (a few minutes). Then click CSV "
                 "there and choose the Shutterstock CSV: Make Shutterstock CSV below makes it. That's how "
                 "Shutterstock gets your categories and the editorial flag.",
    },
    "adobe": {
        "label": "Adobe Stock", "short": "AD",
        "ftp": {"protocol": "sftp", "host": "sftp.contributor.adobestock.com", "port": 22},
        "login_hint": "Only if Adobe's Upload page shows SFTP details: copy the username and password from there.",
        "after": "In Adobe Stock's Uploaded Files › New, click Upload CSV and choose the Adobe Stock CSV "
                 "(Export CSV… › Adobe Stock). Tick editorial there for your editorial photos.",
    },
    "dreamstime": {
        "label": "Dreamstime", "short": "DT",
        "ftp": {"protocol": "ftp", "host": "upload.dreamstime.com", "port": 21},
        "login_hint": "Your Dreamstime user ID (the number on Dreamstime's FTP page) and your account password.",
        "after": "When the photos show up in Dreamstime's Unfinished section (it can take a while), click Send "
                 "Dreamstime CSV: Dreamstime only reads its CSV from your FTP folder, and only for photos already "
                 "there. It fills in titles, descriptions, keywords, categories and editorial.",
        "csv_by_ftp": True,     # its CSV goes in the FTP folder, not through the website
    },
    "alamy": {
        "label": "Alamy", "short": "AL",
        "ftp": {"protocol": "ftp", "host": "upload.alamy.com", "port": 21},
        "login_hint": "The email you sign in to Alamy with, and your Alamy password.",
        "after": "Alamy reads the caption and keywords saved in the photos. In Alamy Image Manager, tick "
                 "'Sell for editorial only' for your editorial photos.",
    },
    "depositphotos": {
        "label": "Depositphotos", "short": "DP",
        "ftp": {"protocol": "ftp", "host": "ftp.depositphotos.com", "port": 21},
        "login_hint": "Your Depositphotos username and password (the same as the website).",
        "safe_names": True,
        "after": "Depositphotos reads the title and keywords saved in the photos. Check them in its upload tool.",
    },
    "123rf": {
        "label": "123RF", "short": "RF",
        "ftp": {"protocol": "ftp", "host": "ftp.123rf.com", "port": 21},
        "login_hint": "Your 123RF username and password.",
        "after": "123RF reads the title and keywords saved in the photos. Tick editorial there for your "
                 "editorial photos.",
    },
    "pond5": {
        "label": "Pond5", "short": "P5",
        "ftp": {"protocol": "ftp", "host": "ftp.pond5.com", "port": 21},
        "login_hint": "Your Pond5 FTP username and FTP password (Pond5 Dashboard › Account › FTP; it's not your "
                      "login password).",
        "after": "Pond5 reads the title and keywords saved in the photos. Check them in Pond5's Uploads.",
    },
    # These take FTP uploads too, but show their server on your contributor page instead of publishing it:
    # copy it into Logins once.
    "freepik": {
        "label": "Freepik (Magnific)", "short": "FP", "needs_host": True,
        "ftp": {"protocol": "ftp", "host": "", "port": 21},
        "login_hint": "In Freepik's contributor panel › Upload › FTP: the host, port, FTP user and password "
                      "(Get password). FTP opens after your first 120 approved files.",
        "after": "Freepik reads the title and keywords saved in the photos. Check them in its Upload section.",
    },
    "vecteezy": {
        "label": "Vecteezy", "short": "VZ", "needs_host": True,
        "ftp": {"protocol": "sftp", "host": "", "port": 22},
        "login_hint": "In Vecteezy's contributor dashboard › Upload › sFTP: the host, username and password.",
        "after": "Vecteezy uses the title and keywords saved in the photos. Finish them on its Add Data page.",
    },
    "yay": {
        "label": "Yay Images", "short": "YY", "needs_host": True,
        "ftp": {"protocol": "ftp", "host": "", "port": 21},
        "login_hint": "On Yay Images' contributor upload page: the FTP host, username and password.",
        "after": "Yay Images reads the title and keywords saved in the photos.",
    },
    "colourbox": {
        "label": "Colourbox", "short": "CB", "needs_host": True,
        "ftp": {"protocol": "ftp", "host": "", "port": 21},
        "login_hint": "On Colourbox's contributor page: the FTP host, username and password.",
        "after": "Colourbox reads the title and keywords saved in the photos.",
    },
    # any other agency with FTP: you type its name and details
    "other1": {
        "label": "Other agency 1", "short": "O1", "needs_host": True, "custom": True,
        "ftp": {"protocol": "ftp", "host": "", "port": 21},
        "login_hint": "Any other agency that takes FTP uploads: its name, host, port, username and password.",
        "after": "Check the photos on that agency's site.",
    },
    "other2": {
        "label": "Other agency 2", "short": "O2", "needs_host": True, "custom": True,
        "ftp": {"protocol": "ftp", "host": "", "port": 21},
        "login_hint": "Any other agency that takes FTP uploads: its name, host, port, username and password.",
        "after": "Check the photos on that agency's site.",
    },
}
SITE_ORDER = list(SITES)
SITE_GROUPS = [   # how they're shown in Logins
    ("Agencies with a known upload server", [k for k, v in SITES.items() if not v.get("needs_host")]),
    ("Agencies that show their server on your contributor page", [k for k, v in SITES.items()
                                                                     if v.get("needs_host") and not v.get("custom")]),
    ("Your own", [k for k, v in SITES.items() if v.get("custom")]),
]


def site_label(name: str, login: dict | None = None) -> str:
    """A site's name; for Other agency, the name you gave it."""
    if login and login.get("label") and SITES[name].get("custom"):
        return login["label"]
    return SITES[name]["label"]


def _conf(name: str) -> str:
    return os.path.join(core.config_dir(), name)


def logins_path() -> str:
    return _conf("upload_logins.ini")


def history_path() -> str:
    return _conf("upload_history.json")


def receipt_path() -> str:
    return _conf("upload_receipt.csv")


def partial_path() -> str:
    return _conf("upload_partial.json")


def stockup_dirs() -> list[str]:
    """Where StockUp keeps its settings, if you have it."""
    if sys.platform == "darwin":
        return [os.path.expanduser("~/Library/Application Support/StockUp")]
    if os.name == "nt":
        return [os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "StockUp")]
    return [os.path.join(os.path.expanduser("~"), ".config", "stockup")]


def safe_name(name: str) -> str:
    """A file name every site takes: plain letters, numbers, dot, dash and underscore."""
    stem, ext = os.path.splitext(name)
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "photo"
    return stem + (ext.lower() if ext.lower() in UPLOAD_EXT else ".jpg")


def remote_name(site: str, path: str) -> str:
    name = os.path.basename(path)
    return safe_name(name) if SITES[site].get("safe_names") else name


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- the keychain -------------------------------------------------------------------------------------
_KEYRING = []
_kr_lock = threading.Lock()


def _keyring():
    """The computer's keychain (Credential Manager on Windows, Keychain on a Mac), or None."""
    with _kr_lock:
        if not _KEYRING:
            try:
                import keyring
                kr = keyring.get_keyring()
                name = type(kr).__name__.lower()
                ok = not ("fail" in name or "null" in name or "plaintext" in name)
                _KEYRING.append(keyring if ok else None)
            except Exception:
                _KEYRING.append(None)
        return _KEYRING[0]


def keychain_name() -> str:
    if not _keyring():
        return ""
    return {"darwin": "your Mac's Keychain", "win32": "Windows Credential Manager"}.get(sys.platform,
                                                                                         "your system keychain")


def _get_password(service: str, site: str) -> str:
    kr = _keyring()
    if not kr:
        return ""
    try:
        return kr.get_password(service, site) or ""
    except Exception:
        return ""


# ---------- logins ---------------------------------------------------------------------------------------------
_ini_lock = threading.Lock()


def _read_ini(path: str) -> dict:
    cp = configparser.ConfigParser(interpolation=None)
    if os.path.exists(path):
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, OSError):
            return {}
    out = {}
    for site in SITE_ORDER:
        if cp.has_section(site):
            sec = cp[site]
            out[site] = {k: sec.get(k, "") for k in ("protocol", "host", "port", "username", "password",
                                                       "remote_dir", "label")}
    return out


def _write_ini(values: dict):
    path = logins_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# IPTC Tagger upload logins. Usernames are kept here; passwords are kept in "
             + (keychain_name() or "this file") + ".", ""]
    for site in SITE_ORDER:
        v = values.get(site) or {}
        if not v.get("username") and not v.get("password"):
            continue
        ftp = SITES[site]["ftp"]
        lines += [f"[{site}]", f"protocol = {v.get('protocol') or ftp['protocol']}",
                  f"host = {v.get('host') or ftp['host']}", f"port = {v.get('port') or ftp['port']}",
                  f"username = {v.get('username', '')}", f"password = {v.get('password', '')}",
                  f"remote_dir = {v.get('remote_dir') or '/'}"] + (
                     [f"label = {v['label']}"] if v.get("label") else []) + [""]
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # readable by you only
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def stockup_logins() -> dict:
    """Your StockUp logins, read only: {site: {username, password, ...}} for the sites you set up there."""
    for d in stockup_dirs():
        vals = _read_ini(os.path.join(d, "stockup_sites.ini"))
        if vals:
            out = {}
            for site, v in vals.items():
                if v.get("username"):
                    v = dict(v)
                    v["password"] = v.get("password") or _get_password(STOCKUP_SERVICE, site)
                    out[site] = v
            return out
    return {}


def load_sites() -> list[dict]:
    """Every site with its login: yours in IPTC Tagger first, else the one you saved in StockUp."""
    with _ini_lock:
        mine = _read_ini(logins_path())
    theirs = None
    sites = []
    for site in SITE_ORDER:
        v = mine.get(site) or {}
        source = "IPTC Tagger" if v.get("username") else ""
        password = v.get("password") or (_get_password(KEYRING_SERVICE, site) if v.get("username") else "")
        if not v.get("username"):
            if theirs is None:
                theirs = stockup_logins()
            if site in theirs:
                v, source = theirs[site], "StockUp"
                password = v.get("password", "")
        ftp = SITES[site]["ftp"]
        try:
            port = int(v.get("port") or ftp["port"])
        except ValueError:
            port = ftp["port"]
        sites.append({"name": site, "label": site_label(site, v),
                      "protocol": (v.get("protocol") or ftp["protocol"]).lower(),
                      "host": v.get("host") or ftp["host"], "port": port,
                      "username": v.get("username", ""), "password": password,
                      "remote_dir": v.get("remote_dir") or "/", "source": source})
    return sites


def save_login(site: str, username: str, password: str = "", remote_dir: str = "", clear: bool = False,
               host: str = "", port=None, protocol: str = "", label: str = ""):
    """Save one site's login (a blank password keeps the saved one). Passwords go in the keychain when there is
    one, otherwise in the logins file, which only you can read."""
    with _ini_lock:
        vals = _read_ini(logins_path())
        v = vals.setdefault(site, {})
        kr = _keyring()
        if clear:
            vals.pop(site, None)
            if kr:
                try:
                    kr.delete_password(KEYRING_SERVICE, site)
                except Exception:
                    pass
        else:
            v["username"] = (username or "").strip()
            if remote_dir.strip():
                v["remote_dir"] = remote_dir.strip()
            if host.strip():
                v["host"] = host.strip()
            if port:
                try:
                    v["port"] = str(int(port))
                except (TypeError, ValueError):
                    pass
            if protocol in ("ftp", "ftps", "sftp"):
                v["protocol"] = protocol
            if label.strip() and SITES[site].get("custom"):
                v["label"] = label.strip()
            if password:
                if kr:
                    kr.set_password(KEYRING_SERVICE, site, password)
                    v["password"] = ""
                else:
                    v["password"] = password
        _write_ini(vals)


# ---------- connections (from StockUp) -----------------------------------------------------------------------
class _TLS(ftplib.FTP_TLS):
    """FTP over TLS that reuses the login session for file transfers. Many servers (including stock agencies')
    refuse transfers without this, and plain ftplib doesn't do it."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


class FTPConn:
    """protocol ftps: always encrypted. protocol ftp: tries encryption first, and only falls back to plain FTP
    if the site doesn't offer it (and you allow that)."""

    def __init__(self, site, timeout=60, encrypted_only=False):
        self.encrypted = False
        tls_error = None
        if site["protocol"] in ("ftps", "ftp"):
            try:
                self.ftp = _TLS(timeout=timeout)
                self.ftp.connect(site["host"], site["port"])
                self.ftp.auth()
                self.ftp.login(site["username"], site["password"])
                self.ftp.prot_p()
                self.ftp.set_pasv(True)
                self._cd(site)
                self.ftp.nlst()                  # proves encrypted file transfers work, not just the login
                self.encrypted = True
            except ftplib.error_perm as e:
                if str(e).startswith("530"):     # wrong username or password
                    raise
                tls_error = e
            except (ftplib.Error, ssl.SSLError, OSError, EOFError) as e:
                tls_error = e
            if not self.encrypted:
                try:
                    self.ftp.close()
                except Exception:
                    pass
                if site["protocol"] == "ftps" or encrypted_only:
                    raise RuntimeError(f"{site['host']} wouldn't make an encrypted connection ({tls_error}).")
        if not self.encrypted:
            self.ftp = ftplib.FTP(timeout=timeout)
            self.ftp.connect(site["host"], site["port"])
            self.ftp.login(site["username"], site["password"])
            self.ftp.set_pasv(True)
            self._cd(site)

    def _cd(self, site):
        if site["remote_dir"] not in ("", "/"):
            self.ftp.cwd(site["remote_dir"])

    def listdir(self):
        try:
            return [n.rsplit("/", 1)[-1] for n in self.ftp.nlst()]
        except ftplib.error_perm:
            return []

    def delete(self, name):
        self.ftp.delete(name)

    def put(self, local, name, progress, handed_over=None):
        self.ftp.voidcmd("TYPE I")
        with open(local, "rb") as fh, self.ftp.transfercmd("STOR " + name) as conn:
            while True:
                buf = fh.read(256 * 1024)
                if not buf:
                    break
                conn.sendall(buf)
                progress(len(buf))
            if handed_over:
                handed_over()
            if isinstance(conn, ssl.SSLSocket):
                conn.unwrap()
        self.ftp.voidresp()
        try:
            size = self.ftp.size(name)
            if size is not None and size != os.path.getsize(local):
                raise IOError(f"size mismatch after upload ({size} vs {os.path.getsize(local)})")
        except ftplib.error_perm:
            pass

    def close(self):
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass


class SFTPConn:
    encrypted = True

    def __init__(self, site, timeout=60, encrypted_only=False):
        try:
            import paramiko
        except ImportError:
            raise RuntimeError("SFTP (Adobe Stock) needs paramiko: run  pip install paramiko")
        import socket
        sock = socket.create_connection((site["host"], site["port"]), timeout=timeout)
        self.t = paramiko.Transport(sock)
        self.t.banner_timeout = timeout
        self.t.auth_timeout = timeout
        self.t.connect(username=site["username"], password=site["password"])
        self.sftp = paramiko.SFTPClient.from_transport(self.t)
        self.sftp.get_channel().settimeout(timeout)
        if site["remote_dir"] not in ("", "/"):
            self.sftp.chdir(site["remote_dir"])

    def listdir(self):
        return self.sftp.listdir()

    def delete(self, name):
        self.sftp.remove(name)

    def put(self, local, name, progress, handed_over=None):
        last = [0]

        def cb(done, total):
            progress(done - last[0])
            last[0] = done
        self.sftp.put(local, name, callback=cb)

    def close(self):
        try:
            self.sftp.close()
            self.t.close()
        except Exception:
            pass


def connect(site: dict, timeout: int = 60, encrypted_only: bool = False):
    if not site["username"] or not site["password"]:
        raise RuntimeError("login not set yet: add it under Logins")
    if not site.get("host"):
        raise RuntimeError("no server yet: copy it from the agency's upload page into Logins")
    cls = SFTPConn if site["protocol"] == "sftp" else FTPConn
    return cls(site, timeout=timeout, encrypted_only=encrypted_only)


def login_for(site_name: str) -> dict | None:
    """The saved login for one site (IPTC Tagger's, else StockUp's), or None."""
    return next((s for s in load_sites() if s["name"] == site_name and s["username"] and s["password"]), None)


def send_csv(site_name: str, local: str, encrypted_only: bool = False, connect_fn=None) -> str:
    """Put a site's CSV in its FTP folder (Dreamstime reads its CSV from there, not from the website), under its
    own name, which already has the date and time in it so it never replaces an earlier one. Returns a message."""
    site = login_for(site_name)
    if site is None:
        raise RuntimeError(f"No {SITES[site_name]['label']} login yet: add it under Upload › Logins…")
    c = (connect_fn or connect)(site, timeout=60, encrypted_only=encrypted_only)
    try:
        c.put(local, os.path.basename(local), lambda n: None)
        enc = c.encrypted
    finally:
        c.close()
    return (f"Sent {os.path.basename(local)} to {SITES[site_name]['label']}"
            f"{' over an encrypted connection' if enc else ''}.")


class CsvAfterUpload(threading.Thread):
    """Send a site's CSV by itself once the site has taken the photos in. Dreamstime only fills in photos that are
    already in its Unfinished section, and it moves each photo out of the FTP folder once it has taken it in. So:
    look at the FTP folder now and then, and when none of the uploaded photos are left there, send the CSV.
    If they're still there after `longest` seconds, it's sent anyway. state is read by the window."""

    def __init__(self, site_name, csv_path, names, every=120, first_wait=60, longest=3600,
                 encrypted_only=False, connect_fn=None):
        super().__init__(daemon=True, name=f"csv-after-upload-{site_name}")
        self.site_name, self.csv_path, self.names = site_name, csv_path, set(names)
        self.every, self.first_wait, self.longest = every, first_wait, longest
        self.encrypted_only, self.connect_fn = encrypted_only, connect_fn
        self._halt = threading.Event()
        self.state = {"stage": "waiting", "left": len(self.names), "message": "", "checks": 0}

    def stop(self):
        self._halt.set()

    def _still_there(self) -> int | None:
        site = login_for(self.site_name)
        if site is None:
            return None
        c = (self.connect_fn or connect)(site, timeout=60, encrypted_only=self.encrypted_only)
        try:
            listing = set(c.listdir())
        finally:
            c.close()
        return len(self.names & listing)

    def run(self):
        start = time.monotonic()
        if self._halt.wait(self.first_wait):
            self.state.update(stage="stopped")
            return
        while True:
            try:
                left = self._still_there()
            except Exception as e:                   # a dropped connection: just look again next time
                left = self.state["left"]
                self.state["message"] = f"couldn't look at the FTP folder ({e}); trying again"
            self.state["checks"] += 1
            if left is None:
                self.state.update(stage="failed", message=f"No {SITES[self.site_name]['label']} login.")
                return
            self.state["left"] = left
            if left == 0 or time.monotonic() - start >= self.longest:
                break
            if self._halt.wait(self.every):
                self.state.update(stage="stopped")
                return
        try:
            msg = send_csv(self.site_name, self.csv_path, self.encrypted_only, self.connect_fn)
            self.state.update(stage="sent", message=msg + ("" if left == 0 else
                              f" ({left} photo{'s were' if left != 1 else ' was'} still waiting in the FTP folder "
                              "after an hour, so it was sent anyway.)"))
        except Exception as e:
            self.state.update(stage="failed", message=f"The {SITES[self.site_name]['label']} CSV couldn't be sent: {e}")


def test_login(site: dict, encrypted_only: bool = False) -> str:
    """'' when the login works, otherwise why not."""
    try:
        c = connect(site, timeout=15, encrypted_only=encrypted_only)
        enc = c.encrypted
        c.close()
        return "" if enc else "works (plain FTP: the site doesn't offer encryption)"
    except Exception as e:
        return str(e) or e.__class__.__name__


# ---------- what was sent ------------------------------------------------------------------------------------
_history_lock = threading.Lock()


def load_history() -> dict:
    """{site: {sha256: {"date", "file"}}}: every photo sent to each site, by its exact contents."""
    try:
        with open(history_path(), encoding="utf-8") as fh:
            h = json.load(fh)
        return h if isinstance(h, dict) else {}
    except (OSError, ValueError):
        return {}


def stockup_sent() -> dict:
    """What StockUp sent: {site: set(sha256)} (read only)."""
    out = {}
    for d in stockup_dirs():
        try:
            with open(os.path.join(d, "upload_history.json"), encoding="utf-8") as fh:
                h = json.load(fh)
        except (OSError, ValueError):
            continue
        for sha, sites in (h.get("sha") or {}).items():
            for site in sites or {}:
                out.setdefault(site, set()).add(sha)
    return out


def sent_before(site: str, sha: str, history=None, stockup=None) -> str:
    """When this exact file was sent to this site already ('' if never), by IPTC Tagger or StockUp."""
    history = load_history() if history is None else history
    hit = (history.get(site) or {}).get(sha)
    if hit:
        return hit.get("date", "") or "before"
    stockup = stockup_sent() if stockup is None else stockup
    return "before (by StockUp)" if sha in stockup.get(site, set()) else ""


def _record(site: str, sha: str, path: str):
    with _history_lock:
        h = load_history()
        h.setdefault(site, {})[sha] = {"date": datetime.now().strftime("%Y-%m-%d"), "file": path}
        os.makedirs(core.config_dir(), exist_ok=True)
        tmp = history_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(h, fh)
        os.replace(tmp, history_path())


# ---------- uploading ----------------------------------------------------------------------------------------
class Interrupted(Exception):
    """Raised inside a transfer when you press Pause or Stop."""


def plan(paths, site: str, history=None, stockup=None) -> dict:
    """What an upload to this site would do: {"send": [paths], "sent": [(path, when)], "left_out": [(path, why)]}."""
    out = {"send": [], "sent": [], "left_out": []}
    for p in paths:
        if os.path.splitext(p)[1].lower() not in UPLOAD_EXT:
            out["left_out"].append((p, "not a JPEG (export a JPEG to upload it)"))
        elif not os.path.isfile(p):
            out["left_out"].append((p, "the file isn't there any more"))
        else:
            when = sent_before(site, file_sha256(p), history, stockup)
            (out["sent"].append((p, when)) if when else out["send"].append(p))
    return out


class Uploader:
    """Sends photos to several sites at once, one thread per site. status() can be read from the window at any
    time; pause(), stop() take effect part-way through a photo."""

    def __init__(self, paths, sites, encrypted_only: bool = False, skip_sent: bool = True, connect_fn=None):
        self.paths = list(paths)
        self.sites = list(sites)
        self.encrypted_only = encrypted_only
        self.skip_sent = skip_sent
        self.connect = connect_fn or connect
        self.lock = threading.Lock()
        self.progress = {}
        self.running = self.paused = self.stop_requested = False

    # ---- records
    def _partials(self) -> dict:
        try:
            with open(partial_path(), encoding="utf-8") as fh:
                return {k: set(v) for k, v in json.load(fh).items()}
        except (OSError, ValueError):
            return {}

    def _set_partial(self, site, name, unfinished):
        with self.lock:
            p = self._partials()
            names = p.setdefault(site, set())
            (names.add if unfinished else names.discard)(name)
            os.makedirs(core.config_dir(), exist_ok=True)
            tmp = partial_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({k: sorted(v) for k, v in p.items() if v}, fh)
            os.replace(tmp, partial_path())

    def _mark_done(self, site, path, sent_as, sha, encrypted):
        _record(site, sha, path)
        with self.lock:
            new = not os.path.exists(receipt_path())
            with open(receipt_path(), "a", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["time", "site", "photo", "sent as", "bytes", "encrypted", "sha256"])
                w.writerow([datetime.now().isoformat(timespec="seconds"), self.progress[site]["label"], path, sent_as,
                            os.path.getsize(path), "yes" if encrypted else "no", sha])
        self._set_partial(site, sent_as, False)

    # ---- controls
    def status(self) -> dict:
        with self.lock:
            return {"running": self.running, "paused": self.paused and self.running,
                    "stopping": self.stop_requested and self.running,
                    "sites": json.loads(json.dumps(self.progress))}

    def stop(self):
        self.stop_requested = True
        self.paused = False

    def pause(self, on: bool):
        if self.running:
            self.paused = bool(on)

    def _check(self):
        if self.stop_requested or self.paused:
            raise Interrupted()

    def _set(self, site, **kw):
        with self.lock:
            self.progress[site].update(kw)

    def _err(self, site, msg):
        with self.lock:
            self.progress[site]["errors"].append(msg)

    def _note(self, site, msg):
        with self.lock:
            self.progress[site]["notes"].append(msg)

    # ---- running
    def start(self) -> bool:
        if self.running or not self.sites:
            return False
        self.stop_requested = self.paused = False
        history, theirs = load_history(), stockup_sent()
        self._todo = {}
        with self.lock:
            self.running = True
            self.progress = {}
            for s in self.sites:
                p = plan(self.paths, s["name"], history, theirs)
                todo = p["send"] if self.skip_sent else p["send"] + [x for x, _ in p["sent"]]
                self._todo[s["name"]] = todo
                self.progress[s["name"]] = {
                    "label": s.get("label") or SITES[s["name"]]["label"], "state": "waiting", "done": 0, "total": len(todo),
                    "bytes": 0, "total_bytes": sum(os.path.getsize(x) for x in todo) or 1, "current": "",
                    "errors": [], "notes": [], "skipped": len(p["sent"]) if self.skip_sent else 0,
                    "left_out": len(p["left_out"]), "encrypted": None}
        threads = [threading.Thread(target=self._run_site, args=(s,), daemon=True) for s in self.sites]
        for t in threads:
            t.start()

        def waiter():
            for t in threads:
                t.join()
            with self.lock:
                self.running = self.paused = False
        threading.Thread(target=waiter, daemon=True).start()
        return True

    def _remove_partial(self, site, name) -> bool:
        """After an interrupted transfer: delete the half-sent file so the site never gets half a photo."""
        label, last = site.get("label") or SITES[site["name"]]["label"], None
        for _ in range(2):
            try:
                c = self.connect(site, encrypted_only=self.encrypted_only)
                try:
                    if name in c.listdir():
                        c.delete(name)
                finally:
                    c.close()
                self._set_partial(site["name"], name, False)
                return True
            except Exception as e:
                last = e
                time.sleep(1)
        self._err(site["name"], f"{name}: couldn't remove the half-sent file from {label} ({last}). It will be "
                                f"replaced next time you upload, or delete it on {label}'s site.")
        return False

    def _wait_while_paused(self, name):
        if not self.paused:
            return
        self._set(name, state="paused", current="")
        while self.paused and not self.stop_requested:
            time.sleep(0.2)

    def _run_site(self, site):
        name = site["name"]
        label = site.get("label") or SITES[name]["label"]
        conn, failures_in_row, last, on_server = None, 0, "", set()
        ours = self._partials().get(name, set())      # half-sent earlier by IPTC Tagger: safe to replace

        def hang_up():
            nonlocal conn
            if conn:
                conn.close()
            conn = None

        for path in self._todo[name]:
            self._wait_while_paused(name)
            if self.stop_requested:
                hang_up()
                self._set(name, state="stopped", current="")
                return
            remote = remote_name(name, path)
            sha = file_sha256(path)
            ok, skipped, attempt = False, False, 0
            while attempt < 3:
                start_bytes = self.progress[name]["bytes"]
                started = False
                try:
                    self._check()
                    if conn is None:
                        self._set(name, state="connecting")
                        conn = self.connect(site, encrypted_only=self.encrypted_only)
                        on_server = set(conn.listdir())
                        self._set(name, encrypted=conn.encrypted)
                    if remote in on_server and remote not in ours:
                        self._note(name, f"{remote} is already on the {label} server, so it wasn't sent again "
                                         "or overwritten.")
                        skipped = True
                        break
                    self._set(name, state="uploading", current=os.path.basename(path))

                    def prog(n, _name=name):
                        with self.lock:
                            self.progress[_name]["bytes"] += n
                        self._check()                    # Pause / Stop take effect mid-photo
                    self._set_partial(name, remote, True)
                    ours.add(remote)
                    started = True
                    conn.put(path, remote, prog, lambda _name=name: self._set(_name, state="finishing"))
                    on_server.add(remote)
                    ok = True
                    break
                except Interrupted:
                    self._set(name, bytes=start_bytes)
                    hang_up()
                    if started:
                        self._set(name, state="tidying up", current=os.path.basename(path))
                        if self._remove_partial(site, remote):
                            ours.discard(remote)
                            self._note(name, f"{remote}: interrupted part-way; the half-sent file was removed "
                                             "from the server.")
                    if self.stop_requested:
                        self._set(name, state="stopped", current="")
                        return
                    self._wait_while_paused(name)       # then send this photo again from the start
                    if self.stop_requested:
                        self._set(name, state="stopped", current="")
                        return
                    continue
                except Exception as e:
                    self._set(name, bytes=start_bytes)
                    hang_up()
                    last = str(e) or e.__class__.__name__
                    low = last.lower()
                    if "530" in last or "login" in low or "auth" in low or "paramiko" in low \
                            or "encrypted connection" in low:
                        self._set(name, state="error", current="")
                        self._err(name, f"Couldn't connect: {last}")
                        return
                    attempt += 1
                    time.sleep(min(2 * attempt, 4) if not os.environ.get("IPTC_TAGGER_OFFLINE") else 0.05)
            if ok:
                failures_in_row = 0
                self._mark_done(name, path, remote, sha, conn.encrypted if conn else False)
                ours.discard(remote)
                with self.lock:
                    self.progress[name]["done"] += 1
            elif skipped:
                with self.lock:
                    self.progress[name]["skipped"] += 1
            else:
                failures_in_row += 1
                self._err(name, f"{os.path.basename(path)}: {last}")
                if remote in self._partials().get(name, set()):  # never leave half a photo behind
                    self._remove_partial(site, remote)
                if failures_in_row >= 5:
                    self._set(name, state="error", current="")
                    self._err(name, "Stopped after 5 failures in a row.")
                    hang_up()
                    return
        hang_up()
        self._set(name, state="finished with errors" if self.progress[name]["errors"] else "finished",
                  current="")
