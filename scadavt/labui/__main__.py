"""`python -m scadavt.labui` -> serve the builder; anything else -> the CLI."""
import sys

from .cli import main

if __name__ == "__main__":
    main(sys.argv[1:] or ["serve"])
