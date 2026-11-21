"""Core logic: link handling, yt-dlp fetch/download tasks, library storage. No UI code lives here."""

import ctypes
import json
import os
import re
import subprocess
import sys
import urllib.request
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from accounts import ACCOUNTS, PLATFORM_CFG
from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtGui import QImage

APP_NAME = "video-downloader"
VERSION = "3.0"
DATA_DIR = Path(os.getenv("APPDATA") or Path.home()) / "VideoDownloader"
THUMB_DIR = DATA_DIR / "thumbs"
LIB_FILE = DATA_DIR / "library.json"
CFG_FILE = DATA_DIR / "config.json"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

PLATFORM_HOSTS = {"twitter.com": "X", "x.com": "X", "fxtwitter.com": "X", "vxtwitter.com": "X", "fixupx.com": "X",
                  "tiktok.com": "TikTok", "instagram.com": "Instagram", "instagr.am": "Instagram"}
# Mirror / short hosts that yt-dlp does not handle itself are rewritten to the real site.
REWRITE_HOSTS = {"fxtwitter.com": "x.com", "vxtwitter.com": "x.com", "fixupx.com": "x.com", "instagr.am": "instagram.com"}
PLATFORM_TAG = {"X": "X", "TikTok": "TIKTOK", "Instagram": "INSTAGRAM"}
URL_RE = re.compile(r"https?://[^\s<>\"']+")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ----------------------------------------------------------------------------- helpers
def platform_of(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for h, name in PLATFORM_HOSTS.items():
        if host == h or host.endswith("." + h):
            return name
    return None


def normalize_url(u: str) -> str:
    p = urlparse(u)
    host = (p.hostname or "").lower()
    for src, dst in REWRITE_HOSTS.items():
        if host == src or host.endswith("." + src):
            return p._replace(netloc=dst).geturl()
    return u


def extract_url(text: str) -> str | None:
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(").,;!?\"'")
        if platform_of(u):
            return normalize_url(u)
    return None


def fmt_duration(sec) -> str:
    if not sec:
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(n) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_count(n) -> str:
    if n is None:
        return ""
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= div:
            return f"{n / div:.1f}{suf}".replace(".0", "")
    return str(n)


def fmt_upload_date(s) -> str:
    try:
        return datetime.strptime(str(s), "%Y%m%d").strftime("%Y-%m-%d")
    except Exception:
        return ""


def friendly_error(err: str, ctx=None) -> str:
    e = ANSI_RE.sub("", err).lower()
    if "no video" in e or "no media" in e or "there is no video" in e or "photo" in e or "slideshow" in e:
        return ("that post has no video. photo posts can be saved from X; instagram and tiktok photo "
                "posts and slideshows aren't supported yet.")
    if "unexpected response from webpage" in e or "impersonat" in e or "challenge" in e or "captcha" in e:
        return ("the site blocked the request with an anti-bot check. wait a moment and press RETRY. "
                "if it keeps happening, rebuild the app (build.bat) to refresh its downloader.")
    if "login" in e or "log in" in e or "sign in" in e or "private" in e or "cookies" in e or "rate-limit" in e:
        plat, user = ctx if ctx else (None, None)
        if plat and user is not None:  # an account is connected but still can't see it
            who = f"@{user}" if user else "your signed-in account"
            return (f"the connected {plat} account ({who}) can't open this post. it may be private to people that account "
                    "isn't allowed to see, the session may have expired (check ACCOUNTS), or the platform doesn't offer "
                    "this video for download. the app never bypasses privacy settings.")
        if plat:
            return (f"this post looks private, age-restricted or needs a sign-in. open ACCOUNTS and connect an account on "
                    f"{plat} that is allowed to see it, then try again. the app can only save what that account itself can view.")
        return "this post is private or needs a sign-in, so it can't be downloaded."
    if "drm" in e:
        return "this video is DRM-protected and can't be downloaded."
    if any(k in e for k in ("getaddrinfo", "urlopen", "timed out", "unable to download webpage", "connection")):
        return "network problem. check your internet connection and try again."
    if "unsupported url" in e:
        return "that link isn't supported."
    if "not available" in e or "404" in e or "removed" in e or "deleted" in e:
        return "this video isn't available anymore. it may have been deleted."
    detail = " ".join(ANSI_RE.sub("", err).replace("ERROR:", "").split())[:150]
    return "couldn't read that video. the link may be wrong or the post was removed." + (f" [{detail}]" if detail else "")


def ffmpeg_path() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def move_to_recycle_bin(path: str) -> bool:
    """Send a file to the Windows Recycle Bin (recoverable). Falls back to plain delete."""
    try:
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        op = SHFILEOPSTRUCTW()
        op.wFunc = 3  # FO_DELETE
        op.pFrom = str(Path(path)) + "\0"
        op.fFlags = 0x40 | 0x10 | 0x4 | 0x400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted:
            return not Path(path).exists()
    except Exception:
        pass
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def make_thumb(video_path: str, entry_id: str, fallback: bytes | None, audio: bool) -> str | None:
    """Create a small JPEG thumbnail for a downloaded file. Returns its path."""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    out = THUMB_DIR / f"{entry_id}.jpg"
    ff = ffmpeg_path()
    if ff and not audio:
        for ss in ("1", "0"):
            try:
                subprocess.run([ff, "-y", "-ss", ss, "-i", video_path, "-frames:v", "1", "-vf", "scale=480:-2",
                                "-q:v", "4", str(out)], capture_output=True, timeout=40, creationflags=NO_WINDOW)
                if out.exists() and out.stat().st_size > 0:
                    return str(out)
            except Exception:
                pass
    if fallback:
        img = QImage.fromData(fallback)
        if not img.isNull():
            img.scaledToWidth(480, Qt.SmoothTransformation).save(str(out), "JPG", 85)
            if out.exists():
                return str(out)
    return None


def make_image_thumb(image_path: str, entry_id: str) -> str | None:
    """Small JPEG thumbnail for a saved photo."""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    img = QImage(image_path)
    if img.isNull():
        return None
    out = THUMB_DIR / f"{entry_id}.jpg"
    img.scaledToWidth(480, Qt.SmoothTransformation).save(str(out), "JPG", 85)
    return str(out) if out.exists() else None


def open_folder_of(path: str) -> None:
    p = Path(path)
    try:
        if p.is_dir():
            os.startfile(p)  # noqa: S606
        elif p.exists():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(p.parent if p.parent.exists() else DATA_DIR)  # noqa: S606
    except Exception:
        pass


# ----------------------------------------------------------------------------- background tasks
class Cancelled(Exception):
    pass


class Signals(QObject):
    info = Signal(int, dict)
    info_err = Signal(int, str)
    progress = Signal(int, float, str)
    done = Signal(int, dict)
    fail = Signal(int, str)


class QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


class FileLogger:
    """Opt-in diagnostics: set the VD_DEBUG_LOG environment variable to a file path to get yt-dlp's verbose log."""

    def __init__(self, path: str):
        self.path = path

    def _w(self, msg) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(str(msg) + "\n")
        except OSError:
            pass

    debug = info = warning = error = _w


def base_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "socket_timeout": 25, "retries": 3, "logger": QuietLogger(), "windowsfilenames": True}
    if os.environ.get("VD_DEBUG_LOG"):
        opts["logger"] = FileLogger(os.environ["VD_DEBUG_LOG"])
        opts["verbose"] = True
    ff = ffmpeg_path()
    if ff:
        opts["ffmpeg_location"] = ff
    return opts


