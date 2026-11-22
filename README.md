<div align="center">

# Video Downloader

**Save videos from X (Twitter), TikTok and Instagram Reels on Windows. No setup, no command line.**

<a href="https://github.com/astroazoz/video-uploader-v1/releases/latest/download/VideoDownloader-Setup.exe"><img src="https://img.shields.io/badge/%E2%AC%87%20Download%20for%20Windows-VideoDownloader--Setup.exe-2ea44f?style=for-the-badge&logo=windows&logoColor=white" alt="Download for Windows" height="52"></a>

[Latest release](https://github.com/astroazoz/video-uploader-v1/releases/latest) · Windows 10 / 11, 64-bit · Free

</div>

## Install in 3 steps
1. **Click the green Download button above.** (`VideoDownloader-Setup.exe`)
2. **Open the file** and click **Next, Next, Install**. You can pick the install folder on the way.
3. **Open Video Downloader** from your desktop or Start Menu, paste a link, press Download.

Everything the app needs (Python, FFmpeg, the downloader engine) is already inside the installer.
You do not need to install anything else, and you never need a command window.

> **Do not download "Source code" or the repository ZIP, and do not use the green *Code* button.** That is the
> programmer's copy and it asks for Python ("Python was not found..."). Use the green **Download** button above, or
> if you already have the repository folder, just **double-click `INSTALL.bat`**: it fetches and starts the installer
> for you (no Python needed).

> **"Windows protected your PC"?** The installer is not code-signed yet, so Windows SmartScreen may show a blue
> screen the first time. Click **More info**, then **Run anyway**. The file is built by GitHub from this
> repository's source, and its SHA-256 is published next to it as `SHA256SUMS.txt` on the release page.

## Updates, uninstall, your data
- **Updates:** the app checks GitHub for a newer version a few seconds after it starts (at most every 6 hours) and
  asks you first. Nothing is installed without your click. You can also press **Check for updates** in the
  menu or `Ctrl+U`. Updating keeps your settings, library and connected accounts.
- **Uninstall:** Windows **Settings > Apps > Video Downloader > Uninstall**. Your downloaded videos are never
  deleted. The uninstaller only offers to remove the app's own saved data.
- **Where your data lives:** `%APPDATA%\VideoDownloader` (library list, settings, thumbnails, encrypted account
  sessions). It is separate from the program folder, which is why updates never touch it.

Only use the app for videos you own or have permission to save.

## Features
- Paste a link: platform is detected and a preview (thumbnail, title, length, stats) is shown first
- Choose quality and format (MP4 H.264, MP4 original codec, or MP3 audio)
- Photo posts from X are saved at original quality (a folder when there are several) and open in a photo viewer
- Built-in library (grid or list), in-app player, hide videos, open folder / copy link / delete to Recycle Bin
- Optional connected accounts (TikTok, Instagram, X) so posts your own account may view can be saved

## Connected accounts and privacy
You sign in on each platform's own login page inside the app. The app never sees your password.
Only the session cookie is kept, encrypted with Windows DPAPI for your user account (`accounts.py`).
Disconnect deletes it. The app does not bypass privacy settings, and some private or
download-restricted posts cannot be saved even if you can watch them.
The only network traffic besides the downloads you start is the update check to `api.github.com`
(turn it off by setting `"update_auto": false` in `%APPDATA%\VideoDownloader\config.json`).

---

## For developers

### Build from source
Requires Windows and Python 3.10+ (3.12 is what it is tested with). Run this from the project folder, in any shell:

    python build.py

The app is written to `dist\VideoDownloader\VideoDownloader.exe` (keep the whole folder together).
Useful options: `--installer` also builds `installer_output\VideoDownloader-Setup.exe` and `SHA256SUMS.txt`
(needs Inno Setup 6: `winget install JRSoftware.InnoSetup`), `--version 3.1.0` stamps a version,
`--no-pip` skips installing the dependencies, `--no-prune` keeps every Qt file.
Every build ends with the app's own self-check (`VideoDownloader.exe --selfcheck result.json`), which verifies the
downloader engine, FFmpeg encoders, Qt modules, encryption and video decoding.
Rebuild now and then to pick up the newest yt-dlp when a site changes.

`dev\build.bat` and `dev\build_installer.bat` are developer-only shortcuts for `python build.py` and
`python build.py --installer`; using `python build.py` directly behaves the same in every shell.

To run without building: `pip install -r requirements.txt`, then `python app.py`.

### Publishing a release
Releases are fully automatic. Push a version tag and GitHub Actions (`.github/workflows/release.yml`) builds the
installer, tests that it installs, runs and uninstalls, and publishes it with `SHA256SUMS.txt` to GitHub Releases:

    git tag v3.1.0
    git push origin v3.1.0

The version comes from the tag (the workflow rewrites `version.py`). The asset names `VideoDownloader-Setup.exe` and
`SHA256SUMS.txt` are fixed: the README download link and the in-app updater depend on them.

### Installer
Built with [Inno Setup](https://jrsoftware.org/isinfo.php) from `installer.iss`: installs for the current user (no admin
prompt, no questions about install mode), lets you choose the folder, adds Start Menu and optional Desktop shortcuts, closes a
running copy before updating, replaces program files cleanly on update, and registers an uninstaller.
`prune.py` removes Qt files the app provably never loads (unreachable DLLs, devtools, translations) to keep the installer small.

### Code layout
- `app.py` entry point
- `version.py` the single app version
- `core.py` link handling, yt-dlp fetch and download tasks, library storage (no UI)
- `accounts.py` encrypted account sessions and cookie files
- `updater.py` GitHub Releases update check, verified download (SHA-256, GitHub hosts only)
- `selfcheck.py` headless self-test of an installed copy
- `hud.py` the HUD design system (menu, bracketed panels, buttons, terminal log, segmented progress, theme)
- `glass.py` shared drawing helpers and the icon set used by `hud.py`
- `ui.py` the main window and dialogs
- `login_dialog.py` the embedded sign-in window
- `INSTALL.bat` end-user helper: downloads and starts the latest installer (no Python)
- `build.py`, `prune.py`, `installer.iss`, `requirements.txt`, `dev/` packaging
