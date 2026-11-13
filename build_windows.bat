@echo off
rem Builds a standalone dist\FileConvert\FileConvert.exe (no Python needed on the target PC).
rem To bundle ffmpeg, put ffmpeg.exe in the bin\ folder first.
setlocal
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv || goto :fail
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt pyinstaller || goto :fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name FileConvert ^
    --icon assets\icon.ico --collect-all pillow_heif --collect-submodules fileconvert --collect-submodules comtypes launcher.py || goto :fail
if exist bin\ffmpeg.exe (
  if not exist dist\FileConvert\bin mkdir dist\FileConvert\bin
  copy /y bin\ffmpeg.exe dist\FileConvert\bin\ >nul
)
echo.
echo Built: dist\FileConvert\FileConvert.exe
echo Double-click it and choose Yes to add the right-click menu.
echo To share it, zip the whole dist\FileConvert folder.
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
