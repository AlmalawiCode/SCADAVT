"""Shim for `python -m scadavt.labui` -> the moved web lab CLI."""
import sys
from scadavt.apps.water.labui.cli import main
if __name__ == "__main__":
    main(sys.argv[1:] or ["serve"])
