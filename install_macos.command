#!/bin/bash
# FileConvert - add "Convert To…" to the Finder right-click menu (runs from this folder).
# Double-click this file, or run:  bash install_macos.command
# Optional: bash install_macos.command --all-files   (show it on every file, e.g. for .mkv)
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed. Install it from https://www.python.org/downloads/ or: brew install python"
  read -r -p "Press Return to close."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Setting up..."
  python3 -m venv .venv || exit 1
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt || exit 1
.venv/bin/python launcher.py install "$@"
echo
echo "Done. Keep this folder where it is - the menu runs from here."
read -r -p "Press Return to close."
