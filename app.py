"""video-downloader - preview, download and organise videos from X (Twitter), TikTok and Instagram.

For videos you are authorized to save (your own content, or content you have permission to
download). Only publicly accessible videos are supported; no DRM or login bypassing.
"""

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
from PySide6.QtCore import (QCoreApplication, QEvent, QObject, QPoint, QPropertyAnimation, QRect, QRunnable, QSize, Qt,
                            QThreadPool, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QIcon,
                           QImage, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap, QTextLayout,
                           QTextOption)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame,
                               QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSlider,
                               QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

APP_NAME = "video-downloader"
VERSION = "2.2"
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

# Design tokens: neutral charcoal with a faint green cast, one phosphor-green accent (never pure black).
BG, PANEL, PANEL_HI = "#0c0e0d", "#111413", "#171b19"
BORDER, BORDER_HI = "#242a27", "#35403a"
TEXT, MUTED, DIM = "#d5dbd7", "#707a74", "#454d49"
ACCENT, ERR = "#66d19a", "#e0786c"
MONO = "Consolas"

# Layout system (8px rhythm): every control is CONTROL_H tall; sections are GAP apart.
CONTROL_H, GAP, MARGIN, PAD = 36, 16, 28, 16


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
        return "that post doesn't contain a video (photo and slideshow posts aren't supported)."
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
            return (f"this post looks private or needs a sign-in. open ACCOUNTS and connect a {plat} account that is "
                    "allowed to see it, then try again. the app can only save what that account itself can view.")
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


def _colorref(hex_color: str) -> ctypes.c_int:
    c = QColor(hex_color)
    return ctypes.c_int((c.blue() << 16) | (c.green() << 8) | c.red())


def style_titlebar(widget: QWidget) -> None:
    """Dark title bar; on Windows 11 also tint it to match the window."""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        one = ctypes.c_int(1)
        for attr in (20, 19):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(one), 4)
        for attr, col in ((35, BG), (36, TEXT), (34, BORDER)):  # caption, text, border (Win11 only)
            v = _colorref(col)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
    except Exception:
        pass


def open_folder_of(path: str) -> None:
    p = Path(path)
    try:
        if p.exists():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(p.parent if p.parent.exists() else DATA_DIR)  # noqa: S606
    except Exception:
        pass


