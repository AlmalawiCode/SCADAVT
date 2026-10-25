"""Adapter base contract (register milestone M4).

An **Adapter** is the only code that knows about a physical process. It supplies
register values (``in``) and consumes register commands (``out``) behind one
contract, so the generic PLC never changes (requirement G6). Three kinds —
simulated / hardware / simulator — implement this ABC and are chosen purely by
configuration.

A :class:`Sample` carries not just a value but its **quality** and a
**timestamp**: a successful transport read does not mean the measurement is
fresh, so staleness/fail-safe is decided from the sample's age, not from whether
the read succeeded.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

QUALITY_OK = "OK"
QUALITY_STALE = "STALE"
QUALITY_FAILED = "FAILED"


class AdapterError(RuntimeError):
    pass


@dataclass
class Sample:
    value: float
    quality: str = QUALITY_OK
    ts: float = field(default_factory=time.monotonic)   # when the value was produced

    def age(self, now: float | None = None) -> float:
        return (time.monotonic() if now is None else now) - self.ts


class Adapter(ABC):
    """Lifecycle: ``configure`` -> ``open`` -> ``start`` -> per-cycle
    ``read``/``write`` -> ``stop`` -> ``close``. A context manager; ``close`` is
    idempotent and runs on any exception; a failing adapter must never crash the
    PLC (callers treat a raised read/write as a FAILED sample)."""

    kind = "base"

    def __init__(self, spec: dict | None = None):
        self.spec = dict(spec or {})
        self.id = self.spec.get("id", self.kind)
        self._open = False
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    def configure(self, spec: dict) -> None:
        self.spec = dict(spec)
        self.id = self.spec.get("id", self.kind)

    def open(self) -> None:
        self._open = True

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def close(self) -> None:
        self._open = False                       # idempotent

    @property
    def is_open(self) -> bool:
        return self._open

    def __enter__(self):
        self.open()
        self.start()
        return self

    def __exit__(self, *exc):
        try:
            self.stop()
        finally:
            self.close()                         # always release, even on error

    # -- data --------------------------------------------------------------
    def tick(self) -> None:
        """Advance the adapter's process one cycle (co-sim step). Default no-op;
        a simulator advances its engine here so reads see a consistent state."""

    @abstractmethod
    def read(self, var: str) -> Sample:
        """Produce a measurement for ``var`` (``in`` direction)."""

    def write(self, var: str, value: float) -> None:
        """Consume a command for ``var`` (``out`` direction). Default: no-op."""
        raise AdapterError(f"{self.kind} adapter does not support writing {var!r}")

    def supports(self, var: str, direction: str) -> bool:
        return True
