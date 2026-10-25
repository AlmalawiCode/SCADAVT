"""Adapter registry — build an adapter from its config ``kind``."""

from __future__ import annotations

from .base import Adapter, AdapterError
from .hardware import HardwareAdapter
from .simulated import SimulatedAdapter
from .simulator import SimulatorAdapter

_KINDS = {
    "simulated": SimulatedAdapter,
    "hardware": HardwareAdapter,
    "simulator": SimulatorAdapter,
}

KINDS = tuple(_KINDS)


def make_adapter(spec: dict) -> Adapter:
    # optional plugin module to import first (e.g. a domain overlay registering a
    # simulator engine or a new adapter kind) — keeps the core domain-neutral.
    plugin = spec.get("plugin")
    if plugin:
        import importlib
        importlib.import_module(plugin)
    kind = spec.get("kind")
    cls = _KINDS.get(kind)
    if cls is None:
        raise AdapterError(f"unknown adapter kind {kind!r} (one of {', '.join(KINDS)})")
    return cls(spec)


def register_kind(name: str, cls) -> None:
    _KINDS[name] = cls