def icon_pixmap(size: int) -> QPixmap:
    """App icon: a terminal window with a prompt chevron and a download arrow."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, size * 0.18, size * 0.18)
    p.fillPath(path, QColor("#101412"))
    p.setPen(QPen(QColor("#2e3a34"), max(1.0, size * 0.025)))
    p.drawPath(path)
    pen = QPen(QColor(ACCENT), size * 0.085, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    s = size
    p.drawLine(QPoint(int(s * .24), int(s * .30)), QPoint(int(s * .40), int(s * .44)))
    p.drawLine(QPoint(int(s * .40), int(s * .44)), QPoint(int(s * .24), int(s * .58)))
    p.drawLine(QPoint(int(s * .50), int(s * .62)), QPoint(int(s * .76), int(s * .62)))
    p.drawLine(QPoint(int(s * .64), int(s * .24)), QPoint(int(s * .64), int(s * .42)))
    p.drawLine(QPoint(int(s * .64), int(s * .42)), QPoint(int(s * .56), int(s * .34)))
    p.drawLine(QPoint(int(s * .64), int(s * .42)), QPoint(int(s * .72), int(s * .34)))
    p.drawLine(QPoint(int(s * .24), int(s * .78)), QPoint(int(s * .76), int(s * .78)))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(icon_pixmap(size))
    return icon


_framed_cache: dict = {}


def framed_pixmap(path: str | None, w: int, h: int, radius: int = 2, data: bytes | None = None) -> QPixmap:
    key = (path, w, h, radius, len(data) if data else 0)
    if key in _framed_cache and not data:
        return _framed_cache[key]
    src = QPixmap()
    if data:
        src.loadFromData(data)
    elif path and Path(path).exists():
        src.load(path)
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, w, h, radius, radius)
    p.setClipPath(clip)
    p.fillRect(0, 0, w, h, QColor("#080a09"))
    if not src.isNull():
        s = src.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawPixmap((w - s.width()) // 2, (h - s.height()) // 2, s)
    else:  # no thumbnail: draw a quiet placeholder
        p.setPen(QColor(DIM))
        f = QFont(MONO)
        f.setPointSizeF(9)
        p.setFont(f)
        p.drawText(QRect(0, 0, w, h), Qt.AlignCenter, "no preview")
    p.end()
    if not data:
        _framed_cache[key] = out
    return out


def text_bar(frac: float, cells: int = 30) -> str:
    n = max(0, min(cells, int(round(frac * cells))))
    return (f"<span style='color:{ACCENT}'>[{'█' * n}</span><span style='color:{DIM}'>{'░' * (cells - n)}</span>"
            f"<span style='color:{ACCENT}'>]</span>")


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


def base_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "socket_timeout": 25, "retries": 3, "logger": QuietLogger(), "windowsfilenames": True}
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
            except Exception:  # noqa: BLE001
                raise first  # report the signed-in error: it is the more informative one
            ACCOUNTS.mark(plat, "attention")
            return result
    return attempt(None)


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
            self.s.info_err.emit(self.token, friendly_error(str(e), account_ctx(plat)))


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

    def run(self):
        try:
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


def mono(pt: float, bold: bool = False) -> QFont:
    f = QFont(MONO)
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


def wrapped_text(p: QPainter, text: str, rect: QRect, max_lines: int) -> None:
    fm = p.fontMetrics()
    layout = QTextLayout(text, p.font())
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    y, n = rect.y(), 0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(rect.width())
        n += 1
        start, length = line.textStart(), line.textLength()
        if n == max_lines:
            p.drawText(QPoint(rect.x(), y + fm.ascent()), fm.elidedText(text[start:], Qt.ElideRight, rect.width()))
            break
        p.drawText(QPoint(rect.x(), y + fm.ascent()), text[start:start + length].rstrip())
        y += fm.lineSpacing()
    layout.endLayout()


def draw_tag(p: QPainter, x: int, y: int, text: str, fg: QColor | None = None, pt: float = 8) -> int:
    """Small square bracket-style tag, e.g. [TIKTOK]."""
    p.setFont(mono(pt, True))
    fm = QFontMetrics(p.font())
    w = fm.horizontalAdvance(text) + 12
    h = fm.height() + 2
    p.setPen(QPen(QColor(BORDER_HI), 1))
    p.setBrush(QColor(12, 14, 13, 215))
    p.drawRect(x, y, w, h)
    p.setPen(fg or QColor(TEXT))
    p.drawText(QRect(x, y, w, h), Qt.AlignCenter, text)
    return w


def entry_date(e: dict) -> str:
    try:
        return datetime.fromisoformat(e["date"]).strftime("%Y-%m-%d")
    except Exception:
        return ""


class GridDelegate(QStyledItemDelegate):
    MIN_W = 224
    W = 228  # updated by MainWindow so cards fill each row exactly

    @staticmethod
    def thumb_h(w: int) -> int:
        return max(96, int((w - 8 - 16) * 9 / 16))

    @property
    def H(self) -> int:
        return self.thumb_h(self.W) + 92

    def sizeHint(self, option, index):
        return QSize(self.W, self.H)

    def paint(self, p: QPainter, opt, index):
        e = index.data(Qt.UserRole)
        r = opt.rect.adjusted(4, 4, -4, -4)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        missing = not Path(e["path"]).exists()
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        p.setBrush(QColor(PANEL_HI if (hover or sel) else PANEL))
        p.setPen(QPen(QColor(ACCENT if (sel or hover) else BORDER), 1))
        p.drawRoundedRect(r, 3, 3)

        tr = QRect(r.x() + 8, r.y() + 8, r.width() - 16, self.thumb_h(r.width() + 8))
        p.drawPixmap(tr.topLeft(), framed_pixmap(e.get("thumb"), tr.width(), tr.height(), 2))
        draw_tag(p, tr.x() + 6, tr.y() + 6, PLATFORM_TAG.get(e.get("platform"), "VIDEO"))
        dur = fmt_duration(e.get("duration")) or ("MP3" if e.get("kind") == "audio" else "")
        if dur:
            p.setFont(mono(8, True))
            w = QFontMetrics(p.font()).horizontalAdvance(dur) + 12
            draw_tag(p, tr.right() - w - 5, tr.bottom() - 22, dur)
        if missing:
            p.fillRect(tr, QColor(12, 14, 13, 205))
            p.setPen(QColor(ERR))
            p.setFont(mono(9, True))
            p.drawText(tr, Qt.AlignCenter, "[ file missing ]")
        elif hover:
            p.setPen(QColor(ACCENT))
            p.setFont(mono(10, True))
            band = QRect(tr.x(), tr.bottom() - 26, tr.width(), 26)
            p.fillRect(band, QColor(12, 14, 13, 200))
            p.drawText(band, Qt.AlignCenter, "> play")

        p.setFont(mono(9.5, True))
        p.setPen(QColor(TEXT))
        wrapped_text(p, e.get("title") or e["filename"], QRect(r.x() + 12, tr.bottom() + 12, r.width() - 24, 36), 2)
        p.setFont(mono(8.5))
        p.setPen(QColor(MUTED))
        meta = "  ".join(x for x in (entry_date(e), fmt_size(e.get("size"))) if x)
        p.drawText(QRect(r.x() + 12, r.bottom() - 28, r.width() - 24, 20), Qt.AlignVCenter | Qt.AlignLeft, meta)
        p.restore()


class ListDelegate(QStyledItemDelegate):
    """`ls -l` style row: thumbnail, title + file name, then aligned columns."""
    H = 76

    def sizeHint(self, option, index):
        return QSize(100, self.H)

    def paint(self, p: QPainter, opt, index):
        e = index.data(Qt.UserRole)
        r = opt.rect.adjusted(2, 2, -2, -2)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        missing = not Path(e["path"]).exists()
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        p.setBrush(QColor(PANEL_HI if (hover or sel) else PANEL))
        p.setPen(QPen(QColor(ACCENT if sel else (BORDER_HI if hover else BORDER)), 1))
        p.drawRoundedRect(r, 3, 3)
        tr = QRect(r.x() + 8, r.y() + 8, 90, 52)
        p.drawPixmap(tr.topLeft(), framed_pixmap(e.get("thumb"), tr.width(), tr.height(), 2))

        cols = [(86, PLATFORM_TAG.get(e.get("platform"), "VIDEO")), (70, fmt_duration(e.get("duration")) or ("mp3" if e.get("kind") == "audio" else "-")),
                (84, fmt_size(e.get("size")) or "-"), (96, entry_date(e) or "-")]
        x = r.right() - 14 - sum(w for w, _ in cols)
        p.setFont(mono(9))
        for w, text in cols:
            p.setPen(QColor(MUTED if text not in PLATFORM_TAG.values() else TEXT))
            p.drawText(QRect(x, r.y(), w, r.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += w
        tx, tw = tr.right() + 16, max(60, r.right() - 14 - sum(w for w, _ in cols) - (tr.right() + 16) - 12)
        p.setFont(mono(10, True))
        p.setPen(QColor(ERR if missing else TEXT))
        fm = p.fontMetrics()
        title = ("[missing] " if missing else "") + (e.get("title") or e["filename"])
        p.drawText(QRect(tx, r.y() + 12, tw, 22), Qt.AlignVCenter | Qt.AlignLeft, fm.elidedText(title, Qt.ElideRight, tw))
        p.setFont(mono(8.5))
        p.setPen(QColor(MUTED))
        fm2 = p.fontMetrics()
        p.drawText(QRect(tx, r.y() + 38, tw, 20), Qt.AlignVCenter | Qt.AlignLeft, fm2.elidedText(e["filename"], Qt.ElideMiddle, tw))
        p.restore()


# ----------------------------------------------------------------------------- widgets
class Toast(QLabel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        self.eff = QGraphicsOpacityEffect(self)
        self.eff.setOpacity(0)
        self.setGraphicsEffect(self.eff)
        self.anim = QPropertyAnimation(self.eff, b"opacity", self)
        self.anim.finished.connect(lambda: self.hide() if self.eff.opacity() < 0.05 else None)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self._out)
        self.hide()

    def popup(self, text: str, error: bool = False):
        self.setText(f"<span style='color:{ERR if error else ACCENT}'>[{'err' if error else 'ok'}]</span>&nbsp;&nbsp;{text}")
        self.adjustSize()
        pw = self.parentWidget()
        self.move(30, pw.height() - self.height() - 24)
        self.raise_()
        self.show()
        self._fade(1, 160)
        self.timer.start(2400)

    def _out(self):
        self._fade(0, 320)

    def _fade(self, end: float, ms: int):
        self.anim.stop()
        self.anim.setDuration(ms)
        self.anim.setStartValue(self.eff.opacity())
        self.anim.setEndValue(end)
        self.anim.start()


class Skeleton(QWidget):
    """Loading placeholder shaped like the preview panel, with a moving shimmer band."""

    def __init__(self):
        super().__init__()
        self.phase = 0.0
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self._step)

    def showEvent(self, e):
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def _step(self):
        self.phase = (self.phase + 0.012) % 1.4
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        shapes = [QRect(16, 16, 300, 236)]
        x0 = 340
        for i, frac in enumerate((0.38, 0.62, 0.50, 0.30, 0.44, 0.36)):
            shapes.append(QRect(x0, 22 + i * 30, int((w - x0 - 30) * frac), 14))
        shapes.append(QRect(x0, 22 + 6 * 30 + 14, 150, 34))
        shapes.append(QRect(x0 + 164, 22 + 6 * 30 + 14, 110, 34))
        band_x = -200 + self.phase * (w + 400)
        grad = QLinearGradient(band_x, 0, band_x + 200, 0)
        grad.setColorAt(0, QColor("#171b19"))
        grad.setColorAt(0.5, QColor("#222a26"))
        grad.setColorAt(1, QColor("#171b19"))
        p.setPen(Qt.NoPen)
        for s in shapes:
            p.fillRect(s, QColor("#171b19"))
            clip = QPainterPath()
            clip.addRect(s)
            p.save()
            p.setClipPath(clip)
            p.fillRect(s, grad)
            p.restore()
        p.setPen(QColor(MUTED))
        p.setFont(mono(9))
        p.drawText(QPoint(x0, h - 14), "probing source...")


class Gallery(QListWidget):
    resized = Signal()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


class ClickableVideo(QVideoWidget):
    clicked = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()


class PromptEdit(QLineEdit):
    focusChanged = Signal(bool)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focusChanged.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focusChanged.emit(False)


class Scanlines(QWidget):
    """Very faint CRT scanline overlay (static, ignores the mouse)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.tile = QPixmap(4, 4)
        self.tile.fill(Qt.transparent)
        tp = QPainter(self.tile)
        tp.fillRect(0, 0, 4, 1, QColor(255, 255, 255, 5))
        tp.end()

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawTiledPixmap(self.rect(), self.tile)


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("hline")
    f.setFixedHeight(1)
    return f


