"""Shim -> scadavt.apps.water.generate (the EPANET .inp writer moved to the Water
application package). Kept so old imports keep working."""
import sys as _sys
from scadavt.apps.water import generate as _m
_sys.modules[__name__] = _m
