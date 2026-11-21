@echo off
rem Rebuilds the app. Run this if you change the code or want the newest yt-dlp
rem (video sites change often, and a fresh yt-dlp fixes most "can't download" problems).
rem Output: dist\VideoDownloader\VideoDownloader.exe  (keep the whole folder together)
python -m pip install -U -q PySide6 "yt-dlp[curl-cffi]" curl_cffi imageio-ffmpeg pyinstaller
python app.py --make-icon icon.ico
python -m PyInstaller --noconfirm --onedir --windowed --name VideoDownloader --icon icon.ico ^
  --collect-all imageio_ffmpeg --collect-all yt_dlp --collect-all curl_cffi --collect-all certifi ^
  --hidden-import _cffi_backend --exclude-module tkinter --exclude-module customtkinter app.py
echo.
echo Done. Open dist\VideoDownloader\VideoDownloader.exe
if /i not "%~1"=="nopause" pause
