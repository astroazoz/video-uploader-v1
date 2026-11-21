"""Video Downloader entry point.

Layout of the code:
  core.py          link handling, yt-dlp fetch/download tasks, library storage (no UI)
  accounts.py      encrypted account sessions and cookie files
  glass.py         the frosted-glass design system (panels, buttons, dial, icons, theme)
  ui.py            the windows and dialogs
  login_dialog.py  the embedded sign-in window

For videos you are authorized to save (your own content, or content you have permission to download).
"""

from ui import main

if __name__ == "__main__":
    main()
