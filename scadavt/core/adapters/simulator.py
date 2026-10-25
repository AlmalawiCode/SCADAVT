"""Simulator adapter — an industrial process co-simulation behind the Adapter
contract. Domain-neutral core; a specific engine (EPANET, energy, …) plugs in by
``engine`` name. For M4 the built-in ``process`` engine is a small stateful tank
(a level that responds to a pump command), enough to show genuine co-simulation
and actuator feedback. The EPANET engine is added as an overlay in M5.

    {"id": "sim1", "kind": "simulator", "engine": "process",
     "model": {"level0": 5.0, "fill": 0.5, "drain": 0.2, "max": 10.0}}
"""

from __future__ import annotations

import time

from .base import Adapter, Sample, QUALITY_OK, AdapterError


class _TankProcess:
    """Minimal co-sim: level integrates (fill if pump on) - drain, clamped."""

    def __init__(self, model: dict):
        self.level = float(model.get("level0", 5.0))
        self.fill = float(model.get("fill", 0.5))
        self.drain = float(model.get("drain", 0.2))
        self.max = float(model.get("max", 10.0))
        self.pump = 0.0
        self._t = time.monotonic()

    def _advance(self) -> None:
        now = time.monotonic()
        dt = now - self._t
        self._t = now
        rate = (self.fill if self.pump >= 0.5 else 0.0) - self.drain
        self.level = max(0.0, min(self.max, self.level + rate * dt))

    def read(self, var: str) -> float:
        self._advance()
        if var in ("level", "pv"):
            return self.level
        if var == "pump":
            return self.pump
        raise KeyError(var)

    def write(self, var: str, value: float) -> None:
        if var == "pump":
            self.pump = float(value)
        else:
            raise KeyError(var)


_ENGINES = {"process": _TankProcess}


class SimulatorAdapter(Adapter):
    kind = "simulator"

    def __init__(self, spec: dict | None = None):
        super().__init__(spec)
        self._proc = None

    def open(self) -> None:
        engine = self.spec.get("engine", "process")
        factory = _ENGINES.get(engine)
        if factory is None:
            raise AdapterError(f"unknown simulator engine {engine!r}")
        self._proc = factory(self.spec.get("model", {}))
        self._open = True

    def close(self) -> None:
        self._proc = None
        self._open = False

    def read(self, var: str) -> Sample:
        return Sample(float(self._proc.read(var)), QUALITY_OK, time.monotonic())

    def write(self, var: str, value: float) -> None:
        self._proc.write(var, float(value))

    @classmethod
    def register_engine(cls, name: str, factory) -> None:
        """Register an external process engine (e.g. EPANET in M5)."""
        _ENGINES[name] = factory
