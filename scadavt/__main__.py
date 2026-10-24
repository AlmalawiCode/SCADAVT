"""`python -m scadavt` — one of the two CLI entry points. Both this and the
`scadavt` console-script call scadavt.cli:main, so there is a single
implementation behind them (R11)."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