def term_button(text: str, name: str = "tbtn") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(CONTROL_H)
    b.setMinimumWidth(88)
    return b


class PlayerDialog(QDialog):
    def __init__(self, win: "MainWindow", entry: dict):
        super().__init__(win)
        self.win, self.entry = win, entry
        self.setWindowTitle(f"play - {entry['filename']}")
        self.resize(980, 780)
        self.setMinimumSize(720, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)

        head = QLabel(f"<span style='color:{ACCENT}'>&gt;</span> play <span style='color:{MUTED}'>{entry['filename']}</span>")
        lay.addWidget(head)
        lay.addWidget(hline())

        self.video = ClickableVideo()
        self.video.setMinimumHeight(340)
        lay.addWidget(self.video, 1)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.video.clicked.connect(self.toggle)

        ctr = QHBoxLayout()
        ctr.setSpacing(12)
        self.play_btn = term_button("PAUSE")
        self.play_btn.setMinimumWidth(84)
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setObjectName("muted")
        self.time_lbl.setMinimumWidth(110)
        vol = QSlider(Qt.Horizontal)
        vol.setRange(0, 100)
        vol.setValue(80)
        vol.setFixedWidth(90)
        vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        vl = QLabel("vol")
        vl.setObjectName("muted")
        ctr.addWidget(self.play_btn)
        ctr.addWidget(self.slider, 1)
        ctr.addWidget(self.time_lbl)
        ctr.addWidget(vl)
        ctr.addWidget(vol)
        lay.addLayout(ctr)

        self.err = QLabel("")
        self.err.setObjectName("error")
        self.err.setWordWrap(True)
        self.err.hide()
        lay.addWidget(self.err)
        lay.addWidget(hline())

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        try:
            date = datetime.fromisoformat(entry["date"]).strftime("%Y-%m-%d %H:%M")
        except Exception:
            date = "-"
        link = entry.get("url") or ""
        rows = [("title", entry.get("title") or "-"), ("platform", PLATFORM_TAG.get(entry.get("platform"), "-").lower()),
                ("source", f'<a href="{link}" style="color:{ACCENT}">{link}</a>' if link else "-"),
                ("file", entry["filename"]), ("saved", date),
                ("info", "  ".join(x for x in (fmt_duration(entry.get("duration")), fmt_size(entry.get("size")),
                                               "mp3" if entry.get("kind") == "audio" else "mp4") if x))]
        for i, (k, v) in enumerate(rows):
            kl = QLabel(k)
            kl.setObjectName("muted")
            kl.setAlignment(Qt.AlignTop)
            vl2 = QLabel(v)
            vl2.setWordWrap(True)
            vl2.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            vl2.setOpenExternalLinks(True)
            grid.addWidget(kl, i, 0)
            grid.addWidget(vl2, i, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addWidget(hline())

        btns = QHBoxLayout()
        btns.setSpacing(8)
        for text, fn in (("OPEN FOLDER", lambda: open_folder_of(entry["path"])),
                         ("COPY LINK", lambda: win.copy_link(entry)),
                         ("DEFAULT PLAYER", self.open_external)):
            b = term_button(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        d = term_button("DELETE", "danger")
        d.clicked.connect(self.delete)
        c = term_button("CLOSE")
        c.clicked.connect(self.accept)
        btns.addWidget(d)
        btns.addWidget(c)
        lay.addLayout(btns)

        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.playbackStateChanged.connect(
            lambda s: self.play_btn.setText("PAUSE" if s == QMediaPlayer.PlayingState else "PLAY"))
        self.player.mediaStatusChanged.connect(self._status)
        self.player.errorOccurred.connect(lambda *_: self._show_err())
        self.player.setSource(QUrl.fromLocalFile(entry["path"]))
        self.player.play()

    def showEvent(self, e):
        super().showEvent(e)
        style_titlebar(self)

    def _pos(self, ms):
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.time_lbl.setText(f"{fmt_duration(ms // 1000) or '0:00'} / {fmt_duration(self.player.duration() // 1000) or '0:00'}")

    def _status(self, st):
        if st == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0)
            self.player.pause()

    def _show_err(self):
        why = (self.player.errorString() or "").strip()
        self.err.setText("[err] this file can't be played inside the app" + (f" ({why})" if why else "") +
                         ". use DEFAULT PLAYER, or download it again as mp4 (h.264).")
        self.err.show()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def open_external(self):
        self.player.pause()
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.entry["path"]))

    def release(self):
        self.player.stop()
        self.player.setSource(QUrl())

    def delete(self):
        if self.win.delete_entry(self.entry, self, before=self.release):
            self.accept()

    def done(self, r):
        self.release()
        super().done(r)


