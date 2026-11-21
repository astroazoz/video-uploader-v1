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

## Build
Requires Python 3.12 and ffmpeg is bundled via `imageio-ffmpeg`.

    build.bat

The app is written to `dist\VideoDownloader\VideoDownloader.exe` (keep the whole folder together).
Run from source with `python app.py` after `pip install PySide6 "yt-dlp[curl-cffi]" curl_cffi imageio-ffmpeg`.
Rebuild regularly to pick up the newest yt-dlp when a site changes.

## Code layout
- `app.py` entry point
- `core.py` link handling, yt-dlp fetch and download tasks, library storage (no UI)
- `accounts.py` encrypted account sessions and cookie files
- `glass.py` the frosted-glass design system (panels, buttons, progress dial, icons, theme)
- `ui.py` the main window and dialogs
- `login_dialog.py` the embedded sign-in window