def first_entry(info):
    if info and info.get("entries"):
        return next((e for e in info["entries"] if e), None)
    return info


def account_ctx(plat: str | None):
    """(platform label, connected account name or None) - used to word login-related errors."""
    if not plat:
        return None
    return (PLATFORM_CFG.get(plat, {}).get("label", plat), ACCOUNTS.session_user(plat))


def run_ytdlp(plat: str | None, make_opts, work):
    """Run `work(ydl)` with the platform's connected account (if any).

    The account's cookies live in a temporary file for the duration of the call. If a signed-in attempt
    fails but an anonymous one succeeds, the session is probably stale: public videos keep working and the
    account is flagged so the user is told to reconnect.
    """
    def attempt(cookie_path):
        opts = make_opts()
        if cookie_path:
            opts["cookiefile"] = cookie_path
        with yt_dlp.YoutubeDL(opts) as ydl:
            return work(ydl)

    if plat and ACCOUNTS.has_session(plat):
        try:
            with ACCOUNTS.cookie_file(plat) as cf:
                return attempt(cf)
        except Cancelled:
            raise
        except Exception as first:  # noqa: BLE001
            try:
                result = attempt(None)
            except Exception as anon:  # noqa: BLE001
                if any(k in str(anon).lower() for k in ("no video", "no media", "photo", "slideshow")):
                    # Anonymous access *did* reach the post (it just has no video): the saved session is what's broken.
                    # Keep the anonymous answer so callers can still handle e.g. photo posts.
                    ACCOUNTS.mark(plat, "attention")
                    raise anon
                raise first  # otherwise the signed-in error is the more informative one
            ACCOUNTS.mark(plat, "attention")
            return result
    return attempt(None)


