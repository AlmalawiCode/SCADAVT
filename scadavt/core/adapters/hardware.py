"""Hardware adapter — physical I/O via a device driver, explicitly gated.

A real hardware adapter opens **only** when both ``authorized: true`` (per
adapter) and the launch gate ``SCADAVT_ALLOW_HARDWARE=1`` are set; otherwise it
refuses. Every output variable must declare safe ``limits`` and writes are
clamped (out-of-range rejected). Automated tests use the ``mock`` driver, which
performs **no real I/O**, so real hardware is never actuated in CI.

    {"id": "hw1", "kind": "hardware", "driver": "mock",
     "inputs": {"temp": 21.5}, "limits": {"valve": {"min": 0, "max": 1}}}
"""

from __future__ import annotations

import os
import time

from .base import Adapter, Sample, AdapterError, QUALITY_OK


class MockHardwareDriver:
    """In-memory stand-in for a real driver: no serial/USB/GPIO, no real I/O.
    Returns configured input values and records (clamped) outputs."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.inputs = dict(spec.get("inputs", {}))
        self.outputs: dict[str, float] = {}

    def open(self): pass
    def close(self): pass

    def read(self, var: str) -> float:
        if var in self.inputs:
            return float(self.inputs[var])
        if var in self.outputs:
            return self.outputs[var]
        raise KeyError(var)

    def write(self, var: str, value: float) -> None:
        self.outputs[var] = float(value)


def _load_real_driver(name: str, spec: dict):
    # Real drivers (serial/USB/GPIO/field-Modbus gateways) would be registered
    # here. None are bundled; a real bus is out of scope for the emulator and is
    # reached only through the explicit gate above.
    raise AdapterError(f"no real hardware driver {name!r} is installed")


class HardwareAdapter(Adapter):
    kind = "hardware"

    def __init__(self, spec: dict | None = None):
        super().__init__(spec)
        self._drv = None

    def open(self) -> None:
        driver = self.spec.get("driver", "mock")
        if driver == "mock":
            self._drv = MockHardwareDriver(self.spec)
        else:
            authorized = bool(self.spec.get("authorized", False))
            gate = os.environ.get("SCADAVT_ALLOW_HARDWARE") == "1"
            if not (authorized and gate):
                raise AdapterError(
                    "real hardware adapter refused: requires authorized:true AND "
                    "SCADAVT_ALLOW_HARDWARE=1 (use driver 'mock' for tests)")
            self._drv = _load_real_driver(driver, self.spec)
        self._drv.open()
        self._open = True

    def close(self) -> None:
        if self._drv is not None:
            try:
                self._drv.close()
            finally:
                self._drv = None
        self._open = False

    def _limits(self, var: str):
        lim = self.spec.get("limits", {})
        return lim.get(var) if isinstance(lim.get(var), dict) else lim if "min" in lim else None

    def read(self, var: str) -> Sample:
        return Sample(float(self._drv.read(var)), QUALITY_OK, time.monotonic())

    def write(self, var: str, value: float) -> None:
        value = float(value)
        lim = self._limits(var)
        if lim is not None:
            lo, hi = float(lim.get("min", value)), float(lim.get("max", value))
            if value < lo or value > hi:
                raise AdapterError(f"{var}={value} outside safe limits [{lo},{hi}]")
        elif self.spec.get("require_limits", True) and self._is_output_configured(var):
            raise AdapterError(f"output {var!r} has no safe limits declared")
        self._drv.write(var, value)

    def _is_output_configured(self, var: str) -> bool:
        return var in self.spec.get("limits", {}) or "min" in self.spec.get("limits", {})
