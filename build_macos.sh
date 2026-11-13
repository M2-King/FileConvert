#!/bin/bash
# Builds a standalone dist/FileConvert/FileConvert (no Python needed on the target Mac).
# To bundle ffmpeg, put an ffmpeg binary in the bin/ folder first.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet -r requirements.txt pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --clean --name FileConvert \
    --collect-all pillow_heif --collect-submodules fileconvert \
    --add-data "macos:macos" launcher.py
if [ -x bin/ffmpeg ]; then
  mkdir -p dist/FileConvert/bin && cp bin/ffmpeg dist/FileConvert/bin/
fi
echo
echo "Built: dist/FileConvert/FileConvert"
echo "Add the menu with:  dist/FileConvert/FileConvert install"
