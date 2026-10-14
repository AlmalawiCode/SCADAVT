"""Shim -> scadavt.apps.water.wdsproto (the water process-variable types moved
to the Water application package). Kept so old imports keep working."""
import sys as _sys
from scadavt.apps.water import wdsproto as _m
_sys.modules[__name__] = _m
