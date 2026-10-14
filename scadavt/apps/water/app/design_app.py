"""A generic external process application for ANY drawn EPANET design.

Generalises ``water_app.WaterApp``: it builds an EPANET plant from a config's
``epanet`` design and exposes each mapped point over the public Integration API,
resolving the point to its EPANET element/quantity via the config ``mapping``.
This is the external program a CORE deployment (or the networked loopback run)
connects to; it holds no SCADA knowledge.
"""

from __future__ import annotations

import tempfile

from scadavt.builder import generate
from scadavt.core import integration as ig
from scadavt.protocols import wdsproto as wp

_Q = {
    "level": (wp.PT_NODE, wp.DT_LEVEL), "pressure": (wp.PT_NODE, wp.DT_PRESSURE),
    "head": (wp.PT_NODE, wp.DT_HEAD), "demand": (wp.PT_NODE, wp.DT_DEMAND),
    "flow": (wp.PT_LINK, wp.DT_FLOW), "status": (wp.PT_LINK, wp.DT_STATUS),
    "setting": (wp.PT_LINK, wp.DT_SETTING),
}


class DesignApp(ig.ExternalApp):
    features = {"step": True}

    def __init__(self, design: dict, mapping: list):
        from scadavt import scenario as sc
        from scadavt.wds.model import Plant
        self._tmp = tempfile.TemporaryDirectory(prefix="scadavt_design_app_")
        scada = generate.write_scenario(design, self._tmp.name)
        self.plant = Plant(sc.load(scada), seed=1)
        # point id -> (element, ptype, dtype, role)
        self.bind = {}
        for m in mapping:
            pt, dt = _Q.get(m.get("quantity", "level"), (wp.PT_NODE, wp.DT_LEVEL))
            self.bind[m["point"]] = (m["element"], pt, dt, m.get("role", ig.MEASUREMENT))

    def points(self):
        return [ig.Point(pid, b[3], wp.DATA_TYPE_NAMES.get(b[2], "number"))
                for pid, b in self.bind.items()]

    def acquire(self, point_id: str, role: str) -> float:
        el, pt, dt, _ = self.bind[point_id]
        return self.plant.net.get(el, pt, dt)

    def control(self, point_id: str, value: float) -> None:
        el, pt, dt, _ = self.bind[point_id]
        self.plant.net.set(el, wp.PT_LINK, dt, value)

    def clock(self) -> float:
        return float(self.plant.net.t)

    def step(self, seconds: float) -> float:
        self.plant.advance(seconds)
        return float(self.plant.net.t)

    def close(self):
        self.plant.close()
        self._tmp.cleanup()
