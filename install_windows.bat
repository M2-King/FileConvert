@echo off
rem FileConvert - add "Convert to" to the Windows right-click menu (runs from this folder).
rem Optional: install_windows.bat --classic-menu   (Windows 11: skip "Show more options")
setlocal
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python 3 isn't installed.
  echo Get it from https://www.python.org/downloads/  and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Setting up...
  %PY% -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :fail
".venv\Scripts\python.exe" launcher.py install %* || goto :fail
echo.
echo Done. Keep this folder where it is - the menu runs from here.
pause
exit /b 0

:fail
echo.
echo Something went wrong (see the messages above).
pause
exit /b 1
