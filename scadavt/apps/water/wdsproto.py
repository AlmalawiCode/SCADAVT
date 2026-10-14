"""Shim -> scadavt.protocols.wdsproto.

The process-variable type registry is GENERIC infrastructure (used by the control
rule engine, the integration legacy codec, and the scenario loader), so it lives
in ``scadavt.protocols``. This alias keeps the water import path working.
"""
import sys as _sys
from scadavt.protocols import wdsproto as _m
_sys.modules[__name__] = _m
