"""Simulated adapter — software sensors and actuators for experiments and CI.

Domain-neutral: a variable is a named signal. Sensors are generated from a simple
waveform spec (constant / ramp / sine / random / square); actuators record the commands
written to them (a simulated actuator "responding to register commands").

    {"id": "sim1", "kind": "simulated", "spec": {
        "temp": {"wave": "sine", "min": 18, "max": 26, "period": 60},
        "humidity": {"wave": "constant", "value": 45},
        "valve": {"actuator": true}}}
"""

from __future__ import annotations

import math
import random
import time

from .base import Adapter, Sample, QUALITY_OK


class SimulatedAdapter(Adapter):
    kind = "simulated"

    def __init__(self, spec: dict | None = None):
        super().__init__(spec)
        self._t0 = time.monotonic()
        self._outputs: dict[str, tuple[float, float]] = {}   # var -> (value, ts)
        self._rng = random.Random(self.spec.get("seed", 0))

    def start(self) -> None:
        super().start()
        self._t0 = time.monotonic()

    def _signal(self, cfg: dict) -> float:
        wave = cfg.get("wave", "constant")
        t = time.monotonic() - self._t0
        lo, hi = float(cfg.get("min", 0.0)), float(cfg.get("max", 1.0))
        if wave == "constant":
            return float(cfg.get("value", lo))
        if wave == "ramp":
            period = float(cfg.get("period", 60.0))
            return lo + (hi - lo) * ((t % period) / period)
        if wave == "sine":
            period = float(cfg.get("period", 60.0))
            return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))
        if wave == "random":
            return self._rng.uniform(lo, hi)
        if wave == "square":                     # on/off switch: hi for `duty` of each period
            period = float(cfg.get("period", 60.0))
            return hi if (t % period) < float(cfg.get("duty", 0.5)) * period else lo
        return float(cfg.get("value", lo))

    def read(self, var: str) -> Sample:
        cfg = self.spec.get("spec", {}).get(var)
        if cfg is None or cfg.get("actuator"):
            # an actuator var reads back its last command (or 0 if never commanded)
            if var in self._outputs:
                v, ts = self._outputs[var]
                return Sample(v, QUALITY_OK, ts)
            if cfg is None:
                raise KeyError(f"simulated adapter has no variable {var!r}")
            return Sample(0.0, QUALITY_OK, time.monotonic())
        return Sample(self._signal(cfg), QUALITY_OK, time.monotonic())

    def write(self, var: str, value: float) -> None:
        self._outputs[var] = (float(value), time.monotonic())

    def output(self, var: str):
        """Last command a simulated actuator received (for inspection/tests)."""
        return self._outputs.get(var, (None, None))[0]

    def supports(self, var: str, direction: str) -> bool:
        cfg = self.spec.get("spec", {}).get(var, {})
        if direction == "out":
            return bool(cfg.get("actuator")) or var not in self.spec.get("spec", {})
        return True
