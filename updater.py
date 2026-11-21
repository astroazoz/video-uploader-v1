"""Update checker built on GitHub Releases.

Nothing is ever installed without the user's confirmation. The installer is downloaded only from this
repository's GitHub release (HTTPS, GitHub hosts only), and is verified against the SHA-256 checksum
published in the same release before it is started.
"""

import hashlib
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from version import VERSION

REPO = "astroazoz/video-uploader-v1"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
INSTALLER_ASSET = "VideoDownloader-Setup.exe"
CHECKSUM_ASSET = "SHA256SUMS.txt"
ALLOWED_HOSTS = {"github.com", "api.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
                 "github-releases.githubusercontent.com"}
UA = f"VideoDownloader/{VERSION} (+https://github.com/{REPO})"


class UpdateError(Exception):
    pass


def _api_url() -> str:
    return os.environ.get("VD_UPDATE_URL") or API_URL  # the override exists only so the updater can be tested locally


def host_ok(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if os.environ.get("VD_UPDATE_URL") and host in ("127.0.0.1", "localhost"):
        return True
    return p.scheme == "https" and (host in ALLOWED_HOSTS or host.endswith(".githubusercontent.com"))


def parse_version(s) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", str(s))[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


def is_newer(latest, current: str = VERSION) -> bool:
    return parse_version(latest) > parse_version(current)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects (GitHub serves release files from another GitHub host) but never to anywhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_ok(newurl):
            raise UpdateError(f"refusing a redirect to an untrusted address: {urlparse(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: float):
    if not host_ok(url):
        raise UpdateError(f"refusing to contact an untrusted address: {urlparse(url).hostname}")
    opener = urllib.request.build_opener(_SafeRedirect())
    return opener.open(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json, */*"}),
                       timeout=timeout)


def check_latest(current: str = VERSION, timeout: float = 12) -> dict:
    """Return details of the latest published release (key 'newer' says whether it is newer than `current`)."""
    with _open(_api_url(), timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise UpdateError("no release found")
    assets = {a.get("name"): a for a in data.get("assets", [])}
    inst, sums = assets.get(INSTALLER_ASSET), assets.get(CHECKSUM_ASSET)
    return {"version": tag.lstrip("vV"), "tag": tag, "newer": is_newer(tag, current), "notes": (data.get("body") or "").strip(),
            "page": data.get("html_url") or RELEASES_PAGE, "installer_url": inst and inst.get("browser_download_url"),
            "installer_size": inst and inst.get("size"), "sums_url": sums and sums.get("browser_download_url")}


def plain_notes(md: str, limit: int = 1800) -> str:
    """Release notes without Markdown noise."""
    out = []
    for line in md.replace("\r", "").splitlines():
        line = re.sub(r"^#{1,6}\s*", "", line.strip())
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = line.replace("**", "").replace("`", "")
        out.append(line)
    text = "\n".join(out).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _expected_sha(sums_text: str) -> str | None:
    for line in sums_text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m and m.group(2) == INSTALLER_ASSET:
            return m.group(1).lower()
    return None


def download_installer(info: dict, dest_dir: str | None = None, progress=None, cancelled=None, timeout: float = 30) -> Path:
    """Download the release installer and verify its SHA-256. `progress(done, total)`; `cancelled()` -> bool."""
    if not info.get("installer_url") or not info.get("sums_url"):
        raise UpdateError("this release has no installer or checksum file yet")
    with _open(info["sums_url"], timeout) as r:
        expected = _expected_sha(r.read().decode("utf-8", "replace"))
    if not expected:
        raise UpdateError("the release checksum file does not list the installer")
    dest = Path(dest_dir or tempfile.mkdtemp(prefix="vd_update_"))
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / f"VideoDownloader-Setup-{info['version']}.exe"
    part = final.with_suffix(".part")
    sha, done = hashlib.sha256(), 0
    try:
        with _open(info["installer_url"], timeout) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.get("installer_size") or 0)
            while True:
                if cancelled and cancelled():
                    raise UpdateError("cancelled")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if info.get("installer_size") and done != int(info["installer_size"]):
            raise UpdateError("the download is incomplete")
        if sha.hexdigest().lower() != expected:
            raise UpdateError("the download is corrupted (checksum mismatch), so it was not installed")
        os.replace(part, final)
        return final
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def launch_installer(path: Path) -> bool:
    """Start the downloaded installer (UAC prompts only if the install needs it). The app should quit right after."""
    import ctypes
    args = "/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /UPDATED=1"
    return ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), args, None, 1) > 32
