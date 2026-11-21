"""Headless self-test of an installed copy:  VideoDownloader.exe --selfcheck result.json

Verifies that everything the app needs is really inside the install (no Python, pip, FFmpeg or other
tools required on the PC): the downloader engine and its site extractors, browser impersonation,
FFmpeg with the H.264 / AAC / MP3 encoders, the Qt modules, Windows encryption for saved sessions,
a writable data folder, and that a video can actually be decoded. No window, no network.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _ffmpeg() -> str:
    import imageio_ffmpeg
    p = imageio_ffmpeg.get_ffmpeg_exe()
    if not Path(p).exists():
        raise FileNotFoundError(p)
    return p


def _check_ytdlp() -> str:
    import yt_dlp
    from yt_dlp.extractor import get_info_extractor
    names = [get_info_extractor(n).IE_NAME for n in ("Twitter", "TikTok", "Instagram")]
    return f"yt-dlp {yt_dlp.version.__version__}, extractors: {', '.join(names)}"


def _check_impersonation() -> str:
    import curl_cffi
    import yt_dlp
    ydl = yt_dlp.YoutubeDL({"quiet": True})
    rh = ydl._request_director.handlers.get("CurlCFFI")
    if rh is None:
        raise RuntimeError("curl_cffi request handler is not available (TikTok would be blocked)")
    targets = getattr(rh, "_SUPPORTED_IMPERSONATE_TARGET_MAP", {})
    if not targets:
        raise RuntimeError("no browser-impersonation targets (TikTok would be blocked)")
    return f"curl_cffi {curl_cffi.__version__}, {len(targets)} browser profiles"


def _check_ffmpeg() -> str:
    ff = _ffmpeg()
    ver = subprocess.run([ff, "-hide_banner", "-version"], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    if ver.returncode != 0:
        raise RuntimeError("ffmpeg does not start")
    enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW).stdout
    missing = [e for e in ("libx264", "aac", "libmp3lame") if e not in enc]
    if missing:
        raise RuntimeError(f"ffmpeg is missing encoders: {', '.join(missing)}")
    return ver.stdout.splitlines()[0]


def _check_qt() -> str:
    import PySide6
    from PySide6 import QtCore, QtGui, QtMultimedia, QtMultimediaWidgets, QtNetwork, QtWebEngineCore, QtWebEngineWidgets, QtWidgets  # noqa: F401
    return f"PySide6 {PySide6.__version__} (Core, Gui, Widgets, Network, Multimedia, WebEngine)"


def _check_crypto() -> str:
    import accounts
    blob = accounts.protect(b"selfcheck")
    if accounts.unprotect(blob) != b"selfcheck" or b"selfcheck" in blob:
        raise RuntimeError("Windows DPAPI round trip failed")
    return "Windows DPAPI encryption works"


def _check_data_dir() -> str:
    import core
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    probe = core.DATA_DIR / ".selfcheck"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return str(core.DATA_DIR)


def _check_playback() -> str:
    """Make a 1-second H.264/AAC clip with the bundled FFmpeg and decode a frame through Qt Multimedia."""
    ff = _ffmpeg()
    clip = Path(tempfile.mkdtemp(prefix="vd_selfcheck_")) / "clip.mp4"
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=1", "-f", "lavfi",
                    "-i", "sine=duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
                   check=True, timeout=60, creationflags=NO_WINDOW)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    frames: list = []
    player, sink = QMediaPlayer(), QVideoSink()
    player.setVideoOutput(sink)
    sink.videoFrameChanged.connect(lambda f: frames.append(1) if f.isValid() else None)
    player.setSource(QUrl.fromLocalFile(str(clip)))
    player.play()
    t0 = time.time()
    while time.time() - t0 < 10 and not frames and player.error() == QMediaPlayer.Error.NoError:
        app.processEvents()
        time.sleep(0.02)
    if not frames:
        raise RuntimeError(f"no video frame was decoded ({player.errorString() or player.mediaStatus()})")
    player.stop()
    return f"decoded {len(frames)} frame(s) of an H.264 clip"


def run() -> dict:
    from version import VERSION
    res: dict = {"version": VERSION, "frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0], "checks": {}, "ok": True}
    for name, fn in (("yt-dlp engine", _check_ytdlp), ("browser impersonation", _check_impersonation), ("ffmpeg", _check_ffmpeg),
                     ("qt modules", _check_qt), ("encryption", _check_crypto), ("data folder", _check_data_dir),
                     ("video decoding", _check_playback)):
        try:
            res["checks"][name] = {"ok": True, "detail": fn()}
        except Exception as e:  # noqa: BLE001
            res["checks"][name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
            res["ok"] = False
    return res


def main(outfile: str) -> int:
    res = run()
    Path(outfile).write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0 if res["ok"] else 1
