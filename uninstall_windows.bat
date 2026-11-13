@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" launcher.py uninstall
) else (
  python launcher.py uninstall
)
pause
