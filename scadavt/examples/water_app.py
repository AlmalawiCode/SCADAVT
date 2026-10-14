"""Shim -> scadavt.apps.water.app.water_app (moved to the Water application package)."""
import sys as _sys
from scadavt.apps.water.app import water_app as _m
_sys.modules[__name__] = _m
