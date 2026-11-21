"""Connected accounts: an encrypted session vault and the cookie plumbing yt-dlp needs.

Design (read this before changing anything):
  * Sign-in happens on each platform's own website inside an embedded browser (see login_dialog.py).
    This app never sees, receives or stores a password.
  * After sign-in only the platform's *session cookies* are kept. They are encrypted with the
    Windows Data Protection API (DPAPI), so they can only be decrypted by the same Windows user on
    this PC. Nothing is stored in plain text.
  * When a link is processed, the matching account's cookies are written to a temporary cookie file,
    handed to yt-dlp, and deleted straight afterwards.
  * Disconnecting deletes the stored session. The app never bypasses privacy settings: it can only
    see what that signed-in account is itself allowed to see.
"""

import base64
import ctypes
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime
from http.cookiejar import MozillaCookieJar
from pathlib import Path

DATA_DIR = Path(os.getenv("APPDATA") or Path.home()) / "VideoDownloader"
VAULT_FILE = DATA_DIR / "vault.json"
_ENTROPY = b"VideoDownloader.vault.v1"
SESSION_FAR = 4102444800  # 2100-01-01: marks cookies that had no expiry (session cookies) in the cookie file

# ---------------------------------------------------------------------------- per-platform settings
# username_js runs on the platform's home page after sign-in and should return the account name.
# It is best effort: if a site changes its page, the account still connects (shown without a name).
PLATFORM_CFG: dict[str, dict] = {
    "TikTok": {
        "label": "TikTok",
        "login_url": "https://www.tiktok.com/login",
        "home_url": "https://www.tiktok.com/",
        "domains": ("tiktok.com",),
        "session_cookies": ("sessionid", "sessionid_ss", "sid_tt"),
        "username_js": """(function(){try{
            var el=document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__'); if(!el) return '';
            var j=JSON.parse(el.textContent); var d=j['__DEFAULT_SCOPE__']||{};
            var u=(d['webapp.app-context']||{}).user||{}; return u.uniqueId||'';
        }catch(e){return ''}})()""",
    },
    "Instagram": {
        "label": "Instagram",
        "login_url": "https://www.instagram.com/accounts/login/",
        "home_url": "https://www.instagram.com/",
        "domains": ("instagram.com",),
        "session_cookies": ("sessionid",),
        "username_js": """(function(){try{
            var x=new XMLHttpRequest(); x.open('GET','/api/v1/accounts/current_user/?edit=true',false);
            x.setRequestHeader('X-IG-App-ID','936619743392459'); x.setRequestHeader('X-Requested-With','XMLHttpRequest');
            x.send(); if(x.status!==200) return ''; var j=JSON.parse(x.responseText);
            return (j.user&&j.user.username)||'';
        }catch(e){return ''}})()""",
    },
    "X": {
        "label": "X (Twitter)",
        "login_url": "https://x.com/i/flow/login",
        "home_url": "https://x.com/home",
        "domains": ("x.com", "twitter.com"),
        "session_cookies": ("auth_token",),
        "username_js": """(function(){try{
            var a=document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
            if(!a) return ''; return (a.getAttribute('href')||'').replace(/^\\//,'').split('/')[0];
        }catch(e){return ''}})()""",
    },
}


# ---------------------------------------------------------------------------- Windows DPAPI
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p)), buf


def _dpapi():
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    ptr = ctypes.POINTER(_BLOB)
    crypt32.CryptProtectData.argtypes = [ptr, wintypes.LPCWSTR, ptr, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ptr]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ptr, ctypes.c_void_p, ptr, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ptr]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    return crypt32, kernel32


def protect(data: bytes) -> bytes:
    """Encrypt for the current Windows user (DPAPI)."""
    crypt32, kernel32 = _dpapi()
    inb, k1 = _blob(data)
    ent, k2 = _blob(_ENTROPY)
    out = _BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(inb), "VideoDownloader session", ctypes.byref(ent), None, None, 0,
                                    ctypes.byref(out)):
        raise OSError("Windows could not encrypt the session")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def unprotect(data: bytes) -> bytes:
    crypt32, kernel32 = _dpapi()
    inb, k1 = _blob(data)
    ent, k2 = _blob(_ENTROPY)
    out = _BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(inb), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not decrypt the session (different user or PC?)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# ---------------------------------------------------------------------------- cookie files for yt-dlp
