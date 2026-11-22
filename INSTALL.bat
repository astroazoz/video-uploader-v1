@echo off
title Video Downloader - Installer
echo.
echo  Video Downloader
echo  ----------------
echo  Downloading the latest installer (about 140 MB). No Python or anything else is needed.
echo  Please wait...
echo.
set "VD_DIR=%TEMP%\VideoDownloaderSetup"
if not exist "%VD_DIR%" mkdir "%VD_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; $b='https://github.com/astroazoz/video-uploader-v1/releases/latest/download/'; $d=$env:VD_DIR; $f=$d+'\VideoDownloader-Setup.exe'; try { $sf=$d+'\SHA256SUMS.txt'; Invoke-WebRequest ($b+'SHA256SUMS.txt') -OutFile $sf; $sums=Get-Content $sf -Raw; Invoke-WebRequest ($b+'VideoDownloader-Setup.exe') -OutFile $f; $want=([regex]::Match($sums,'[0-9a-fA-F]{64}')).Value.ToLower(); $got=(Get-FileHash $f -Algorithm SHA256).Hash.ToLower(); if ($want -ne $got) { throw 'The download is damaged. Please run this again.' } } catch { Write-Host ('Could not download: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }"
if errorlevel 1 goto failed
echo  Starting the installer...
start "" "%VD_DIR%\VideoDownloader-Setup.exe"
exit /b 0

:failed
echo.
echo  Download failed. Check your internet connection and try again,
echo  or open https://github.com/astroazoz/video-uploader-v1/releases/latest and click VideoDownloader-Setup.exe
echo.
pause
exit /b 1
