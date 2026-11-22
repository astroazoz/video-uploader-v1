@echo off
rem DEVELOPERS ONLY. Same as: python build.py   (needs Python; end users should use INSTALL.bat or the Releases page)
python "%~dp0..\build.py" %*
if errorlevel 1 echo Build failed. See the messages above.
pause
