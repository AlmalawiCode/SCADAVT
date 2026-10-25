"""EPANET as a SCADAVT ``simulator`` adapter engine (water overlay).

This is the *only* place water lives: it registers an ``epanet`` engine with the
generic :class:`~scadavt.core.adapters.simulator.SimulatorAdapter`, so a config
can select it with ``{"kind": "simulator", "engine": "epanet",
"plugin": "scadavt.apps.water.epanet_adapter", "model": {"inp": "net.inp"}}``.
The generic PLC, CORE runtime and core adapter layer never import this module —
it is pulled in only by the adapter's ``plugin`` hook at construction time.

A measurement variable is ``"<element>.<property>"`` (e.g. ``"T1.level"``,
``"J1.pressure"``, ``"P1.flow"``); a control variable is a link property
(``"P1.status"`` for a pump, ``"V1.setting"`` for a valve). EPANET advances one
hydraulic step per co-sim ``tick`` so control actions affect later steps.
"""

from __future__ import annotations

from scadavt.core.adapters.simulator import SimulatorAdapter
from scadavt.protocols import wdsproto as wp

_PROPS = {
    "level": (wp.PT_NODE, wp.DT_LEVEL),
    "pressure": (wp.PT_NODE, wp.DT_PRESSURE),
    "demand": (wp.PT_NODE, wp.DT_DEMAND),
    "flow": (wp.PT_LINK, wp.DT_FLOW),
    "status": (wp.PT_LINK, wp.DT_STATUS),
    "setting": (wp.PT_LINK, wp.DT_SETTING),
}


class EpanetEngine:
    """Wraps :class:`scadavt.apps.water.wds.engine.WaterNetwork` behind the
    simulator-engine interface (``tick`` / ``read`` / ``write`` / ``close``)."""

    def __init__(self, model: dict):
        from .wds.engine import WaterNetwork
        inp = model.get("inp") or model.get("model") or model.get("path")
        if inp is None:
            raise ValueError("epanet engine needs model.inp = path to a .inp file")
        self.step = float(model.get("step", 300))
        self.net = WaterNetwork(inp, hyd_step=int(self.step))

    @staticmethod
    def _parse(var: str):
        element, _, prop = var.partition(".")
        if prop not in _PROPS:
            raise KeyError(f"unknown EPANET property in {var!r} (use element.level/pressure/"
                           f"flow/status/setting)")
        pt, dt = _PROPS[prop]
        return element, pt, dt

    def tick(self) -> None:
        self.net.advance(self.step)              # one hydraulic step per co-sim cycle

    def read(self, var: str) -> float:
        element, pt, dt = self._parse(var)
        return float(self.net.get(element, pt, dt))

    def write(self, var: str, value: float) -> None:
        element, pt, dt = self._parse(var)
        self.net.set(element, pt, dt, float(value))

    def close(self) -> None:
        try:
            self.net.close()
        except Exception:
            pass


# register the engine with the generic simulator adapter
SimulatorAdapter.register_engine("epanet", EpanetEngine)