def write_netscape(path: str, cookies: list[dict]) -> None:
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        dom = c["domain"]
        exp = int(c.get("expires") or 0) or SESSION_FAR
        name = str(c["name"]).replace("\t", "").replace("\n", "")
        value = str(c["value"]).replace("\t", "").replace("\n", "").replace("\r", "")
        lines.append("\t".join([("#HttpOnly_" if c.get("httponly") else "") + dom,
                                "TRUE" if dom.startswith(".") else "FALSE", c.get("path") or "/",
                                "TRUE" if c.get("secure") else "FALSE", str(exp), name, value]))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_netscape(path: str) -> list[dict]:
    jar = MozillaCookieJar(path)
    jar.load(ignore_discard=True, ignore_expires=True)
    out = []
    for c in jar:
        exp = c.expires or 0
        out.append({"domain": c.domain, "path": c.path, "name": c.name, "value": c.value or "",
                    "expires": 0 if exp == SESSION_FAR else int(exp), "secure": bool(c.secure),
                    "httponly": bool(c.has_nonstandard_attr("HttpOnly"))})
    return out


def _session_expiry(platform: str, cookies: list[dict]) -> int:
    """Earliest expiry (epoch seconds) among the platform's session cookies; 0 = no expiry known."""
    names = PLATFORM_CFG.get(platform, {}).get("session_cookies", ())
    exps = [int(c["expires"]) for c in cookies if c["name"] in names and int(c.get("expires") or 0) > 0]
    return min(exps) if exps else 0


# ---------------------------------------------------------------------------- the vault
class AccountStore:
    """Encrypted, per-platform session storage. Thread safe."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else VAULT_FILE
        self._lock = threading.RLock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- queries
    def cookies(self, platform: str) -> list[dict] | None:
        with self._lock:
            rec = self._data.get(platform)
            if not rec or not rec.get("blob"):
                return None
            try:
                return json.loads(unprotect(base64.b64decode(rec["blob"])).decode("utf-8"))
            except Exception:
                return None

    def has_session(self, platform: str) -> bool:
        return self.state(platform) in ("connected", "attention")

    def session_user(self, platform: str) -> str | None:
        """Account name ('' if unknown) when a usable session exists, otherwise None."""
        if not self.has_session(platform):
            return None
        return self._data[platform].get("username", "")

    def state(self, platform: str) -> str:
        """none | connected | attention (session may be stale) | expired | unreadable"""
        with self._lock:
            rec = self._data.get(platform)
            if not rec or not rec.get("blob"):
                return "none"
            exp = int(rec.get("exp") or 0)
            if exp and exp < time.time():
                return "expired"
            if self.cookies(platform) is None:
                return "unreadable"  # e.g. vault copied from another Windows user: can't be decrypted here
            return "attention" if rec.get("state") == "attention" else "connected"

    def info(self, platform: str) -> dict:
        with self._lock:
            rec = self._data.get(platform) or {}
            return {"state": self.state(platform), "username": rec.get("username", ""), "saved": rec.get("saved", "")}

    # -- changes
    def save(self, platform: str, cookies: list[dict], username: str = "") -> None:
        with self._lock:
            blob = base64.b64encode(protect(json.dumps(cookies).encode("utf-8"))).decode("ascii")
            self._data[platform] = {"username": username.lstrip("@"), "saved": datetime.now().isoformat(timespec="seconds"),
                                    "blob": blob, "exp": _session_expiry(platform, cookies), "state": "ok"}
            self._save()

    def remove(self, platform: str) -> None:
        with self._lock:
            self._data.pop(platform, None)
            self._save()

    def mark(self, platform: str, state: str) -> None:
        with self._lock:
            rec = self._data.get(platform)
            if rec and rec.get("state") != state:
                rec["state"] = state
                self._save()

    @contextmanager
    def cookie_file(self, platform: str):
        """Yield the path of a temporary cookie file for this platform's session (or None if not connected).

        The file is deleted on exit. Cookies refreshed by the site during use are saved back encrypted.
        """
        cookies = self.cookies(platform) if self.has_session(platform) else None
        if not cookies:
            yield None
            return
        tmpdir = tempfile.mkdtemp(prefix="vdc_")
        path = os.path.join(tmpdir, "cookies.txt")
        try:
            write_netscape(path, cookies)
            yield path
            self._merge_back(platform, path, cookies)
            self.mark(platform, "ok")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _merge_back(self, platform: str, path: str, original: list[dict]) -> None:
        try:
            updated = read_netscape(path)
        except Exception:
            return
        key = lambda c: (c["domain"].lstrip("."), c["path"], c["name"])  # noqa: E731
        old = {key(c): c for c in original}
        merged, changed = [], False
        for c in updated:
            o = old.get(key(c))
            if o and o["value"] == c["value"]:
                merged.append(o)
            else:
                merged.append(c)
                changed = True
        if changed and merged:
            with self._lock:
                rec = self._data.get(platform)
                if rec:
                    self.save(platform, merged, rec.get("username", ""))


ACCOUNTS = AccountStore()
