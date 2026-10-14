"""Shim -> scadavt.apps.water.app.app_runner (moved). Forwards 'python -m'."""
import sys as _sys
from scadavt.apps.water.app import app_runner as _m
_sys.modules[__name__] = _m
if __name__ == "__main__":
    _m.main()
