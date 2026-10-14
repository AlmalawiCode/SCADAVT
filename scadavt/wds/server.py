"""Shim -> scadavt.apps.water.wds.server (moved)."""
import sys as _sys
from scadavt.apps.water.wds import server as _m
_sys.modules[__name__] = _m

if __name__ == "__main__":      # support `python -m scadavt.wds.server`
    _m.main()
