"""Pluggable Adapter/Driver layer (register milestone M4).

Three interchangeable integration kinds behind one :class:`Adapter` contract —
``simulated`` software devices, ``hardware`` real I/O (gated, mock in tests), and
``simulator`` industrial co-simulation — selected purely by configuration. The
generic PLC never changes; adapters supply/consume register values via the I/O
mapping (:mod:`scadavt.core.iosync`), with data quality/timestamps,
staleness/fail-safe behaviour, and deterministic write ownership.
"""

from .base import Adapter, AdapterError, Sample, QUALITY_OK, QUALITY_STALE, QUALITY_FAILED
from .registry import make_adapter, register_kind, KINDS

__all__ = ["Adapter", "AdapterError", "Sample", "QUALITY_OK", "QUALITY_STALE",
           "QUALITY_FAILED", "make_adapter", "register_kind", "KINDS"]
