"""Shim -> scadavt.apps.water.app.epanet_standalone (moved). Forwards 'python -m'."""
import sys as _sys
from scadavt.apps.water.app import epanet_standalone as _m
_sys.modules[__name__] = _m
if __name__ == "__main__":
    _m.main()
