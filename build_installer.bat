@echo off
rem Builds the app, then packs it into a single Setup.exe with Inno Setup (https://jrsoftware.org/isinfo.php).
rem Output: installer_output\VideoDownloader-Setup-<version>.exe
call build.bat nopause
if errorlevel 1 exit /b 1
set ISCC=
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if exist %%P set ISCC=%%~P
if "%ISCC%"=="" (
  echo Inno Setup 6 was not found. Install it with:  winget install JRSoftware.InnoSetup
  exit /b 1
)
"%ISCC%" installer.iss
echo.
echo Done. Your installer is in the installer_output folder.
pause