X_ID_RE = re.compile(r"/status(?:es)?/(\d+)")
IMAGE_EXTS = ("jpg", "jpeg", "png", "webp")


class PostHidden(Exception):
    """The platform returned nothing for this post to the current (signed-out) session."""


def x_status(ydl, url: str) -> dict:
    """The post record for an X link, read through yt-dlp's maintained X access.

    Uses the connected account's session when one is set up, so posts that account may see are readable.
    """
    m = X_ID_RE.search(url)
    if not m:
        return {}
    ie = ydl.get_info_extractor("Twitter")
    return ie._extract_status(m.group(1)) or {}


def x_photos(status: dict) -> list[dict]:
    out = []
    for m in (status.get("extended_entities") or {}).get("media") or []:
        url = m.get("media_url_https") or m.get("media_url")
        if m.get("type") == "photo" and url:
            oi = m.get("original_info") or {}
            out.append({"url": url, "w": oi.get("width"), "h": oi.get("height")})
    return out


def photo_candidates(im: dict) -> list[str]:
    """Original resolution first, then the plain URL as a fallback."""
    return [im["url"] + ":orig", im["url"]]


def photo_ext(url: str) -> str:
    ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
    return ext if ext in IMAGE_EXTS else "jpg"


def http_get(url: str, timeout: int = 30) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout).read()


