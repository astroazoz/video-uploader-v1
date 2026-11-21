@echo off
rem Same as: python build.py --installer   (needs Inno Setup 6: winget install JRSoftware.InnoSetup)
python "%~dp0build.py" --installer
if errorlevel 1 echo Build failed. See the messages above.
pause
