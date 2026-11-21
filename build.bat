@echo off
rem Same as: python build.py   (works from any shell; this file is only a shortcut)
python "%~dp0build.py" %*
if errorlevel 1 echo Build failed. See the messages above.
pause