class FetchTask(QRunnable):
    def __init__(self, token: int, url: str):
        super().__init__()
        self.setAutoDelete(False)
        self.token, self.url = token, url
        self.s = Signals()

    def run(self):
        plat = platform_of(self.url)
        try:
            info = first_entry(run_ytdlp(plat, base_opts, lambda ydl: ydl.extract_info(self.url, download=False)))
            if not info:
                raise RuntimeError("no video")
            dur = info.get("duration")
            heights: dict[int, dict] = {}
            for f in info.get("formats") or []:
                h = f.get("height")
                if h and f.get("vcodec") not in (None, "none"):
                    size = f.get("filesize") or f.get("filesize_approx")
                    if not size and f.get("tbr") and dur:
                        size = f["tbr"] * 1000 / 8 * dur
                    cur = heights.setdefault(h, {"w": f.get("width"), "size": 0})
                    cur["size"] = max(cur["size"] or 0, size or 0)
            if not heights and info.get("height"):
                heights[info["height"]] = {"w": info.get("width"), "size": info.get("filesize") or 0}
            thumb = None
            turl = info.get("thumbnail") or ((info.get("thumbnails") or [{}])[-1].get("url"))
            if turl:
                try:
                    req = urllib.request.Request(turl, headers={"User-Agent": UA})
                    thumb = urllib.request.urlopen(req, timeout=15).read()
                except Exception:
                    thumb = None
            title = (info.get("title") or "").strip()
            desc = (info.get("description") or "").strip()
            summary = {
                "url": self.url, "platform": platform_of(self.url), "id": info.get("id"),
                "title": title or "Untitled video", "description": desc if desc and desc != title else "",
                "uploader": info.get("uploader") or info.get("uploader_id") or info.get("channel") or "",
                "duration": dur, "views": info.get("view_count"), "likes": info.get("like_count"),
                "date": fmt_upload_date(info.get("upload_date")), "thumb": thumb,
                "heights": sorted(((h, v) for h, v in heights.items()), reverse=True),
            }
            self.s.info.emit(self.token, summary)
        except Exception as e:  # noqa: BLE001
            if plat == "X" and "no video" in str(e).lower():
                try:  # no video: it may be a photo post
                    self.s.info.emit(self.token, self._photo_summary(plat))
                    return
                except Exception as e2:  # noqa: BLE001
                    e = e2
            self.s.info_err.emit(self.token, friendly_error(str(e), account_ctx(plat)))

    def _photo_summary(self, plat: str) -> dict:
        st = run_ytdlp(plat, base_opts, lambda ydl: x_status(ydl, self.url))
        photos = x_photos(st)
        text = (st.get("full_text") or st.get("text") or "").strip()
        user = st.get("user") or {}
        if not photos:
            if not user and not text:  # X gave nothing back: hidden from this (signed-out) session
                raise PostHidden("this post is hidden from signed-out visitors: sign in required")
            raise RuntimeError("no video")  # a text-only post
        clean = re.sub(r"\s*https?://t\.co/\S+", "", text).strip()
        title = " ".join(clean.split())[:160] or "Photo post"
        thumb = None
        try:
            thumb = http_get(photos[0]["url"] + ":medium", 20)
        except Exception:  # noqa: BLE001
            try:
                thumb = http_get(photos[0]["url"], 20)
            except Exception:  # noqa: BLE001
                thumb = None
        date = ""
        try:
            date = datetime.strptime(st.get("created_at", ""), "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            pass
        m = X_ID_RE.search(self.url)
        return {"url": self.url, "platform": plat, "id": m.group(1) if m else "post", "kind": "images", "images": photos,
                "title": title, "description": clean if clean and " ".join(clean.split()) != title else "",
                "uploader": user.get("screen_name") or user.get("name") or "", "duration": None, "views": None,
                "likes": st.get("favorite_count"), "date": date, "thumb": thumb, "heights": []}


class DownloadTask(QRunnable):
    def __init__(self, token: int, summary: dict, out_dir: str, height: int | None, audio: bool, tag: str = "",
                 compat: bool = True):
        super().__init__()
        self.setAutoDelete(False)
        self.token, self.summary, self.out_dir, self.height, self.audio = token, summary, out_dir, height, audio
        self.compat = compat and not audio  # re-encode HEVC/AV1/VP9 to H.264 so it plays everywhere
        # Distinct file name per variant so MP3 / different qualities / codecs never overwrite each other.
        if audio:
            self.tag = " (audio)"
        else:
            self.tag = (f" [{tag}]" if tag else "") + ("" if compat else " (original)")
        self.cancelled = False
        self.s = Signals()
        self.stream_idx = 0

    def cancel(self):
        self.cancelled = True

    def _ensure_h264(self, path: str) -> str:
        """If the video isn't H.264 (TikTok often serves HEVC), re-encode it so it plays on any PC."""
        ff = ffmpeg_path()
        if not ff:
            return path
        probe = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True,
                               creationflags=NO_WINDOW)
        m = re.search(r"Video: (\w+)", probe.stderr or "")
        if not m or m.group(1).lower() in ("h264", "avc1"):
            return path
        dur = self.summary.get("duration") or 0
        src = Path(path)
        tmp = src.with_name(src.stem + ".h264tmp.mp4")
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy",
               "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(tmp)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                creationflags=NO_WINDOW)
        try:
            for line in proc.stdout:
                if self.cancelled:
                    proc.kill()
                    raise Cancelled()
                if line.startswith("out_time_ms=") and dur:
                    try:
                        frac = min(int(line.split("=")[1]) / 1_000_000 / dur, 1.0)
                    except ValueError:
                        continue
                    self.s.progress.emit(self.token, min(frac, 0.99), "converting to h.264 so it plays everywhere")
            proc.wait()
            if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
                return path  # keep the original file rather than failing the download
            final = src.with_suffix(".mp4")
            os.replace(tmp, final)
            if final != src:
                try:
                    src.unlink()
                except OSError:
                    pass
            return str(final)
        finally:
            if proc.poll() is None:
                proc.kill()
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _run_images(self) -> dict:
        """Save a photo post: one file for a single photo, a folder (01.jpg, 02.jpg, ...) for several."""
        s = self.summary
        imgs = s["images"]
        out = Path(self.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        base = yt_dlp.utils.sanitize_filename(f"{s.get('uploader') or 'photo'} - {s.get('id')}", restricted=False)[:120]
        n = len(imgs)
        folder = out / base
        if n > 1:
            folder.mkdir(exist_ok=True)
        files: list[str] = []
        for i, im in enumerate(imgs, 1):
            if self.cancelled:
                raise Cancelled()
            data, last = None, None
            for u in photo_candidates(im):
                for _ in range(2):
                    try:
                        data = http_get(u)
                        break
                    except Exception as e:  # noqa: BLE001
                        last = e
                if data:
                    break
            if not data:
                raise RuntimeError(f"couldn't download photo {i} of {n} ({last})")
            ext = photo_ext(im["url"])
            f = (out / f"{base}.{ext}") if n == 1 else (folder / f"{i:02d}.{ext}")
            f.write_bytes(data)
            files.append(str(f))
            self.s.progress.emit(self.token, min(i / n, 0.99), f"photo {i} of {n}")
        path = files[0] if n == 1 else str(folder)
        eid = uuid.uuid4().hex[:12]
        return {"id": eid, "title": s["title"], "platform": s["platform"], "url": s["url"], "path": path,
                "filename": Path(path).name, "date": datetime.now().isoformat(timespec="seconds"), "duration": None,
                "size": sum(os.path.getsize(f) for f in files), "uploader": s["uploader"], "kind": "image",
                "images": n, "files": files, "thumb": make_image_thumb(files[0], eid)}

    def run(self):
        try:
            if self.summary.get("kind") == "images":
                self.s.done.emit(self.token, self._run_images())
                return
            out = Path(self.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            opts = base_opts()
            opts["outtmpl"] = str(out / ("%(uploader|video)s - %(id)s" + self.tag.replace("%", "%%") + ".%(ext)s"))
            opts["trim_file_name"] = 120
            if self.audio:
                opts["format"] = "ba/b"
                opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
            else:
                h = f"[height<={self.height}]" if self.height else ""
                opts["format"] = (f"bv*{h}[ext=mp4]+ba[ext=m4a]/b{h}[ext=mp4]/bv*{h}+ba/b{h}/b")
                opts["merge_output_format"] = "mp4"

            def hook(d):
                if self.cancelled:
                    raise Cancelled()
                if d["status"] == "downloading":
                    tot = d.get("total_bytes") or d.get("total_bytes_estimate")
                    frac = (d.get("downloaded_bytes") or 0) / tot if tot else 0
                    n = len((d.get("info_dict") or {}).get("requested_formats") or [1])
                    overall = min((self.stream_idx + frac) / n, 0.99)
                    parts = []
                    if d.get("speed"):
                        parts.append(f"{fmt_size(d['speed'])}/s")
                    if d.get("eta") is not None:
                        parts.append(f"eta {fmt_duration(d['eta']) or '0:00'}")
                    self.s.progress.emit(self.token, overall, "   ".join(parts))
                elif d["status"] == "finished":
                    self.stream_idx += 1

            def pp_hook(d):
                if d["status"] == "started":
                    self.s.progress.emit(self.token, 0.99, "finishing up")

            opts["progress_hooks"] = [hook]
            opts["postprocessor_hooks"] = [pp_hook]
            def work(ydl):
                self.stream_idx = 0
                info = first_entry(ydl.extract_info(self.summary["url"], download=True))
                rd = (info or {}).get("requested_downloads")
                return rd[0].get("filepath") if rd else ydl.prepare_filename(info)

            path = run_ytdlp(platform_of(self.summary["url"]), lambda: dict(opts), work)
            if not path or not Path(path).exists():
                cands = sorted(out.glob(f"*{self.summary.get('id')}*"), key=lambda p: p.stat().st_mtime, reverse=True)
                cands = [c for c in cands if c.suffix not in (".part", ".ytdl")]
                path = str(cands[0]) if cands else None
            if not path:
                raise RuntimeError("download finished but the file wasn't found.")
            if self.compat:
                path = self._ensure_h264(str(path))
            eid = uuid.uuid4().hex[:12]
            entry = {
                "id": eid, "title": self.summary["title"], "platform": self.summary["platform"],
                "url": self.summary["url"], "path": str(path), "filename": Path(path).name,
                "date": datetime.now().isoformat(timespec="seconds"), "duration": self.summary["duration"],
                "size": Path(path).stat().st_size, "uploader": self.summary["uploader"],
                "kind": "audio" if self.audio else "video",
                "thumb": make_thumb(str(path), eid, self.summary.get("thumb"), self.audio),
            }
            self.s.done.emit(self.token, entry)
        except Cancelled:
            self.s.fail.emit(self.token, "download cancelled.")
        except Exception as e:  # noqa: BLE001
            self.s.fail.emit(self.token, friendly_error(str(e), account_ctx(platform_of(self.summary["url"])))
                             if isinstance(e, yt_dlp.utils.YoutubeDLError) else str(e))


# ----------------------------------------------------------------------------- library
class Library:
    def __init__(self):
        data = load_json(LIB_FILE, [])
        self.items: list[dict] = [d for d in data if isinstance(d, dict) and d.get("path") and d.get("id")]

    def add(self, e: dict) -> None:
        self.items.insert(0, e)
        save_json(LIB_FILE, self.items)

    def remove(self, e: dict) -> None:
        self.items = [x for x in self.items if x["id"] != e["id"]]
        save_json(LIB_FILE, self.items)
        if e.get("thumb"):
            try:
                os.remove(e["thumb"])
            except OSError:
                pass

    def set_hidden(self, entry_id: str, hidden: bool) -> bool:
        """Hide/unhide a library entry. Only the library record changes: the video file is never touched."""
        for x in self.items:
            if x["id"] == entry_id:
                x["hidden"] = bool(hidden)
                save_json(LIB_FILE, self.items)
                return True
        return False
