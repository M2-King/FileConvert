#!/bin/bash
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
"$PY" launcher.py uninstall
read -r -p "Press Return to close."
