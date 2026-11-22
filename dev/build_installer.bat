@echo off
rem DEVELOPERS ONLY. Same as: python build.py --installer   (needs Python and Inno Setup 6)
python "%~dp0..\build.py" --installer
if errorlevel 1 echo Build failed. See the messages above.
pause
