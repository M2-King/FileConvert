"""Entry point used by the right-click menus and by PyInstaller."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fileconvert.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
