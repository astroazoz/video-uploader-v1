"""Build Video Downloader. Works the same from any shell (PowerShell, cmd, Git Bash, VS Code terminal).

    python build.py                 build the app   -> dist/VideoDownloader/VideoDownloader.exe
    python build.py --installer     build the app, then a single Setup.exe (needs Inno Setup 6)
    python build.py --no-pip        skip the dependency install/upgrade step

Requires Windows and Python 3.10 or newer (3.12 is what it is tested with).
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPS = ["PySide6", "yt-dlp[curl-cffi]", "curl_cffi", "imageio-ffmpeg", "pyinstaller"]


def run(cmd: list[str]) -> None:
    print("> " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True)


def find_iscc() -> str | None:
    env = os.environ
    for base in (env.get("LOCALAPPDATA", ""), env.get("ProgramFiles(x86)", ""), env.get("ProgramFiles", "")):
        for sub in ("Programs\\Inno Setup 6", "Inno Setup 6"):
            p = Path(base) / sub / "ISCC.exe"
            if base and p.exists():
                return str(p)
    return shutil.which("ISCC")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build Video Downloader (and optionally its installer).")
    ap.add_argument("--installer", action="store_true", help="also build installer_output/VideoDownloader-Setup-<version>.exe")
    ap.add_argument("--no-pip", action="store_true", help="skip installing/upgrading the Python dependencies")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("This app and its build are for Windows.")
        return 1
    if sys.version_info < (3, 10):
        print(f"Python 3.10 or newer is required (found {sys.version.split()[0]}). Install it from python.org.")
        return 1

    py = sys.executable
    if not args.no_pip:
        run([py, "-m", "pip", "install", "-U", "-q", *DEPS])
    run([py, "app.py", "--make-icon", "icon.ico"])
    run([py, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--name", "VideoDownloader",
         "--icon", "icon.ico", "--collect-all", "imageio_ffmpeg", "--collect-all", "yt_dlp",
         "--collect-all", "curl_cffi", "--collect-all", "certifi", "--hidden-import", "_cffi_backend",
         "--exclude-module", "tkinter", "--exclude-module", "customtkinter", "app.py"])
    exe = ROOT / "dist" / "VideoDownloader" / "VideoDownloader.exe"
    if not exe.exists():
        print("Build finished but VideoDownloader.exe was not found. Check the messages above.")
        return 1
    print(f"\nApp built: {exe}  (keep the whole VideoDownloader folder together)")

    if args.installer:
        iscc = find_iscc()
        if not iscc:
            print("\nInno Setup 6 was not found. Install it with:  winget install JRSoftware.InnoSetup\nthen run this again.")
            return 1
        run([iscc, "installer.iss"])
        print(f"\nInstaller built in: {ROOT / 'installer_output'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"\nBuild step failed (exit code {e.returncode}): {' '.join(map(str, e.cmd))}")
        sys.exit(e.returncode or 1)
