# video-downloader

Windows desktop app (Python + PySide6 + yt-dlp) to preview, download and organise videos from
X (Twitter), TikTok and Instagram Reels, with a built-in library and player.

Only use it for videos you own or have permission to save.

## Features
- Paste a link: platform is detected and a preview (thumbnail, title, length, stats) is shown first
- Choose quality and format (MP4 H.264, MP4 original codec, or MP3 audio)
- Photo posts from X are saved at original quality (a folder when there are several) and open in a photo viewer
- Built-in library (grid or list), in-app player, open folder / copy link / delete to Recycle Bin
- Optional connected accounts (TikTok, Instagram, X) so posts your own account may view can be saved

## Connected accounts and privacy
You sign in on each platform's own login page inside the app. The app never sees your password.
Only the session cookie is kept, encrypted with Windows DPAPI for your user account (`accounts.py`).
Disconnect deletes it. The app does not bypass privacy settings, and some private or
download-restricted posts cannot be saved even if you can watch them.

## Just want to use it?
Download `VideoDownloader-Setup-<version>.exe` from the repository's **Releases** page and run it. No Python needed.
(Windows may show "Windows protected your PC" because the installer is unsigned: click **More info**, then **Run anyway**.)

## Build from source
Requires Windows and Python 3.10+ (3.12 is what it is tested with). Run this from the project folder, in any shell:

    python build.py

The app is written to `dist\VideoDownloader\VideoDownloader.exe` (keep the whole folder together).
Useful options: `--installer` also builds the Setup.exe (needs Inno Setup 6: `winget install JRSoftware.InnoSetup`),
`--no-pip` skips installing/upgrading the dependencies. Rebuild now and then to pick up the newest yt-dlp when a site changes.

`build.bat` and `build_installer.bat` are only double-click shortcuts for `python build.py` and `python build.py --installer`.
If a terminal says `build.bat` is "not recognized": PowerShell needs `.\build.bat`, Git Bash needs `cmd //c build.bat`
(a single slash gets rewritten), or simply use `python build.py`, which behaves the same everywhere.

To run without building: `pip install PySide6 "yt-dlp[curl-cffi]" curl_cffi imageio-ffmpeg`, then `python app.py`.

## Code layout
- `app.py` entry point
- `core.py` link handling, yt-dlp fetch and download tasks, library storage (no UI)
- `accounts.py` encrypted account sessions and cookie files
- `glass.py` the frosted-glass design system (panels, buttons, progress dial, icons, theme)
- `ui.py` the main window and dialogs
- `login_dialog.py` the embedded sign-in window

## Installer
`python build.py --installer` (or `build_installer.bat`) builds the app and packs it into a single `installer_output\VideoDownloader-Setup-<version>.exe`
with [Inno Setup](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`). The installer installs for the
current user (no admin prompt), adds Start Menu and optional Desktop shortcuts, and registers an uninstaller. Uninstalling never
deletes your downloaded videos; it only offers to remove the app's own library list, settings and saved account sessions.
