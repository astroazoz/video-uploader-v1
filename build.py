"""Build Video Downloader. Works the same from any shell (PowerShell, cmd, Git Bash, VS Code terminal, CI).

    python build.py                       build the app          -> dist/VideoDownloader/VideoDownloader.exe
    python build.py --installer           ...and the installer   -> installer_output/VideoDownloader-Setup.exe
    python build.py --version 3.1.0       stamp this version into the app and the installer (CI passes the tag)
    python build.py --no-pip              skip installing the Python dependencies
    python build.py --no-prune            keep every Qt file (bigger, for debugging)

Requires Windows and Python 3.10+ (3.12 is what it is tested with). The installer step needs Inno Setup 6
(winget install JRSoftware.InnoSetup).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INSTALLER_NAME = "VideoDownloader-Setup"  # fixed name: the README link and the updater rely on it


def run(cmd: list, **kw) -> None:
    print("> " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True, **kw)


def find_iscc() -> str | None:
    env = os.environ
    for base in (env.get("LOCALAPPDATA", ""), env.get("ProgramFiles(x86)", ""), env.get("ProgramFiles", "")):
        for sub in ("Programs\\Inno Setup 6", "Inno Setup 6"):
            p = Path(base) / sub / "ISCC.exe"
            if base and p.exists():
                return str(p)
    return shutil.which("ISCC")


def read_version() -> str:
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (ROOT / "version.py").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def write_version(v: str) -> None:
    (ROOT / "version.py").write_text(
        '"""Single source of truth for the app version.\n\nThe release workflow rewrites this file from the git tag '
        '(tag v3.1.0 -> VERSION = "3.1.0"),\nso publishing a new version is just: push a tag.\n"""\n\n'
        f'VERSION = "{v}"\n', encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def smoke_test(exe: Path) -> bool:
    """Run the built app's own self-check (engine, ffmpeg, Qt, encryption, video decoding) with no Python on PATH."""
    out = Path(tempfile.mkdtemp(prefix="vd_build_")) / "selfcheck.json"
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    r = subprocess.run([str(exe), "--selfcheck", str(out)], cwd=exe.parent, env=env, timeout=300)
    if not out.exists():
        print("Smoke test FAILED: the app did not produce a self-check report.")
        return False
    res = json.loads(out.read_text(encoding="utf-8"))
    for name, c in res["checks"].items():
        print(f"  {'ok  ' if c['ok'] else 'FAIL'} {name}: {c['detail']}")
    return r.returncode == 0 and res.get("ok", False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build Video Downloader (and optionally its installer).")
    ap.add_argument("--installer", action="store_true", help="also build installer_output/VideoDownloader-Setup.exe")
    ap.add_argument("--version", help="version to stamp into the app, e.g. 3.1.0 (a leading v is ignored)")
    ap.add_argument("--no-pip", action="store_true", help="skip installing/upgrading the Python dependencies")
    ap.add_argument("--no-prune", action="store_true", help="do not remove unused Qt files")
    ap.add_argument("--no-smoke", action="store_true", help="skip the post-build self-check")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("This app and its build are for Windows.")
        return 1
    if sys.version_info < (3, 10):
        print(f"Python 3.10 or newer is required (found {sys.version.split()[0]}). Install it from python.org.")
        return 1

    if args.version:
        v = args.version.lstrip("vV")
        if not re.fullmatch(r"\d+(\.\d+){1,3}", v):
            print(f"Bad version '{args.version}'. Use something like 3.1.0")
            return 1
        write_version(v)
    version = read_version()
    print(f"Building Video Downloader {version}")

    py = sys.executable
    if not args.no_pip:
        run([py, "-m", "pip", "install", "-U", "-q", "-r", "requirements.txt"])
    run([py, "app.py", "--make-icon", "icon.ico"])
    shutil.rmtree(ROOT / "dist" / "VideoDownloader", ignore_errors=True)
    run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--name", "VideoDownloader",
         "--icon", "icon.ico", "--collect-all", "imageio_ffmpeg", "--collect-all", "yt_dlp",
         "--collect-all", "curl_cffi", "--collect-all", "certifi", "--hidden-import", "_cffi_backend",
         "--exclude-module", "tkinter", "--exclude-module", "customtkinter", "app.py"])
    app_dir = ROOT / "dist" / "VideoDownloader"
    exe = app_dir / "VideoDownloader.exe"
    if not exe.exists():
        print("Build finished but VideoDownloader.exe was not found. Check the messages above.")
        return 1

    if not args.no_prune:
        from prune import prune
        prune(app_dir)
    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print(f"\nApp built: {exe}  ({size / 1048576:.0f} MB; keep the whole VideoDownloader folder together)")

    if not args.no_smoke:
        print("\nSelf-check of the built app:")
        if not smoke_test(exe):
            print("\nThe built app failed its self-check, so no installer was made.")
            return 1

    if args.installer:
        iscc = find_iscc()
        if not iscc:
            print("\nInno Setup 6 was not found. Install it with:  winget install JRSoftware.InnoSetup\nthen run this again.")
            return 1
        out = ROOT / "installer_output"
        out.mkdir(exist_ok=True)
        for old in out.glob("*"):
            old.unlink()
        run([iscc, "/Qp", f"/DAppVersion={version}", "installer.iss"])
        setup = out / f"{INSTALLER_NAME}.exe"
        if not setup.exists():
            print("Inno Setup finished but the installer was not found.")
            return 1
        digest = sha256(setup)
        (out / "SHA256SUMS.txt").write_text(f"{digest}  {setup.name}\n", encoding="utf-8")
        print(f"\nInstaller built: {setup}  ({setup.stat().st_size / 1048576:.0f} MB)\nSHA-256: {digest}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"\nBuild step failed (exit code {e.returncode}): {' '.join(map(str, e.cmd))}")
        sys.exit(e.returncode or 1)