class AccountsDialog(QDialog):
    """Connected accounts: connect / reconnect / disconnect TikTok, Instagram and X."""
    changed = Signal()

    ORDER = ("TikTok", "Instagram", "X")

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.setWindowTitle("Connected accounts")
        self.resize(760, 690)
        self.setMinimumSize(700, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(GAP)

        lay.addWidget(QLabel(f"<span style='color:{ACCENT}'>&gt;</span> <span style='font-weight:bold;font-size:13pt'>"
                             f"connected accounts</span>"))
        sub = QLabel("connect the accounts you want to use, so posts those accounts are allowed to see "
                     "(for example a private profile that accepted your follow) can be previewed and saved.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addWidget(hline())

        self.rows: dict[str, dict] = {}
        for plat in self.ORDER:
            f = QFrame()
            f.setObjectName("panel")
            h = QHBoxLayout(f)
            h.setContentsMargins(PAD, 14, PAD, 14)
            h.setSpacing(12)
            name = QLabel(PLATFORM_CFG[plat]["label"])
            name.setStyleSheet("font-weight:bold;font-size:11pt;")
            name.setFixedWidth(130)
            status = QLabel("")
            status.setWordWrap(True)
            status.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            connect = term_button("CONNECT", "primary")
            connect.setMinimumWidth(124)
            disconnect = term_button("DISCONNECT", "danger")
            disconnect.setMinimumWidth(124)
            connect.clicked.connect(lambda _=False, p=plat: self.connect_account(p))
            disconnect.clicked.connect(lambda _=False, p=plat: self.disconnect_account(p))
            h.addWidget(name)
            h.addWidget(status, 1)
            h.addWidget(connect)
            h.addWidget(disconnect)
            lay.addWidget(f)
            self.rows[plat] = {"status": status, "connect": connect, "disconnect": disconnect}

        self.note = QLabel("")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        info = QFrame()
        info.setObjectName("panel")
        il = QVBoxLayout(info)
        il.setContentsMargins(PAD, 12, PAD, 12)
        il.setSpacing(6)
        for head, body in (
            ("how sign-in works", "you sign in on the platform's own page, inside this app. your password is never seen "
                                  "or stored here. only the session cookie is kept, encrypted with Windows for your user "
                                  "account on this PC. DISCONNECT deletes it."),
            ("what it can do", "open posts that the connected account is itself allowed to view. use it only for videos "
                               "you own or have the owner's permission to save."),
            ("limits", "the app never bypasses privacy settings. some private or download-restricted posts can't be saved "
                       "even when you can watch them, because the platform doesn't serve a file for them. using any "
                       "account with automated tools can occasionally trigger a security check on that platform. to end a "
                       "session everywhere, also sign out of other devices in the platform's own security settings.")):
            l = QLabel(f"<span style='color:{ACCENT}'>{head}</span><br><span style='color:{MUTED}'>{body}</span>")
            l.setWordWrap(True)
            il.addWidget(l)
        lay.addWidget(info)
        lay.addStretch(1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        close = term_button("CLOSE")
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        lay.addLayout(foot)
        self.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        style_titlebar(self)

    def refresh(self):
        for plat, w in self.rows.items():
            info = ACCOUNTS.info(plat)
            st, name = info["state"], info["username"]
            saved = info["saved"][:10]
            if st == "connected":
                head = f"<span style='color:{ACCENT}'>■</span> connected" + (f" as <b>@{name}</b>" if name else "")
                sub = f"signed in, saved {saved}" if saved else "signed in"
            elif st == "attention":
                head = f"<span style='color:{ERR}'>■</span> needs attention" + (f" (@{name})" if name else "")
                sub = "the last request with this session failed. it may have expired: reconnect if private posts stop working."
            elif st == "expired":
                head = f"<span style='color:{ERR}'>■</span> session expired" + (f" (@{name})" if name else "")
                sub = "sign in again to keep access to private posts."
            elif st == "unreadable":
                head = f"<span style='color:{ERR}'>■</span> saved session can't be read"
                sub = "it was saved by another Windows user or PC. reconnect."
            else:
                head = f"<span style='color:{DIM}'>■</span> not connected"
                sub = "public posts work without an account."
            w["status"].setText(f"{head}<br><span style='color:{MUTED}'>{sub}</span>")
            has = st != "none"
            w["connect"].setText("RECONNECT" if has else "CONNECT")
            w["disconnect"].setEnabled(has)

    def connect_account(self, plat: str):
        cfg = PLATFORM_CFG[plat]
        try:
            from login_dialog import LoginDialog
            dlg = LoginDialog(self, plat, cfg)
        except Exception as e:  # noqa: BLE001
            self.note.setText(f"<span style='color:{ERR}'>[err]</span> the built-in browser couldn't start: {e}")
            return
        dlg.exec()
        res = dlg.result
        if res and res["cookies"]:
            try:
                ACCOUNTS.save(plat, res["cookies"], res["username"])
            except Exception as e:  # noqa: BLE001
                self.note.setText(f"<span style='color:{ERR}'>[err]</span> couldn't save the session securely: {e}")
                return
            who = f"@{res['username']}" if res["username"] else "your account"
            self.note.setText(f"<span style='color:{ACCENT}'>[ok]</span> {cfg['label']} connected as {who}.")
        else:
            self.note.setText(f"<span style='color:{MUTED}'>[--] sign-in to {cfg['label']} was cancelled.</span>")
        self.refresh()
        self.changed.emit()

    def disconnect_account(self, plat: str):
        label = PLATFORM_CFG[plat]["label"]
        r = QMessageBox.question(self, "Disconnect", f"Disconnect {label}?\n\nThe saved session is deleted from this PC. "
                                 f"To end it everywhere, also sign out of other devices in {label}'s security settings.")
        if r != QMessageBox.Yes:
            return
        ACCOUNTS.remove(plat)
        self.note.setText(f"<span style='color:{MUTED}'>[--] {label} disconnected. the saved session was deleted.</span>")
        self.refresh()
        self.changed.emit()


# ----------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1120, 920)
        self.setMinimumSize(1000, 800)
        self.cfg = load_json(CFG_FILE, {})
        self.save_dir = self.cfg.get("save_dir") or str(Path.home() / "Videos" / "Video Downloader")
        self.view_mode = self.cfg.get("view", "grid")
        self.lib = Library()
        self.pool = QThreadPool.globalInstance()
        self.tasks: set = set()
        self.token = 0
        self.fetched_url = None
        self.summary: dict | None = None
        self.dl_task: DownloadTask | None = None
        self.busy = False
        self.last_clip = None
        self.blink = True
        self.grid_delegate = GridDelegate(self)
        self.list_delegate = ListDelegate(self)
        self._build()
        self._refresh_accounts()
        self.toast = Toast(self.centralWidget())
        self.scan = Scanlines(self.centralWidget())
        self.scan.setGeometry(self.centralWidget().rect())
        self.scan.raise_()
        self.refresh_library()
        self.blink_timer = QTimer(self, interval=530)
        self.blink_timer.timeout.connect(self._blink)
        self.blink_timer.start()

    # ---- UI construction
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        content = QWidget()
        content.setMaximumWidth(1480)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer.addWidget(content, 1000)
        outer.addStretch(1)

        v = QVBoxLayout(content)
        v.setContentsMargins(MARGIN, 22, MARGIN, 20)
        v.setSpacing(GAP)

        # header
        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(icon_pixmap(34))
        title = QLabel(f"<span style='color:{TEXT};font-weight:bold;font-size:14pt'>video-downloader</span>"
                       f"<span style='color:{MUTED}'>&nbsp;&nbsp;v{VERSION}</span>")
        self.sess = QLabel("")
        self.sess.setObjectName("muted")
        head.addWidget(logo)
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(self.sess)
        head.addStretch(1)
        self.acct_chips: dict[str, QPushButton] = {}
        for plat in ("TikTok", "Instagram", "X"):
            b = QPushButton("")
            b.setObjectName("acct")
            b.setFixedHeight(28)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(self.open_accounts)
            self.acct_chips[plat] = b
            head.addWidget(b)
        head.addSpacing(4)
        acct_btn = term_button("ACCOUNTS")
        acct_btn.setMinimumWidth(104)
        acct_btn.clicked.connect(self.open_accounts)
        head.addWidget(acct_btn)
        v.addLayout(head)
        v.addWidget(hline())

        # prompt
        self.prompt = QFrame()
        self.prompt.setObjectName("prompt")
        self.prompt.setProperty("focused", False)
        bl = QHBoxLayout(self.prompt)
        bl.setContentsMargins(PAD, 8, 8, 8)
        bl.setSpacing(8)
        chev = QLabel(">")
        chev.setStyleSheet(f"color:{ACCENT};font-size:15pt;font-weight:bold;")
        self.url_input = PromptEdit()
        self.url_input.setObjectName("url")
        self.url_input.setPlaceholderText("paste a link  (x.com / tiktok.com / instagram.com)")
        self.url_input.setFixedHeight(CONTROL_H)
        self.url_input.focusChanged.connect(self._prompt_focus)
        self.url_input.textChanged.connect(lambda: self.url_timer.start(450))
        self.url_input.returnPressed.connect(self.on_url_changed)
        self.platform_tag = QLabel("")
        self.platform_tag.setObjectName("tag")
        self.platform_tag.setFixedHeight(28)
        self.platform_tag.hide()
        self.paste_btn = term_button("PASTE")
        self.paste_btn.clicked.connect(self.paste)
        self.clear_btn = term_button("CLEAR")
        self.clear_btn.clicked.connect(lambda: self.url_input.clear())
        self.clear_btn.hide()
        bl.addWidget(chev)
        bl.addSpacing(4)
        bl.addWidget(self.url_input, 1)
        bl.addWidget(self.platform_tag)
        bl.addSpacing(4)
        bl.addWidget(self.clear_btn)
        bl.addWidget(self.paste_btn)
        v.addWidget(self.prompt)
        self.hint = QLabel("")
        self.hint.setObjectName("error")
        self.hint.hide()
        v.addWidget(self.hint)
        self.url_timer = QTimer(self, singleShot=True)
        self.url_timer.timeout.connect(self.on_url_changed)

        # preview stack (one fixed-height slot: empty / loading / preview / error)
        self.stack = QStackedWidget()
        self.stack.setFixedHeight(300)
        v.addWidget(self.stack)
        self.stack.addWidget(self._page_empty())
        self.skeleton = Skeleton()
        self.stack.addWidget(self.skeleton)
        self.stack.addWidget(self._page_preview())
        self.stack.addWidget(self._page_error())

        # library toolbar
        v.addWidget(hline())
        lh = QHBoxLayout()
        lh.setSpacing(8)
        self.lib_title = QLabel("")
        self.search = QLineEdit()
        self.search.setPlaceholderText("grep: filter")
        self.search.setFixedSize(200, CONTROL_H)
        self.search.textChanged.connect(self.refresh_library)
        self.filter = QComboBox()
        self.filter.addItems(["all", "X", "TikTok", "Instagram"])
        self.filter.setFixedSize(130, CONTROL_H)
        self.filter.currentIndexChanged.connect(self.refresh_library)
        self.grid_btn = term_button("GRID", "segL")
        self.list_btn = term_button("LIST", "segR")
        seg = QHBoxLayout()
        seg.setSpacing(0)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for b in (self.grid_btn, self.list_btn):
            b.setCheckable(True)
            b.setMinimumWidth(72)
            grp.addButton(b)
            seg.addWidget(b)
        (self.grid_btn if self.view_mode == "grid" else self.list_btn).setChecked(True)
        self.grid_btn.clicked.connect(lambda: self.set_view("grid"))
        self.list_btn.clicked.connect(lambda: self.set_view("list"))
        open_dir = term_button("OPEN DIR")
        open_dir.clicked.connect(self._open_dir)
        lh.addWidget(self.lib_title)
        lh.addStretch(1)
        lh.addWidget(self.search)
        lh.addWidget(self.filter)
        lh.addSpacing(8)
        lh.addLayout(seg)
        lh.addSpacing(8)
        lh.addWidget(open_dir)
        v.addLayout(lh)

        # library
        self.lib_stack = QStackedWidget()
        self.lib_empty = QLabel("")
        self.lib_empty.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lib_empty.setContentsMargins(4, 8, 0, 0)
        self.listw = Gallery()
        self.listw.setResizeMode(QListWidget.Adjust)
        self.listw.setMovement(QListWidget.Static)
        self.listw.setFrameShape(QFrame.NoFrame)
        self.listw.setMouseTracking(True)
        self.listw.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.listw.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self.lib_menu)
        self.listw.itemClicked.connect(lambda it: self.open_entry(it.data(Qt.UserRole)))
        self.listw.resized.connect(self._relayout_grid)
        self.lib_stack.addWidget(self.lib_empty)
        self.lib_stack.addWidget(self.listw)
        v.addWidget(self.lib_stack, 1)
        self._apply_view()

    def _relayout_grid(self):
        """Grid mode: pick a column count for the current width and stretch cards to fill the row."""
        if self.view_mode != "grid":
            return
        gap = self.listw.spacing()
        avail = max(300, self.listw.width() - 24)  # reserve room for the scrollbar so layout never flaps
        n = max(1, avail // (GridDelegate.MIN_W + gap))
        w = avail // n - gap
        d = self.grid_delegate
        if w == d.W and self.listw.count() and self.listw.item(0).sizeHint().width() == w:
            return
        d.W = w
        size = QSize(w, d.H)
        for i in range(self.listw.count()):
            self.listw.item(i).setSizeHint(size)
        self.listw.doItemsLayout()

    def _page_empty(self):
        w = QFrame()
        w.setObjectName("panel")
        l = QVBoxLayout(w)
        l.setContentsMargins(26, 22, 26, 22)
        self.empty_lbl = QLabel("")
        self.empty_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        l.addWidget(self.empty_lbl)
        l.addStretch(1)
        return w

    def _render_empty(self):
        cur = "_" if self.blink else "&nbsp;"
        self.empty_lbl.setText(
            f"<p style='line-height:170%'>"
            f"<span style='color:{MUTED}'>no input yet.</span><br>"
            f"<span style='color:{ACCENT}'>&gt;</span> <span style='color:{TEXT}'>waiting for a link</span>"
            f"<span style='color:{ACCENT}'>{cur}</span><br><br>"
            f"<span style='color:{DIM}'>supported&nbsp;&nbsp;</span><span style='color:{MUTED}'>x.com &nbsp;twitter.com &nbsp;tiktok.com &nbsp;instagram.com (reels)</span><br>"
            f"<span style='color:{DIM}'>shortcut&nbsp;&nbsp;&nbsp;</span><span style='color:{MUTED}'>copy a link anywhere; it is picked up when you return here</span><br>"
            f"<span style='color:{DIM}'>output&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span><span style='color:{MUTED}'>mp4 video or mp3 audio, saved to your library</span>"
            f"</p>")

    def _page_error(self):
        w = QFrame()
        w.setObjectName("panel")
        l = QVBoxLayout(w)
        l.setContentsMargins(26, 22, 26, 22)
        self.err_lbl = QLabel("")
        self.err_lbl.setObjectName("error")
        self.err_lbl.setWordWrap(True)
        self.err_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        retry = term_button("RETRY")
        retry.clicked.connect(lambda: self.start_fetch(self.fetched_url) if self.fetched_url else None)
        self.err_accounts = term_button("OPEN ACCOUNTS")
        self.err_accounts.setMinimumWidth(150)
        self.err_accounts.clicked.connect(self.open_accounts)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(retry)
        row.addWidget(self.err_accounts)
        row.addStretch(1)
        l.addWidget(self.err_lbl)
        l.addSpacing(10)
        l.addLayout(row)
        l.addStretch(1)
        return w

    def _page_preview(self):
        w = QFrame()
        w.setObjectName("panel")
        col = QVBoxLayout(w)
        col.setContentsMargins(PAD, PAD, PAD, PAD)
        col.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(20)
        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(320, 200)
        self.thumb.setAlignment(Qt.AlignCenter)
        top.addWidget(self.thumb, 0, Qt.AlignTop)

        r = QVBoxLayout()
        r.setSpacing(10)
        self.log_rows: dict[str, tuple[QLabel, QLabel]] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        for i, key in enumerate(("source", "title", "author", "length", "stats")):
            k = QLabel(f"<span style='color:{ACCENT}'>[ok]</span>&nbsp;<span style='color:{MUTED}'>{key}</span>")
            k.setFixedWidth(112)
            k.setAlignment(Qt.AlignTop)
            val = QLabel("")
            val.setWordWrap(True)
            val.setAlignment(Qt.AlignTop)
            if key == "title":
                val.setStyleSheet(f"color:{TEXT};font-weight:bold;")
                val.setMaximumHeight(40)
            grid.addWidget(k, i, 0)
            grid.addWidget(val, i, 1)
            self.log_rows[key] = (k, val)
        grid.setColumnStretch(1, 1)
        r.addLayout(grid)
        r.addStretch(1)

        opts = QHBoxLayout()
        opts.setSpacing(12)
        self.q_combo = QComboBox()
        self.f_combo = QComboBox()
        self.f_combo.addItems(["mp4 (h.264)", "mp3 audio only", "mp4 (original)"])
        for i, tip in enumerate(("Best quality, re-encoded to H.264 when needed so it plays on any PC and in the built-in player.",
                                 "Audio only, saved as MP3.",
                                 "Keeps the site's original file. TikTok often uses HEVC, which Windows may not play without an extension.")):
            self.f_combo.setItemData(i, tip, Qt.ToolTipRole)
        self.f_combo.currentIndexChanged.connect(lambda i: self.q_combo.setEnabled(i != 1))
        self.dir_btn = term_button("", "path")
        self.dir_btn.clicked.connect(self.choose_dir)
        for cap, wdg, st in (("quality", self.q_combo, 2), ("format", self.f_combo, 2), ("save-to", self.dir_btn, 3)):
            c = QVBoxLayout()
            c.setSpacing(4)
            lbl = QLabel(cap)
            lbl.setObjectName("muted")
            if isinstance(wdg, QComboBox):
                wdg.setFixedHeight(CONTROL_H)
                wdg.setMinimumWidth(176)
            else:
                wdg.setMinimumWidth(176)
            c.addWidget(lbl)
            c.addWidget(wdg)
            opts.addLayout(c, st)
        r.addLayout(opts)
        self._update_dir_btn()
        top.addLayout(r, 1)
        col.addLayout(top)
        col.addWidget(hline())

        # action bar: status on the left, buttons right-aligned
        act = QHBoxLayout()
        act.setSpacing(8)
        pcol = QVBoxLayout()
        pcol.setSpacing(0)
        self.bar_lbl = QLabel("")
        self.status = QLabel("")
        self.status.setObjectName("muted")
        pcol.addWidget(self.bar_lbl)
        pcol.addWidget(self.status)
        self.cancel_btn = term_button("CANCEL")
        self.cancel_btn.setMinimumWidth(110)
        self.cancel_btn.clicked.connect(lambda: self.dl_task and self.dl_task.cancel())
        self.cancel_btn.hide()
        self.dl_btn = term_button("DOWNLOAD", "primary")
        self.dl_btn.setMinimumWidth(170)
        self.dl_btn.clicked.connect(self.start_download)
        act.addLayout(pcol, 1)
        act.addWidget(self.cancel_btn)
        act.addWidget(self.dl_btn)
        col.addLayout(act)
        return w

    # ---- connected accounts
    def _refresh_accounts(self):
        for plat, b in self.acct_chips.items():
            info = ACCOUNTS.info(plat)
            st, name = info["state"], info["username"]
            short = (name if len(name) <= 14 else name[:13] + "...") if name else ""
            if st in ("connected", "attention"):
                txt, kind = f"{plat}  {'@' + short if short else 'connected'}", "on" if st == "connected" else "warn"
            elif st == "expired":
                txt, kind = f"{plat}  expired", "warn"
            elif st == "unreadable":
                txt, kind = f"{plat}  reconnect", "warn"
            else:
                txt, kind = f"{plat}  off", "off"
            b.setText(txt)
            b.setProperty("kind", kind)
            b.setToolTip({"on": f"{plat}: connected" + (f" as @{name}" if name else ""),
                          "warn": f"{plat}: needs attention. click to manage.",
                          "off": f"{plat}: not connected. click to connect."}[kind])
            b.style().unpolish(b)
            b.style().polish(b)

    def open_accounts(self):
        dlg = AccountsDialog(self)
        dlg.changed.connect(self._refresh_accounts)
        dlg.exec()
        self._refresh_accounts()

    # ---- events
    def _prompt_focus(self, on: bool):
        self.prompt.setProperty("focused", on)
        self.prompt.style().unpolish(self.prompt)
        self.prompt.style().polish(self.prompt)

    def _blink(self):
        self.blink = not self.blink
        dot = ACCENT if (self.blink or self.busy) else DIM
        state = "downloading" if self.busy else "ready"
        self.sess.setText(f"<span style='color:{dot}'>■</span>&nbsp;{state}")
        if self.stack.currentIndex() == 0:
            self._render_empty()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "scan"):
            self.scan.setGeometry(self.centralWidget().rect())

    def showEvent(self, e):
        super().showEvent(e)
        style_titlebar(self)
        self._render_empty()
        QTimer.singleShot(0, self._relayout_grid)
        QTimer.singleShot(400, self.check_clipboard)

    def event(self, e):
        if e.type() == QEvent.WindowActivate:
            QTimer.singleShot(150, self.check_clipboard)
        return super().event(e)

    def check_clipboard(self):
        """If a supported link is on the clipboard and the box is empty, fill it in."""
        if self.busy or self.url_input.text():
            return
        url = extract_url(QGuiApplication.clipboard().text())
        if url and url != self.last_clip:
            self.last_clip = url
            self.url_input.setText(url)
            self.on_url_changed()

    def paste(self):
        txt = QGuiApplication.clipboard().text().strip()
        if txt:
            self.url_input.setText(txt)
            self.on_url_changed()

    def _open_dir(self):
        Path(self.save_dir).mkdir(parents=True, exist_ok=True)
        os.startfile(self.save_dir)  # noqa: S606

    def _update_dir_btn(self):
        fm = QFontMetrics(self.dir_btn.font())
        self.dir_btn.setText(fm.elidedText(self.save_dir, Qt.ElideMiddle, 200))
        self.dir_btn.setToolTip(self.save_dir)

    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose where to save videos", self.save_dir)
        if d:
            self.save_dir = os.path.normpath(d)
            self.cfg["save_dir"] = self.save_dir
            save_json(CFG_FILE, self.cfg)
            self._update_dir_btn()

    # ---- URL detection + preview
    def on_url_changed(self):
        self.url_timer.stop()
        text = self.url_input.text().strip()
        self.clear_btn.setVisible(bool(text))
        if self.busy:
            return
        if not text:
            self.platform_tag.hide()
            self.hint.hide()
            self.fetched_url = None
            self.token += 1
            self.stack.setCurrentIndex(0)
            return
        url = extract_url(text)
        if not url:
            self.platform_tag.hide()
            self.hint.setText("[err] that isn't a link from x, tiktok or instagram.")
            self.hint.show()
            self.stack.setCurrentIndex(0)
            return
        plat = platform_of(url)
        self.platform_tag.setText(f"{PLATFORM_TAG[plat]} detected")
        self.platform_tag.show()
        self.hint.hide()
        if url != self.fetched_url or self.stack.currentIndex() == 3:
            self.start_fetch(url)

    def start_fetch(self, url: str):
        self.token += 1
        self.fetched_url = url
        self.summary = None
        self.stack.setCurrentIndex(1)
        task = FetchTask(self.token, url)
        task.s.info.connect(self.on_info)
        task.s.info_err.connect(self.on_info_err)
        self.tasks.add(task)
        self.pool.start(task)

    def on_info_err(self, token: int, msg: str):
        if token != self.token:
            return
        self.err_lbl.setText(f"[err] {msg}")
        self.err_accounts.setVisible("ACCOUNTS" in msg)
        self.stack.setCurrentIndex(3)

    def _set_row(self, key: str, value: str):
        k, val = self.log_rows[key]
        k.setVisible(bool(value))
        val.setVisible(bool(value))
        val.setText(value)

    def on_info(self, token: int, s: dict):
        if token != self.token:
            return
        self.summary = s
        self.thumb.setPixmap(framed_pixmap(None, 318, 198, 2, data=s.get("thumb")))
        title = s["title"][:140] + ("..." if len(s["title"]) > 140 else "")
        best = s["heights"][0] if s["heights"] else None
        stats = "   ".join(x for x in (f"{fmt_count(s['views'])} views" if s["views"] is not None else "",
                                       f"{fmt_count(s['likes'])} likes" if s["likes"] is not None else "",
                                       s["date"]) if x)
        short = [str(min(m["w"] or h, h)) + "p" for h, m in s["heights"]]
        self._set_row("source", f"{PLATFORM_TAG.get(s['platform'], '').lower()}")
        self._set_row("title", title)
        self._set_row("author", ("@" + s["uploader"].lstrip("@")) if s["uploader"] else "")
        self._set_row("length", fmt_duration(s["duration"]))
        self._set_row("stats", stats)
        self.q_combo.clear()
        self.q_combo.addItem("best available", None)
        for h, meta in s["heights"]:
            label = f"{min(meta['w'] or h, h)}p"
            if meta["size"]:
                label += f"  ~{fmt_size(meta['size'])}"
            self.q_combo.addItem(label, h)
        self.f_combo.setCurrentIndex(0)
        self.bar_lbl.setText("")
        self.status.setStyleSheet("")
        self.status.setText("ready. choose quality and press download.")
        self.dl_btn.setEnabled(True)
        self.stack.setCurrentIndex(2)

    # ---- download
    def set_busy(self, busy: bool):
        self.busy = busy
        for w in (self.url_input, self.paste_btn, self.q_combo, self.f_combo, self.dir_btn):
            w.setEnabled(not busy)
        if not busy:
            self.q_combo.setEnabled(self.f_combo.currentIndex() != 1)
        self.dl_btn.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        self.dl_btn.setText("RUNNING..." if busy else "DOWNLOAD")

    def start_download(self):
        if self.busy or not self.summary:
            return
        self.token += 1
        audio = self.f_combo.currentIndex() == 1
        tag = "" if audio or self.q_combo.currentData() is None else self.q_combo.currentText().split()[0]
        task = DownloadTask(self.token, self.summary, self.save_dir, self.q_combo.currentData(), audio, tag,
                            compat=self.f_combo.currentIndex() == 0)
        task.s.progress.connect(self.on_progress)
        task.s.done.connect(self.on_done)
        task.s.fail.connect(self.on_fail)
        self.dl_task = task
        self.tasks.add(task)
        self.set_busy(True)
        self.bar_lbl.setText(f"{text_bar(0)}&nbsp;&nbsp;<span style='color:{TEXT}'>0%</span>")
        self.status.setStyleSheet("")
        self.status.setText("starting")
        self.pool.start(task)

    def on_progress(self, token: int, frac: float, text: str):
        if token != self.token:
            return
        self.bar_lbl.setText(f"{text_bar(frac)}&nbsp;&nbsp;<span style='color:{TEXT}'>{int(frac * 100)}%</span>")
        self.status.setText(text)

    def _finish_task(self):
        if self.dl_task:
            self.tasks.discard(self.dl_task)
        self.dl_task = None
        self.set_busy(False)

    def on_done(self, token: int, entry: dict):
        if token != self.token:
            return
        self._finish_task()
        self.bar_lbl.setText(f"{text_bar(1.0)}&nbsp;&nbsp;<span style='color:{ACCENT}'>100%</span>")
        self.status.setStyleSheet(f"color:{ACCENT};")
        self.status.setText(f"[done] saved -> {entry['filename']}")
        for old in [x for x in self.lib.items if os.path.normcase(x["path"]) == os.path.normcase(entry["path"])]:
            self.lib.remove(old)  # same file downloaded again -> keep a single library entry
        self.lib.add(entry)
        self.refresh_library()
        self.listw.setCurrentRow(0)
        self.listw.scrollToTop()
        self.toast.popup("download complete, added to library")

    def on_fail(self, token: int, msg: str):
        if token != self.token:
            return
        self._finish_task()
        self.bar_lbl.setText("")
        self.status.setStyleSheet(f"color:{ERR};")
        self.status.setText(f"[err] {msg}")

    # ---- library
    def set_view(self, mode: str):
        self.view_mode = mode
        self.cfg["view"] = mode
        save_json(CFG_FILE, self.cfg)
        self._apply_view()
        self.refresh_library()

    def _apply_view(self):
        grid = self.view_mode == "grid"
        self.listw.setViewMode(QListWidget.IconMode if grid else QListWidget.ListMode)
        self.listw.setFlow(QListWidget.LeftToRight if grid else QListWidget.TopToBottom)
        self.listw.setWrapping(grid)
        self.listw.setUniformItemSizes(True)
        self.listw.setSpacing(6 if grid else 2)
        self.listw.setItemDelegate(self.grid_delegate if grid else self.list_delegate)

    def refresh_library(self):
        q = self.search.text().strip().lower()
        plat = self.filter.currentText()
        grid = self.view_mode == "grid"
        self.listw.clear()
        total = len(self.lib.items)
        for e in self.lib.items:
            if plat != "all" and e.get("platform") != plat:
                continue
            hay = f"{e.get('title','')} {e.get('uploader','')} {e.get('filename','')} {e.get('platform','')}".lower()
            if q and q not in hay:
                continue
            it = QListWidgetItem()
            it.setData(Qt.UserRole, e)
            it.setToolTip(f"{e.get('title','')}\n{e['filename']}")
            it.setSizeHint(QSize(self.grid_delegate.W, self.grid_delegate.H) if grid else QSize(100, ListDelegate.H))
            self.listw.addItem(it)
        shown = self.listw.count()
        if grid:
            self._relayout_grid()
        self.lib_title.setText(f"<span style='color:{ACCENT}'>$</span> <span style='color:{TEXT};font-weight:bold'>ls library/</span>"
                               f"<span style='color:{MUTED}'>&nbsp;&nbsp;{shown if shown == total else f'{shown}/{total}'} item{'s' if shown != 1 else ''}</span>")
        if shown:
            self.lib_stack.setCurrentIndex(1)
        else:
            self.lib_stack.setCurrentIndex(0)
            if total == 0:
                self.lib_empty.setText(
                    f"<p style='line-height:170%'><span style='color:{MUTED}'>total 0</span><br>"
                    f"<span style='color:{DIM}'>(empty)</span><br><br>"
                    f"<span style='color:{ACCENT}'>&gt;</span> <span style='color:{TEXT}'>paste a link above to download your first video.</span><br>"
                    f"<span style='color:{MUTED}'>finished downloads land here with a thumbnail, source link and date.</span></p>")
            else:
                self.lib_empty.setText(f"<p><span style='color:{MUTED}'>grep: no matches for the current filter.</span></p>")

    def open_entry(self, e: dict):
        if not Path(e["path"]).exists():
            r = QMessageBox.question(self, "File missing",
                                     "This file was moved or deleted outside the app.\nRemove it from your library?")
            if r == QMessageBox.Yes:
                self.lib.remove(e)
                self.refresh_library()
            return
        PlayerDialog(self, e).exec()
        self.refresh_library()

    def copy_link(self, e: dict):
        QGuiApplication.clipboard().setText(e.get("url") or "")
        self.last_clip = e.get("url")  # don't auto-paste our own copy back into the URL box
        self.toast.popup("link copied")

    def delete_entry(self, e: dict, parent=None, before=None) -> bool:
        box = QMessageBox(parent or self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Delete video")
        box.setText(f"Delete \"{(e.get('title') or e['filename'])[:80]}\"?")
        box.setInformativeText("The file is moved to the Recycle Bin, so you can still restore it from there.")
        b_del = box.addButton("Move to Recycle Bin", QMessageBox.DestructiveRole)
        b_rm = box.addButton("Remove from library only", QMessageBox.ActionRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_del, b_rm):
            return False
        if before:
            before()
        if clicked is b_del and Path(e["path"]).exists():
            if not move_to_recycle_bin(e["path"]):
                QMessageBox.warning(parent or self, "Couldn't delete", "The file is in use or couldn't be removed. "
                                    "Close any player showing it and try again.")
                return False
        self.lib.remove(e)
        self.refresh_library()
        self.toast.popup("removed from library")
        return True

    def lib_menu(self, pos):
        it = self.listw.itemAt(pos)
        if not it:
            return
        e = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("play", lambda: self.open_entry(e))
        m.addAction("open folder", lambda: open_folder_of(e["path"]))
        m.addAction("copy original link", lambda: self.copy_link(e))
        m.addSeparator()
        m.addAction("delete...", lambda: self.delete_entry(e))
        m.exec(self.listw.viewport().mapToGlobal(pos))


# ----------------------------------------------------------------------------- styling
def stylesheet() -> str:
    return f"""
* {{ font-family: '{MONO}', 'Consolas', 'Lucida Console'; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#error {{ color: {ERR}; }}
QLabel#tag {{ color: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 3px; padding: 0 10px; font-size: 8.5pt; font-weight: bold; }}
QLabel#thumb {{ background: #080a09; border: 1px solid {BORDER}; border-radius: 3px; }}
QLabel#toast {{ background: {PANEL_HI}; border: 1px solid {ACCENT}; border-radius: 3px; padding: 9px 16px; }}
QFrame#hline {{ background: {BORDER}; border: none; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px; }}
QFrame#panel QLabel {{ background: transparent; }}
QFrame#prompt {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px; }}
QFrame#prompt[focused="true"] {{ border-color: {ACCENT}; }}
QLineEdit {{ background: {PANEL}; border: 1px solid {BORDER_HI}; border-radius: 3px; padding: 0 12px; selection-background-color: {ACCENT}; selection-color: {BG}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit#url {{ background: transparent; border: none; font-size: 12pt; padding: 0 2px; }}
QPushButton {{ background: transparent; border: 1px solid {BORDER_HI}; border-radius: 3px; padding: 0 16px; color: {MUTED}; font-size: 9pt; font-weight: bold; }}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: {PANEL_HI}; padding: 2px 16px 0 16px; }}
QPushButton:disabled {{ color: {DIM}; border-color: {BORDER}; }}
QPushButton#primary {{ border: 1px solid {ACCENT}; color: {ACCENT}; font-size: 9pt; letter-spacing: 2px; }}
QPushButton#primary:hover {{ background: {ACCENT}; color: {BG}; }}
QPushButton#primary:pressed {{ background: #4fb783; color: {BG}; padding: 2px 16px 0 16px; }}
QPushButton#primary:disabled {{ color: {DIM}; border-color: {BORDER}; background: transparent; }}
QPushButton#danger {{ color: {ERR}; border-color: #5a332f; }}
QPushButton#danger:hover {{ background: {ERR}; color: {BG}; border-color: {ERR}; }}
QPushButton#segL {{ border-top-right-radius: 0; border-bottom-right-radius: 0; padding: 0 14px; }}
QPushButton#segR {{ border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; padding: 0 14px; }}
QPushButton#segL:checked, QPushButton#segR:checked {{ color: {ACCENT}; border: 1px solid {ACCENT}; background: {PANEL}; }}
QPushButton#acct {{ padding: 0 12px; font-size: 8.5pt; color: {MUTED}; border-color: {BORDER_HI}; }}
QPushButton#acct[kind="on"] {{ color: {ACCENT}; border-color: {ACCENT}; }}
QPushButton#acct[kind="warn"] {{ color: {ERR}; border-color: #5a332f; }}
QPushButton#acct:hover {{ background: {PANEL_HI}; }}
QPushButton#path {{ text-align: left; padding: 0 12px; color: {TEXT}; font-weight: normal; font-size: 10pt; background: {PANEL}; }}
QPushButton#path:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#path:disabled {{ color: {DIM}; }}
QComboBox {{ background: {PANEL}; border: 1px solid {BORDER_HI}; border-radius: 3px; padding: 0 12px; min-width: 80px; }}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {DIM}; border-color: {BORDER}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {PANEL_HI}; border: 1px solid {BORDER_HI}; selection-background-color: {ACCENT}; selection-color: {BG}; outline: 0; padding: 3px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 10px; height: 14px; margin: -6px 0; border-radius: 2px; }}
QListWidget {{ background: transparent; outline: 0; }}
QListWidget::item {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; border-radius: 3px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{ background: {PANEL_HI}; border: 1px solid {BORDER_HI}; padding: 4px; }}
QMenu::item {{ padding: 7px 22px; }}
QMenu::item:selected {{ background: {ACCENT}; color: {BG}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QMessageBox {{ background: {BG}; }}
QToolTip {{ background: {PANEL_HI}; color: {TEXT}; border: 1px solid {BORDER_HI}; padding: 4px; }}
"""


def pick_mono() -> str:
    fams = set(QFontDatabase.families())
    for name in ("Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "Lucida Console", "Courier New"):
        if name in fams:
            return name
    return "Consolas"


def apply_theme(app: QApplication) -> None:
    global MONO
    MONO = pick_mono()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, PANEL),
                      (QPalette.AlternateBase, PANEL), (QPalette.Text, TEXT), (QPalette.Button, PANEL),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, BG),
                      (QPalette.ToolTipBase, PANEL_HI), (QPalette.ToolTipText, TEXT),
                      (QPalette.PlaceholderText, DIM)):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    f = QFont(MONO)
    f.setPointSizeF(10)
    app.setFont(f)
    app.setStyleSheet(stylesheet())


def probe(url: str, outfile: str) -> None:
    """Headless check used for diagnostics: fetch info for a link and write the result to a file."""
    res: dict = {}
    task = FetchTask(1, url)
    task.s.info.connect(lambda _t, s: res.update(ok=True, **{k: v for k, v in s.items() if k not in ("thumb", "heights")},
                                                  has_thumb=bool(s.get("thumb")), qualities=[h for h, _ in s["heights"]]))
    task.s.info_err.connect(lambda _t, m: res.update(ok=False, error=m))
    task.run()
    try:
        import curl_cffi
        res["curl_cffi"] = curl_cffi.__version__
    except Exception as e:  # noqa: BLE001
        res["curl_cffi"] = f"missing ({e})"
    res["yt_dlp"] = yt_dlp.version.__version__
    Path(outfile).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


def webengine_check(outfile: str) -> None:
    """Diagnostic: confirm the built-in browser (used for sign-in) starts and can run a page."""
    app = QApplication(sys.argv)
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    prof = QWebEngineProfile()
    page = QWebEnginePage(prof)
    view = QWebEngineView()
    view.setPage(page)
    res: dict = {"loaded": None, "title": None}

    def loaded(ok):
        res["loaded"] = ok
        page.runJavaScript("document.title", lambda v: (res.update(title=v), app.quit()))

    page.loadFinished.connect(loaded)
    page.renderProcessTerminated.connect(lambda s, c: (res.update(render_process_exit=int(c)), app.quit()))
    view.load(QUrl("data:text/html,<title>webengine-ok</title>"))
    QTimer.singleShot(25000, app.quit)
    app.exec()
    Path(outfile).write_text(json.dumps(res), encoding="utf-8")


def main() -> None:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)  # required before QApplication when using WebEngine
    if len(sys.argv) > 2 and sys.argv[1] == "--webengine-check":
        webengine_check(sys.argv[2])
        return
    if len(sys.argv) > 3 and sys.argv[1] == "--probe":
        probe(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) > 2 and sys.argv[1] == "--make-icon":
        QGuiApplication(sys.argv)
        icon_pixmap(256).toImage().save(sys.argv[2])
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VideoDownloader.App")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    apply_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
